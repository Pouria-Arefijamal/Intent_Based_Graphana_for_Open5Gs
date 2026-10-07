"""Unit tests for IbgCollector with a fake Docker client (no Docker, no network)."""
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exporter"))

import ibg_exporter  # noqa: E402
from ibg_exporter import IbgCollector, build_registry  # noqa: E402

Res = namedtuple("Res", "exit_code output")

NET_UPF = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  eth0: 1000 10 0 1 0 0 0 0 2000 20 0 2 0 0 0 0
ogstun: 3000 30 0 3 0 0 0 0 4000 40 0 4 0 0 0 0
"""
NET_UE_NO_TUN = """\
Inter-|   Receive |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
  eth0: 10 1 0 0 0 0 0 0 20 2 0 0 0 0 0 0
"""
NET_UE_TUN = NET_UE_NO_TUN + "uesimtun0: 500 5 0 0 0 0 0 0 600 6 0 0 0 0 0 0\n"
NET_APP = """\
Inter-|   Receive |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
  eth0: 7000 70 0 0 0 0 0 0 8000 80 0 0 0 0 0 0
"""
TC_TBF = """\
qdisc tbf 1: root refcnt 2 rate 10Mbit burst 32Kb lat 400ms
 Sent 5000 bytes 50 pkt (dropped 4, overlimits 9 requeues 0)
 backlog 3000b 2p requeues 0
"""
TC_UPF = """\
qdisc fq_codel 0: root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms
 Sent 9000 bytes 90 pkt (dropped 7, overlimits 0 requeues 1)
 backlog 1514b 1p requeues 1
