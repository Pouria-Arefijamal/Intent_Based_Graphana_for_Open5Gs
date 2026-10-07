"""Intent-Based Grafana custom Prometheus exporter (SPEC section 3).

Values are computed at scrape time by a custom ``Collector`` and cached for
``CACHE_TTL`` seconds so that several scrapers never multiply the docker-exec
load. A metric whose source cannot be read is omitted (never emitted as 0) and
``ibg_exporter_errors_total{collector=...}`` is incremented instead.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from ibg_parsers import parse_ping_rtt_ms, parse_proc_net_dev, parse_tc_qdisc

log = logging.getLogger("ibg_exporter")

CACHE_TTL = 2.0          # seconds a collection result is reused
PATH_INTERVAL = 10.0     # seconds between ping probes
STATS_INTERVAL = 5.0     # seconds between Docker stats collections
SBI_TIMEOUT = 0.5        # seconds for a TCP connect to <service>:7777
SBI_PORT = 7777

# service -> role label (SPEC section 1)
SERVICE_ROLES: Dict[str, str] = {
    **{s: "core" for s in (
        "mongo", "smf", "upf", "amf", "ausf", "nrf", "udm", "udr", "pcf",
        "nssf", "bsf", "scp", "webui")},
    "gnb": "ran",
    "ue": "ran",
    "app-server": "data",
    **{s: "monitoring" for s in (
        "prometheus", "grafana", "cadvisor", "node-exporter", "exporter",
        "intent-engine")},
}

SBI_SERVICES = ("nrf", "scp", "ausf", "udr", "udm", "pcf", "nssf", "bsf", "amf", "smf")

# service -> interfaces to export from /proc/net/dev
IFACE_TARGETS: Dict[str, Tuple[str, ...]] = {
    "upf": ("eth0", "ogstun"),
    "gnb": ("eth0",),
    "ue": ("eth0", "uesimtun0"),
    "app-server": ("eth0",),
}

QDISC_SERVICES = ("upf", "app-server")   # N6 egress of the UPF + the server's own eth0
QDISC_DEV = "eth0"
UE_SERVICE = "ue"
UE_TUNNEL = "uesimtun0"

ERROR_COLLECTORS = ("container_up", "sbi", "iface", "qdisc", "path", "session", "stats")

# (metric name without _total, /proc/net/dev key, help)
IFACE_COUNTERS = (
    ("ibg_iface_rx_bytes", "rx_bytes", "Bytes received on the interface"),
    ("ibg_iface_tx_bytes", "tx_bytes", "Bytes transmitted on the interface"),
    ("ibg_iface_rx_packets", "rx_packets", "Packets received on the interface"),
    ("ibg_iface_tx_packets", "tx_packets", "Packets transmitted on the interface"),
    ("ibg_iface_rx_drop", "rx_drop", "Received packets dropped on the interface"),
    ("ibg_iface_tx_drop", "tx_drop", "Transmitted packets dropped on the interface"),
)


def parse_container_stats(stats: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Docker stats payload -> (cpu_seconds_total, working_set_bytes); None = unreadable.

    CPU: ``cpu_stats.cpu_usage.total_usage`` (nanoseconds) / 1e9.
    Memory: ``memory_stats.usage`` minus the reclaimable page cache, which is
    ``stats.inactive_file`` on cgroup v2 and ``stats.total_inactive_file`` (or
    ``cache``) on cgroup v1 -- the same definition ``docker stats`` uses.
    """
    cpu: Optional[float] = None
    mem: Optional[float] = None
    total = ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
    if isinstance(total, (int, float)) and total > 0:
        cpu = total / 1e9
    mstats = stats.get("memory_stats") or {}
    usage = mstats.get("usage")
    if isinstance(usage, (int, float)) and usage > 0:
        inner = mstats.get("stats") or {}
        cache = 0
        for key in ("inactive_file", "total_inactive_file", "cache"):
            if isinstance(inner.get(key), (int, float)):
                cache = inner[key]
                break
        mem = float(max(usage - cache, 0))
    return cpu, mem


class ExecError(Exception):
    """A docker exec could not be run at all (container gone, API error...)."""


