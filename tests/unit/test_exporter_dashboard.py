"""Tests for grafana/build_dashboard.py (stdlib only, no network)."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "grafana" / "build_dashboard.py"
COMMITTED = ROOT / "grafana" / "dashboards" / "open5gs_observatory.json"

spec = importlib.util.spec_from_file_location("ibg_build_dashboard", SCRIPT)
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

# SPEC section 3 + section 4 metrics
SPEC_METRICS = {
    "ibg_container_up", "ibg_nf_sbi_up",
    "ibg_iface_rx_bytes_total", "ibg_iface_tx_bytes_total",
    "ibg_iface_rx_packets_total", "ibg_iface_tx_packets_total",
    "ibg_iface_rx_drop_total", "ibg_iface_tx_drop_total",
    "ibg_qdisc_backlog_packets", "ibg_qdisc_backlog_bytes", "ibg_qdisc_sent_bytes_total",
    "ibg_qdisc_drops_total", "ibg_qdisc_overlimits_total",
    "ibg_path_up", "ibg_path_rtt_ms", "ibg_ue_session_up",
    "ibg_exporter_scrape_seconds", "ibg_exporter_errors_total",
    "ibg_container_cpu_seconds_total", "ibg_container_memory_bytes",
    "fivegs_amffunction_rm_registeredsubnbr", "amf_session",
    "pfcp_sessions_active", "ues_active", "bearers_active", "fivegs_upffunction_upf_sessionnbr", "gnb",
}
# Native Open5GS v2.8.0 counters (AMF/SMF/UPF) used on the "Core native metrics" row
OPEN5GS_NATIVE = {
    "fivegs_amffunction_rm_reginitreq", "fivegs_amffunction_rm_reginitsucc",
}
KNOWN_STANDARD = {
    "up",
    "node_cpu_seconds_total", "node_memory_MemAvailable_bytes", "node_memory_MemTotal_bytes",
    "node_load1", "node_network_receive_bytes_total", "node_network_transmit_bytes_total",
}
ALLOWED = SPEC_METRICS | OPEN5GS_NATIVE | KNOWN_STANDARD
PROMQL_WORDS = {"rate", "sum", "avg", "by", "on", "without", "ignoring", "and", "or", "unless", "bool"}


def metric_names(expr):
    expr = re.sub(r'"[^"]*"', "", expr)            # string literals
    expr = re.sub(r"\{[^}]*\}", "", expr)           # label matchers
    expr = re.sub(r"\[[^\]]*\]", "", expr)          # range selectors
    expr = re.sub(r"\b(by|without|on|ignoring)\s*\([^)]*\)", " ", expr)
    idents = re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", expr)
    return {i for i in idents if i not in PROMQL_WORDS}


def all_panels(d):
    return [p for p in d["panels"] if p["type"] not in ("row", "text")]  # data panels only


def test_deterministic():
    assert bd.render() == bd.render()


def test_committed_file_is_up_to_date_and_valid_json():
    on_disk = COMMITTED.read_text(encoding="utf-8")
    assert json.loads(on_disk)
    assert on_disk == bd.render(), "run: python3 grafana/build_dashboard.py"


def test_cli_writes_identical_output(tmp_path):
    out = tmp_path / "d.json"
    subprocess.run([sys.executable, str(SCRIPT), str(out)], check=True, capture_output=True)
    assert out.read_text(encoding="utf-8") == bd.render()


def test_header_fields():
    d = json.loads(bd.render())
    assert d["schemaVersion"] == 39
    assert d["uid"] == "ibg-observatory"
    assert d["title"] == "Open5GS Observatory"
    assert d["refresh"] == "5s"
    assert d["time"] == {"from": "now-15m", "to": "now"}
    assert d["tags"] == ["ibg", "open5gs"]


def test_rows_and_unique_ids():
    d = json.loads(bd.render())
    rows = [p["title"] for p in d["panels"] if p["type"] == "row"]
    assert rows == ["Ask the network (Intent Console)", "Overview", "User plane in vs out", "Core native metrics",
                    "Containers", "N6 qdisc", "Host"]
    ids = [p["id"] for p in d["panels"]]
    assert len(ids) == len(set(ids))


def test_every_panel_has_datasource_description_unit_and_queries():
    d = json.loads(bd.render())
    for p in all_panels(d):
        assert p["datasource"] == {"type": "prometheus", "uid": "ibg-prometheus"}, p["title"]
        assert isinstance(p["description"], str) and len(p["description"]) > 30, p["title"]
        assert p["title"].strip()
        assert "unit" in p["fieldConfig"]["defaults"], p["title"]
        assert p["targets"], p["title"]
        for t in p["targets"]:
            assert t["datasource"] == p["datasource"]
            assert t["expr"].strip()
        pos = p["gridPos"]
        assert 0 <= pos["x"] and pos["x"] + pos["w"] <= 24


def test_no_overlapping_panels():
    d = json.loads(bd.render())
    cells = set()
    for p in d["panels"]:
        g = p["gridPos"]
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                assert (x, y) not in cells, p["title"]
                cells.add((x, y))


def test_promql_metrics_are_known():
    d = json.loads(bd.render())
    used = set()
    for p in all_panels(d):
        for t in p["targets"]:
            names = metric_names(t["expr"])
            assert names, t["expr"]
            unknown = names - ALLOWED
            assert not unknown, f"{p['title']}: unknown metrics {unknown}"
            used |= names
    assert used & SPEC_METRICS


def test_required_panels_and_units():
    d = json.loads(bd.render())
    by_title = {p["title"]: p for p in all_panels(d)}
    ul = by_title["User plane in vs out - uplink"]
    dl = by_title["User plane in vs out - downlink"]
    for p in (ul, dl):
        assert len(p["targets"]) == 3
        assert p["fieldConfig"]["defaults"]["unit"] == "bps"
    assert 'service="ue"' in ul["targets"][0]["expr"] and "tx_bytes" in ul["targets"][0]["expr"]
    assert 'iface="ogstun"' in ul["targets"][1]["expr"] and "rx_bytes" in ul["targets"][1]["expr"]
    assert 'service="app-server"' in ul["targets"][2]["expr"]
    ratio = by_title["Delivery ratio (end-to-end)"]
    assert ratio["fieldConfig"]["defaults"]["unit"] == "percent"
    assert ratio["fieldConfig"]["defaults"]["thresholds"]["steps"][0]["color"] == "red"
    assert len(ratio["targets"]) == 2
    nf = by_title["Network function up grid"]
    assert nf["type"] == "stat" and nf["targets"][0]["expr"] == "ibg_container_up"
    mapping = nf["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mapping["1"]["color"] == "green" and mapping["0"]["color"] == "red"
    assert by_title["CPU per container"]["targets"][0]["expr"] == "rate(ibg_container_cpu_seconds_total[30s])*100"
    assert by_title["Memory per container"]["targets"][0]["expr"] == "ibg_container_memory_bytes"
    assert by_title["UPF CPU"]["targets"][0]["expr"] == 'rate(ibg_container_cpu_seconds_total{service="upf"}[30s])*100'
    assert by_title["CPU per container"]["fieldConfig"]["defaults"]["unit"] == "percent"
    assert by_title["Memory per container"]["fieldConfig"]["defaults"]["unit"] == "bytes"
    for t in ("Registered UEs", "PDU sessions (AMF / SMF / UPF)", "Connected gNBs",
              "UE data path up", "UE -> server RTT", "N6 qdisc backlog (packets)",
              "N6 qdisc drops", "Host CPU", "Host memory used"):
        assert t in by_title, t
    assert by_title["UE -> server RTT"]["fieldConfig"]["defaults"]["unit"] == "ms"


def test_no_cadvisor_name_queries():
    text = bd.render()
    assert "container_cpu_usage" not in text and "container_memory_working" not in text
    assert "container_network" not in text and 'name=~"ibg' not in text


def test_provisioning_files_match_spec():
    ds = (ROOT / "grafana/provisioning/datasources/datasource.yml").read_text()
    assert "uid: ibg-prometheus" in ds and "isDefault: true" in ds
    assert "url: http://prometheus:9090" in ds
    dp = (ROOT / "grafana/provisioning/dashboards/dashboards.yml").read_text()
    assert "/var/lib/grafana/dashboards" in dp and "allowUiUpdates: true" in dp


def test_prometheus_config_jobs():
    text = (ROOT / "prometheus/prometheus.yml").read_text()
    for needle in ("scrape_interval: 5s", "evaluation_interval: 5s", "amf:9091", "smf:9091",
                   "upf:9091", "pcf:9091", "cadvisor:8080", "node-exporter:9100",
                   "exporter:9200", "intent-engine:8088", "localhost:9090"):
        assert needle in text, needle
    for job in ("open5gs", "cadvisor", "node", "ibg_exporter", "intent_engine", "prometheus"):
        assert f"job_name: {job}" in text


def test_chat_panel_embeds_the_intent_console_and_follows_the_url():
    d = json.loads(bd.render("http://myhost:8123/"))
    chat = [p for p in d["panels"] if p["type"] == "text"]
    assert len(chat) == 1
    html = chat[0]["options"]["content"]
    assert chat[0]["options"]["mode"] == "html"
    assert 'src="http://myhost:8123/?embed=1"' in html and "<iframe" in html
    assert len(chat[0]["description"]) > 30
    # the default build points at the default port
    default = [p for p in json.loads(bd.render())["panels"] if p["type"] == "text"][0]
    assert 'src="http://localhost:8088/?embed=1"' in default["options"]["content"]