"""
PING_OK = "64 bytes from 172.30.0.99: icmp_seq=1 ttl=63 time=2.5 ms\n"


class FakeContainer:
    def __init__(self, service, status="running", execs=None, stats=None):
        self.labels = {"com.docker.compose.project": "ibg",
                       "com.docker.compose.service": service}
        self.status = status
        self.execs = execs or {}
        self.calls = 0
        self.stats_payload = stats

    def stats(self, stream=False, one_shot=False):
        assert stream is False and one_shot is True
        if isinstance(self.stats_payload, Exception):
            raise self.stats_payload
        return self.stats_payload

    def exec_run(self, cmd, demux=False):
        self.calls += 1
        assert demux is True
        r = self.execs.get(tuple(cmd))
        if isinstance(r, Exception):
            raise r
        if r is None:
            return Res(127, (None, b"not found"))
        code, out, err = r
        return Res(code, (out.encode() if out else None, err.encode() if err else None))


class FakeClient:
    def __init__(self, containers, fail=False):
        self._c = containers
        self.fail = fail
        self.list_calls = 0
        self.containers = self

    def list(self, all=False, filters=None):
        self.list_calls += 1
        if self.fail:
            raise RuntimeError("docker down")
        assert all is True
        assert filters == {"label": "com.docker.compose.project=ibg"}
        return list(self._c)


CAT = ("cat", "/proc/net/dev")
TC = ("tc", "-s", "qdisc", "show", "dev", "eth0")
PING = ("ping", "-I", "uesimtun0", "-c1", "-W1", "172.30.0.99")


def full_stack():
    return [
        FakeContainer("upf", execs={CAT: (0, NET_UPF, ""), TC: (0, TC_UPF, "")}),
        FakeContainer("amf"),
        FakeContainer("smf", status="exited"),
        FakeContainer("gnb", execs={CAT: (0, NET_APP, "")}),
        FakeContainer("ue", execs={CAT: (0, NET_UE_TUN, ""), PING: (0, PING_OK, "")}),
        FakeContainer("app-server", execs={CAT: (0, NET_APP, ""), TC: (0, TC_TBF, "")}),
    ]


def make(containers, **kw):
    client = FakeClient(containers, fail=kw.pop("fail", False))
    col = IbgCollector(client, sbi_probe=lambda s: s == "amf", **kw)
    return client, col


def samples(col):
    out = {}
    for fam in col.collect():
        for s in fam.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def get(smp, name, **labels):
    return smp.get((name, tuple(sorted(labels.items()))))


def names(smp):
    return {k[0] for k in smp}


def test_container_up_down_and_roles():
    _, col = make(full_stack())
    s = samples(col)
    assert get(s, "ibg_container_up", service="amf", role="core") == 1.0
    assert get(s, "ibg_container_up", service="smf", role="core") == 0.0   # exists but stopped
    assert get(s, "ibg_container_up", service="ue", role="ran") == 1.0
    assert get(s, "ibg_container_up", service="app-server", role="data") == 1.0
    # containers that do not exist are omitted, never 0
    assert get(s, "ibg_container_up", service="nrf", role="core") is None


def test_sbi_only_for_existing_containers():
    _, col = make(full_stack())
    s = samples(col)
    assert get(s, "ibg_nf_sbi_up", service="amf") == 1.0
    assert get(s, "ibg_nf_sbi_up", service="smf") == 0.0
    assert get(s, "ibg_nf_sbi_up", service="nrf") is None


def test_iface_counters():
    _, col = make(full_stack())
    s = samples(col)
    assert get(s, "ibg_iface_rx_bytes_total", service="upf", iface="ogstun") == 3000.0
    assert get(s, "ibg_iface_tx_bytes_total", service="upf", iface="ogstun") == 4000.0
    assert get(s, "ibg_iface_rx_packets_total", service="upf", iface="eth0") == 10.0
    assert get(s, "ibg_iface_tx_drop_total", service="upf", iface="eth0") == 2.0
    assert get(s, "ibg_iface_rx_drop_total", service="upf", iface="ogstun") == 3.0
    assert get(s, "ibg_iface_tx_bytes_total", service="ue", iface="uesimtun0") == 600.0
    assert get(s, "ibg_iface_rx_bytes_total", service="app-server", iface="eth0") == 7000.0
    assert get(s, "ibg_ue_session_up") == 1.0


def test_missing_iface_omitted_and_session_down():
    cs = full_stack()
    cs[4] = FakeContainer("ue", execs={CAT: (0, NET_UE_NO_TUN, ""), PING: (0, PING_OK, "")})
    _, col = make(cs)
    s = samples(col)
    assert get(s, "ibg_iface_rx_bytes_total", service="ue", iface="uesimtun0") is None
    assert get(s, "ibg_iface_rx_bytes_total", service="ue", iface="eth0") == 10.0
    assert get(s, "ibg_ue_session_up") == 0.0
    assert col.error_count("iface") == 0   # absence of a tunnel is not a failure


def test_qdisc_metrics():
    _, col = make(full_stack())
    s = samples(col)
    lab = dict(service="app-server", dev="eth0", kind="tbf")
    assert get(s, "ibg_qdisc_backlog_packets", **lab) == 2.0
    assert get(s, "ibg_qdisc_backlog_bytes", **lab) == 3000.0
    assert get(s, "ibg_qdisc_sent_bytes_total", **lab) == 5000.0
    assert get(s, "ibg_qdisc_drops_total", **lab) == 4.0
    assert get(s, "ibg_qdisc_overlimits_total", **lab) == 9.0
    upf = dict(service="upf", dev="eth0", kind="fq_codel")
    assert get(s, "ibg_qdisc_backlog_packets", **upf) == 1.0
    assert get(s, "ibg_qdisc_backlog_bytes", **upf) == 1514.0
    assert get(s, "ibg_qdisc_sent_bytes_total", **upf) == 9000.0
    assert get(s, "ibg_qdisc_drops_total", **upf) == 7.0
    assert get(s, "ibg_qdisc_overlimits_total", **upf) == 0.0


def test_one_qdisc_failure_omits_only_that_service():
    cs = full_stack()
    cs[0] = FakeContainer("upf", execs={CAT: (0, NET_UPF, ""), TC: (127, "", "tc: not found")})
    _, col = make(cs)
    s = samples(col)
    assert get(s, "ibg_qdisc_drops_total", service="upf", dev="eth0", kind="fq_codel") is None
    assert get(s, "ibg_qdisc_drops_total", service="app-server", dev="eth0", kind="tbf") == 4.0
    assert get(s, "ibg_exporter_errors_total", collector="qdisc") == 1.0


def test_tool_missing_omits_metric_and_counts_error():
    cs = full_stack()
    cs[5] = FakeContainer("app-server", execs={CAT: (0, NET_APP, ""),
                                               TC: (127, "", "tc: not found")})
    _, col = make(cs)
    s = samples(col)
    assert not any(k[0].startswith("ibg_qdisc_") and ("service", "app-server") in k[1] for k in s)
    assert get(s, "ibg_qdisc_drops_total", service="upf", dev="eth0", kind="fq_codel") == 7.0
    assert get(s, "ibg_exporter_errors_total", collector="qdisc") == 1.0
    assert get(s, "ibg_iface_rx_bytes_total", service="app-server", iface="eth0") == 7000.0


def test_exec_exception_counts_error_without_crash():
    cs = full_stack()
    cs[0] = FakeContainer("upf", execs={CAT: RuntimeError("api error")})
    _, col = make(cs)
    s = samples(col)
    assert get(s, "ibg_iface_rx_bytes_total", service="upf", iface="eth0") is None
    assert get(s, "ibg_exporter_errors_total", collector="iface") == 1.0
    assert get(s, "ibg_container_up", service="upf", role="core") == 1.0


def test_docker_unavailable_never_crashes_no_fake_zeros():
    _, col = make([], fail=True)
    s = samples(col)
    assert get(s, "ibg_exporter_errors_total", collector="container_up") == 1.0
    assert "ibg_container_up" not in names(s)
    assert not any(n.startswith("ibg_iface_") or n.startswith("ibg_qdisc_") for n in names(s))
    assert "ibg_exporter_scrape_seconds" in names(s)


def test_error_counter_increments_on_each_collection():
    t = [0.0]
    cs = full_stack()
    cs[5] = FakeContainer("app-server", execs={CAT: (0, NET_APP, "")})  # tc -> 127
    client = FakeClient(cs)
    col = IbgCollector(client, sbi_probe=lambda s: True, cache_ttl=2.0, clock=lambda: t[0])
    assert get(samples(col), "ibg_exporter_errors_total", collector="qdisc") == 1.0
    t[0] = 5.0
    assert get(samples(col), "ibg_exporter_errors_total", collector="qdisc") == 2.0


def test_cache_ttl():
    t = [100.0]
    client = FakeClient(full_stack())
    col = IbgCollector(client, sbi_probe=lambda s: True, cache_ttl=2.0, clock=lambda: t[0])
    col.collect(); col.collect()
    assert client.list_calls == 1
    t[0] += 1.9
    col.collect()
    assert client.list_calls == 1
    t[0] += 0.2
    col.collect()
    assert client.list_calls == 2


def test_path_probe_up_down_unknown():
    _, col = make(full_stack())
    s = samples(col)
    assert "ibg_path_up" not in names(s)          # not probed yet -> omitted
    col.refresh_path()
    col._cache = None
    s = samples(col)
    assert get(s, "ibg_path_up") == 1.0
    assert get(s, "ibg_path_rtt_ms") == 2.5

    cs = full_stack()
    cs[4] = FakeContainer("ue", execs={CAT: (0, NET_UE_TUN, ""), PING: (1, "100% packet loss", "")})
    _, col = make(cs)
    col.refresh_path()
    s = samples(col)
    assert get(s, "ibg_path_up") == 0.0
    assert "ibg_path_rtt_ms" not in names(s)       # omitted when down

    cs = full_stack()
    cs[4] = FakeContainer("ue", execs={CAT: (0, NET_UE_TUN, ""), PING: (127, "", "ping: not found")})
    _, col = make(cs)
    col.refresh_path()
    s = samples(col)
    assert "ibg_path_up" not in names(s)
    assert get(s, "ibg_exporter_errors_total", collector="path") == 1.0


def test_registry_text_exposition():
    from prometheus_client import generate_latest
    _, col = make(full_stack())
    text = generate_latest(build_registry(col)).decode()
    assert 'ibg_container_up{role="core",service="amf"} 1.0' in text
    assert "# TYPE ibg_iface_rx_bytes_total counter" in text
    assert "# TYPE ibg_qdisc_backlog_bytes gauge" in text
    assert 'ibg_exporter_errors_total{collector="qdisc"} 0.0' in text


# ---- Docker stats: cpu seconds + working-set memory --------------------------
STATS_V2 = {
    "cpu_stats": {"cpu_usage": {"total_usage": 2_500_000_000}},
    "memory_stats": {"usage": 100_000_000, "stats": {"anon": 1, "inactive_file": 30_000_000}},
}
STATS_V1 = {
    "cpu_stats": {"cpu_usage": {"total_usage": 4_000_000_000}},
    "memory_stats": {"usage": 50_000_000, "stats": {"total_inactive_file": 10_000_000, "cache": 20_000_000}},
}


def test_parse_container_stats_cgroup_v2_and_v1():
    assert ibg_exporter.parse_container_stats(STATS_V2) == (2.5, 70_000_000.0)
    assert ibg_exporter.parse_container_stats(STATS_V1) == (4.0, 40_000_000.0)
    # no cache info at all: plain usage
    s = {"cpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}}, "memory_stats": {"usage": 5}}
    assert ibg_exporter.parse_container_stats(s) == (1.0, 5.0)
    # missing fields are None, never 0
    assert ibg_exporter.parse_container_stats({}) == (None, None)
    assert ibg_exporter.parse_container_stats({"cpu_stats": {"cpu_usage": {"total_usage": 0}}}) == (None, None)


def test_container_cpu_and_memory_metrics():
    cs = [FakeContainer("upf", stats=STATS_V2), FakeContainer("amf", stats=STATS_V1),
          FakeContainer("smf", status="exited", stats=STATS_V2)]
    _, col = make(cs)
    s = samples(col)
    assert "ibg_container_cpu_seconds_total" not in names(s)   # not collected yet -> omitted
    col.refresh_stats()
    col._cache = None
    s = samples(col)
    assert get(s, "ibg_container_cpu_seconds_total", service="upf") == 2.5
    assert get(s, "ibg_container_memory_bytes", service="upf") == 70_000_000.0
    assert get(s, "ibg_container_cpu_seconds_total", service="amf") == 4.0
    assert get(s, "ibg_container_memory_bytes", service="amf") == 40_000_000.0
    assert get(s, "ibg_container_cpu_seconds_total", service="smf") is None   # stopped
    assert get(s, "ibg_exporter_errors_total", collector="stats") == 0.0


def test_stats_failure_omits_service_and_counts_error():
    cs = [FakeContainer("upf", stats=STATS_V2), FakeContainer("amf", stats=RuntimeError("boom")),
          FakeContainer("smf", stats={})]
    _, col = make(cs)
    col.refresh_stats()
    s = samples(col)
    assert get(s, "ibg_container_cpu_seconds_total", service="upf") == 2.5
    assert get(s, "ibg_container_cpu_seconds_total", service="amf") is None
    assert get(s, "ibg_container_memory_bytes", service="amf") is None
    assert get(s, "ibg_container_cpu_seconds_total", service="smf") is None
    assert get(s, "ibg_exporter_errors_total", collector="stats") == 2.0


def test_stats_docker_down_clears_values():
    client, col = make([FakeContainer("upf", stats=STATS_V2)])
    col.refresh_stats()
    client.fail = True
    col.refresh_stats()
    col._cache = None
    s = samples(col)
    assert "ibg_container_cpu_seconds_total" not in names(s)
    assert get(s, "ibg_exporter_errors_total", collector="stats") == 1.0