class IbgCollector:
    """prometheus_client custom collector, see module docstring."""

    def __init__(
        self,
        client: Any,
        project: str = "ibg",
        app_server_ip: str = "172.30.0.99",
        cache_ttl: float = CACHE_TTL,
        clock: Callable[[], float] = time.monotonic,
        sbi_probe: Optional[Callable[[str], Optional[bool]]] = None,
    ) -> None:
        self.client = client
        self.project = project
        self.app_server_ip = app_server_ip
        self.cache_ttl = cache_ttl
        self._clock = clock
        self._sbi_probe = sbi_probe or self._tcp_probe
        self._lock = threading.Lock()
        self._err_lock = threading.Lock()
        self._errors: Dict[str, int] = {name: 0 for name in ERROR_COLLECTORS}
        self._cache: Optional[List[Any]] = None
        self._cache_at = 0.0
        self._path_lock = threading.Lock()
        self._path_up: Optional[bool] = None
        self._path_rtt: Optional[float] = None
        self._stats_lock = threading.Lock()
        self._stats: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ errors
    def _bump(self, collector: str) -> None:
        with self._err_lock:
            self._errors[collector] = self._errors.get(collector, 0) + 1

    def error_count(self, collector: str) -> int:
        with self._err_lock:
            return self._errors.get(collector, 0)

    # ------------------------------------------------------------------ docker
    def _containers(self) -> Dict[str, Any]:
        """service -> container, preferring a running container per service."""
        found = self.client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={self.project}"},
        )
        by_service: Dict[str, Any] = {}
        for c in found:
            labels = c.labels or {}
            service = labels.get("com.docker.compose.service")
            if not service:
                continue
            prev = by_service.get(service)
            if prev is None or (prev.status != "running" and c.status == "running"):
                by_service[service] = c
        return by_service

    @staticmethod
    def _exec(container: Any, cmd: List[str]) -> Tuple[int, str, str]:
        """Run ``cmd`` in the container -> (exit_code, stdout, stderr)."""
        try:
            res = container.exec_run(cmd, demux=True)
        except Exception as exc:  # docker.errors.APIError, requests errors...
            raise ExecError(str(exc)) from exc
        code = res[0]
        out = res[1]
        if isinstance(out, tuple):
            stdout, stderr = out
        else:  # demux ignored by a fake/old client
            stdout, stderr = out, None
        dec = lambda b: b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else (b or "")
        return (code if code is not None else -1), dec(stdout), dec(stderr)

    # ---------------------------------------------------------------- sbi probe
    def _tcp_probe(self, service: str) -> Optional[bool]:
        """True/False = connect result; None = name unresolvable (omit)."""
        try:
            with socket.create_connection((service, SBI_PORT), timeout=SBI_TIMEOUT):
                return True
        except socket.gaierror:
            return None
        except OSError:
            return False

    # -------------------------------------------------------------------- path
    def refresh_path(self) -> None:
        """Run the UE->app-server ping and store the result (10 s thread)."""
        up: Optional[bool] = None
        rtt: Optional[float] = None
        try:
            ue = self._containers().get(UE_SERVICE)
            if ue is not None and ue.status == "running":
                code, out, err = self._exec(
                    ue, ["ping", "-I", UE_TUNNEL, "-c1", "-W1", self.app_server_ip])
                if code == 0:
                    up, rtt = True, parse_ping_rtt_ms(out)
                elif code in (1, 2):      # no reply / tunnel device missing
                    up = False
                else:                      # 126/127: ping not usable -> unknown
                    raise ExecError(f"ping exit {code}: {err.strip()[:120]}")
        except Exception as exc:
            log.warning("path probe failed: %s", exc)
            self._bump("path")
            up, rtt = None, None
        with self._path_lock:
            self._path_up, self._path_rtt = up, rtt

    # ------------------------------------------------------------------- stats
    def _one_stats(self, service: str, container: Any) -> Tuple[str, Optional[Tuple[Optional[float], Optional[float]]]]:
        try:
            payload = container.stats(stream=False, one_shot=True)
            cpu, mem = parse_container_stats(payload)
            if cpu is None and mem is None:
                raise ValueError("no cpu/memory fields in stats payload")
            return service, (cpu, mem)
        except Exception as exc:
            log.warning("stats for %s failed: %s", service, exc)
            self._bump("stats")
            return service, None

    def refresh_stats(self) -> None:
        """Collect CPU/memory of every running container (parallel, one_shot)."""
        try:
            running = {s: c for s, c in self._containers().items() if c.status == "running"}
        except Exception as exc:
            log.warning("stats: container listing failed: %s", exc)
            self._bump("stats")
            with self._stats_lock:
                self._stats = {}
            return
        results: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        if running:
            with ThreadPoolExecutor(max_workers=min(len(running), 16)) as pool:
                futures = [pool.submit(self._one_stats, s, c) for s, c in running.items()]
                for fut in futures:
                    service, val = fut.result()
                    if val is not None:
                        results[service] = val
        with self._stats_lock:
            self._stats = results

    def _background_loop(self) -> None:
        last_stats = last_path = float("-inf")
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_path >= PATH_INTERVAL:
                self.refresh_path()
                last_path = time.monotonic()
            if now - last_stats >= STATS_INTERVAL:
                self.refresh_stats()
                last_stats = time.monotonic()
            self._stop.wait(1.0)

    def start_background(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._background_loop, name="background", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -------------------------------------------------------------- collection
    def _gather(self) -> List[Any]:
        started = time.monotonic()
        families: List[Any] = []

        # --- containers -----------------------------------------------------
        containers: Dict[str, Any] = {}
        listed = True
        try:
            containers = self._containers()
        except Exception as exc:
            listed = False
            log.warning("container listing failed: %s", exc)
            self._bump("container_up")

        if listed:
            up_fam = GaugeMetricFamily(
                "ibg_container_up", "1 if the container is running, else 0",
                labels=["service", "role"])
            for service in sorted(containers):
                running = containers[service].status == "running"
                up_fam.add_metric([service, SERVICE_ROLES.get(service, "other")],
                                  1.0 if running else 0.0)
            if up_fam.samples:
                families.append(up_fam)

        # --- SBI TCP probes -------------------------------------------------
        try:
            targets = [s for s in SBI_SERVICES if (s in containers or not listed)]
            sbi_fam = GaugeMetricFamily(
                "ibg_nf_sbi_up", "1 if a TCP connect to <service>:7777 succeeds",
                labels=["service"])
            if targets:
                with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                    results = list(pool.map(self._sbi_probe, targets))
                for service, ok in zip(targets, results):
                    if ok is not None:
                        sbi_fam.add_metric([service], 1.0 if ok else 0.0)
            if sbi_fam.samples:
                families.append(sbi_fam)
        except Exception as exc:
            log.warning("sbi probes failed: %s", exc)
            self._bump("sbi")

        # --- interface counters --------------------------------------------
        iface_fams = {
            name: CounterMetricFamily(name, doc, labels=["service", "iface"])
            for name, _, doc in IFACE_COUNTERS
        }
        ue_session: Optional[bool] = None
        for service, ifaces in IFACE_TARGETS.items():
            container = containers.get(service)
            if container is None or container.status != "running":
                continue
            try:
                code, out, err = self._exec(container, ["cat", "/proc/net/dev"])
                if code != 0:
                    raise ExecError(f"exit {code}: {err.strip()[:120]}")
                parsed = parse_proc_net_dev(out)
                if not parsed:
                    raise ExecError("empty /proc/net/dev")
            except Exception as exc:
                log.warning("iface collection for %s failed: %s", service, exc)
                self._bump("iface")
                continue
            if service == UE_SERVICE:
                ue_session = UE_TUNNEL in parsed
            for iface in ifaces:
                stats = parsed.get(iface)
                if stats is None:
                    continue  # interface absent: omit, do not invent zeros
                for name, key, _ in IFACE_COUNTERS:
                    iface_fams[name].add_metric([service, iface], float(stats[key]))
        families.extend(f for f in iface_fams.values() if f.samples)

        # --- qdisc ----------------------------------------------------------
        for service in QDISC_SERVICES:
            families.extend(self._collect_qdisc(service, containers.get(service)))
        families = self._merge_families(families)

        # --- path / session -------------------------------------------------
        with self._path_lock:
            path_up, path_rtt = self._path_up, self._path_rtt
        if path_up is not None:
            fam = GaugeMetricFamily(
                "ibg_path_up", f"1 if ping from the UE tunnel to {self.app_server_ip} succeeds")
            fam.add_metric([], 1.0 if path_up else 0.0)
            families.append(fam)
            if path_up and path_rtt is not None:
                fam = GaugeMetricFamily("ibg_path_rtt_ms", "RTT of the last UE->app-server ping in ms")
                fam.add_metric([], path_rtt)
                families.append(fam)
        if ue_session is not None:
            fam = GaugeMetricFamily("ibg_ue_session_up", "1 if uesimtun0 exists in the ue container")
            fam.add_metric([], 1.0 if ue_session else 0.0)
            families.append(fam)

        # --- per-container CPU / memory (from the background stats thread) ---
        with self._stats_lock:
            stats = dict(self._stats)
        cpu_fam = CounterMetricFamily(
            "ibg_container_cpu_seconds", "Cumulative CPU time used by the container (Docker stats API)",
            labels=["service"])
        mem_fam = GaugeMetricFamily(
            "ibg_container_memory_bytes", "Working-set memory of the container (usage minus inactive file cache)",
            labels=["service"])
        for service in sorted(stats):
            cpu, mem = stats[service]
            if cpu is not None:
                cpu_fam.add_metric([service], cpu)
            if mem is not None:
                mem_fam.add_metric([service], mem)
        families.extend(f for f in (cpu_fam, mem_fam) if f.samples)

        # --- self metrics ---------------------------------------------------
        dur = GaugeMetricFamily("ibg_exporter_scrape_seconds", "Duration of the last collection")
        dur.add_metric([], time.monotonic() - started)
        families.append(dur)
        errs = CounterMetricFamily(
            "ibg_exporter_errors", "Collector failures", labels=["collector"])
        with self._err_lock:
            for name, value in sorted(self._errors.items()):
                errs.add_metric([name], float(value))
        families.append(errs)
        return families

    @staticmethod
    def _merge_families(families: List[Any]) -> List[Any]:
        """Merge same-named families (one per qdisc service) into a single family."""
        merged: Dict[str, Any] = {}
        order: List[Any] = []
        for fam in families:
            first = merged.get(fam.name)
            if first is None:
                merged[fam.name] = fam
                order.append(fam)
            else:
                first.samples.extend(fam.samples)
        return order

    def _collect_qdisc(self, service: str, container: Any) -> List[Any]:
        if container is None or container.status != "running":
            return []
        try:
            code, out, err = self._exec(container, ["tc", "-s", "qdisc", "show", "dev", QDISC_DEV])
            if code != 0:
                raise ExecError(f"exit {code}: {err.strip()[:120]}")
            qdiscs = parse_tc_qdisc(out)
            if not qdiscs:
                raise ExecError("no qdisc in tc output")
        except Exception as exc:
            log.warning("qdisc collection for %s failed: %s", service, exc)
            self._bump("qdisc")
            return []

        labels = ["service", "dev", "kind"]
        gauge_defs = (
            ("ibg_qdisc_backlog_packets", "Packets queued in the qdisc", "backlog_packets"),
            ("ibg_qdisc_backlog_bytes", "Bytes queued in the qdisc", "backlog_bytes"),
        )
        counter_defs = (
            ("ibg_qdisc_sent_bytes", "Bytes sent by the qdisc", "sent_bytes"),
            ("ibg_qdisc_drops", "Packets dropped by the qdisc", "dropped"),
            ("ibg_qdisc_overlimits", "Qdisc overlimit events", "overlimits"),
        )
        # Several qdiscs of the same kind on one dev (rare) are summed so the
        # label set stays unique; None fields are omitted, never zero-filled.
        agg: Dict[str, Dict[str, float]] = {}
        for q in qdiscs:
            slot = agg.setdefault(q.kind, {})
            for _, _, attr in gauge_defs + counter_defs:
                v = getattr(q, attr)
                if v is not None:
                    slot[attr] = slot.get(attr, 0) + v
        fams: List[Any] = []
        for name, doc, attr in gauge_defs:
            fam = GaugeMetricFamily(name, doc, labels=labels)
            for kind in sorted(agg):
                if attr in agg[kind]:
                    fam.add_metric([service, QDISC_DEV, kind], float(agg[kind][attr]))
            if fam.samples:
                fams.append(fam)
        for name, doc, attr in counter_defs:
            fam = CounterMetricFamily(name, doc, labels=labels)
            for kind in sorted(agg):
                if attr in agg[kind]:
                    fam.add_metric([service, QDISC_DEV, kind], float(agg[kind][attr]))
            if fam.samples:
                fams.append(fam)
        return fams

    def collect(self) -> Iterable[Any]:
        with self._lock:
            now = self._clock()
            if self._cache is None or now - self._cache_at >= self.cache_ttl:
                try:
                    self._cache = self._gather()
                except Exception:  # last-resort guard: a scrape must never crash
                    log.exception("collection crashed")
                    self._bump("container_up")
                    self._cache = self._cache or []
                self._cache_at = self._clock()
            return list(self._cache)


def build_registry(collector: IbgCollector) -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(collector)
    return registry


def main() -> None:
    import docker  # imported lazily so unit tests need no Docker SDK

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("EXPORTER_PORT", "9200"))
    project = os.environ.get("COMPOSE_PROJECT", "ibg")
    app_ip = os.environ.get("APP_SERVER_IP", "172.30.0.99")
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=5)
    collector = IbgCollector(client, project=project, app_server_ip=app_ip)
    collector.start_background()
    start_http_server(port, registry=build_registry(collector))
    log.info("ibg_exporter listening on :%d (project=%s)", port, project)
    threading.Event().wait()


if __name__ == "__main__":
    main()
