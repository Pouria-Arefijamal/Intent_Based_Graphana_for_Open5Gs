#!/usr/bin/env python3
"""Generate grafana/dashboards/open5gs_observatory.json (stdlib only, deterministic).

Every query is one of the PromQL recipes of docs/SPEC.md section 4 (or a
node_exporter standard metric), so the dashboard and the intent
engine always agree on what a number means.

Usage: python3 grafana/build_dashboard.py [output_path]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DS = {"type": "prometheus", "uid": "ibg-prometheus"}
OUT_DEFAULT = Path(__file__).resolve().parent / "dashboards" / "open5gs_observatory.json"

# ---- SPEC section 4 recipes (rate window 30s) --------------------------------
Q = {
    "ue_uplink_bps": 'rate(ibg_iface_tx_bytes_total{service="ue",iface="uesimtun0"}[30s])*8',
    "ue_downlink_bps": 'rate(ibg_iface_rx_bytes_total{service="ue",iface="uesimtun0"}[30s])*8',
    "upf_uplink_bps": 'rate(ibg_iface_rx_bytes_total{service="upf",iface="ogstun"}[30s])*8',
    "upf_downlink_bps": 'rate(ibg_iface_tx_bytes_total{service="upf",iface="ogstun"}[30s])*8',
    "server_rx_bps": 'rate(ibg_iface_rx_bytes_total{service="app-server",iface="eth0"}[30s])*8',
    "server_tx_bps": 'rate(ibg_iface_tx_bytes_total{service="app-server",iface="eth0"}[30s])*8',
    "gnb_rx_bps": 'rate(ibg_iface_rx_bytes_total{service="gnb",iface="eth0"}[30s])*8',
    "gnb_tx_bps": 'rate(ibg_iface_tx_bytes_total{service="gnb",iface="eth0"}[30s])*8',
    "upf_cpu_pct": 'rate(ibg_container_cpu_seconds_total{service="upf"}[30s])*100',
    "nf_cpu_pct": 'rate(ibg_container_cpu_seconds_total[30s])*100',
    "nf_mem_bytes": 'ibg_container_memory_bytes',
    "nf_up": 'ibg_container_up',
    "sbi_up": 'ibg_nf_sbi_up',
    "registered_ues": 'fivegs_amffunction_rm_registeredsubnbr',
    "pdu_sessions_amf": 'amf_session',
    "pdu_sessions_smf": 'pfcp_sessions_active',
    "pdu_sessions_upf": 'fivegs_upffunction_upf_sessionnbr',
    "connected_gnbs": 'gnb',
    "qdisc_backlog_pkts": 'ibg_qdisc_backlog_packets',
    "qdisc_drops_per_s": 'rate(ibg_qdisc_drops_total[30s])',
    "path_up": 'ibg_path_up',
    "path_rtt_ms": 'ibg_path_rtt_ms',
    "upf_ogstun_drops": ('rate(ibg_iface_rx_drop_total{service="upf",iface="ogstun"}[30s])'
                         '+rate(ibg_iface_tx_drop_total{service="upf",iface="ogstun"}[30s])'),
}

# ---- Extra expressions built from the recipes / standard exporter metrics -----
# Delivery ratio: the denominator is filtered (> 1 kbps) so that an idle path
# shows "no data" instead of a meaningless NaN/Inf ratio.
RATIO_UL = f'{Q["server_rx_bps"]} / on() ({Q["ue_uplink_bps"]} > 1000) * 100'
RATIO_DL = f'{Q["ue_downlink_bps"]} / on() ({Q["server_tx_bps"]} > 1000) * 100'
# Per-NF network: the exporter measures eth0 of upf / gnb / ue / app-server only.
CONTAINER_NET_RX = 'rate(ibg_iface_rx_bytes_total{iface="eth0"}[30s])*8'
CONTAINER_NET_TX = 'rate(ibg_iface_tx_bytes_total{iface="eth0"}[30s])*8'
QDISC_BACKLOG_BYTES = 'ibg_qdisc_backlog_bytes'
QDISC_OVERLIMITS = 'rate(ibg_qdisc_overlimits_total[30s])'
QDISC_SENT_BPS = 'rate(ibg_qdisc_sent_bytes_total[30s])*8'
HOST_CPU = '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[30s]))*100'
HOST_MEM = '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)*100'
HOST_LOAD = 'node_load1'
HOST_NET_RX = 'sum(rate(node_network_receive_bytes_total{device!="lo"}[30s]))*8'
HOST_NET_TX = 'sum(rate(node_network_transmit_bytes_total{device!="lo"}[30s]))*8'
OPEN5GS_UP = 'up{job="open5gs"}'
AMF_REG_REQ = 'rate(fivegs_amffunction_rm_reginitreq[30s])'
AMF_REG_OK = 'rate(fivegs_amffunction_rm_reginitsucc[30s])'
SMF_UES = 'ues_active'
SMF_PFCP = 'pfcp_sessions_active'
SMF_BEARERS = 'bearers_active'
# NOTE: Open5GS's own fivegs_ep_n3_gtp_*datapkt counters stay at 0 (stubs, verified live), so the
# UPF packet rate is taken from the ogstun interface counters collected by the custom exporter.
UPF_PKT_UL = 'rate(ibg_iface_rx_packets_total{service="upf",iface="ogstun"}[30s])'
UPF_PKT_DL = 'rate(ibg_iface_tx_packets_total{service="upf",iface="ogstun"}[30s])'
EXPORTER_SCRAPE = 'ibg_exporter_scrape_seconds'
EXPORTER_ERRORS = 'rate(ibg_exporter_errors_total[1m])'
UE_SESSION_UP = 'ibg_ue_session_up'

COL_UE, COL_UPF, COL_SRV = "blue", "orange", "green"

UPDOWN_MAP = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 1},
    "1": {"text": "UP", "color": "green", "index": 0}}}]


class Builder:
    """Collects panels with automatic ids and a simple left-to-right grid layout."""

    def __init__(self) -> None:
        self.panels: List[Dict[str, Any]] = []
        self._id = 0
        self._y = 0
        self._x = 0
        self._row_h = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def row(self, title: str) -> None:
        self._flush_line()
        self.panels.append({
            "id": self._next_id(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self._y}, "panels": [],
        })
        self._y += 1

    def _flush_line(self) -> None:
        if self._x:
            self._y += self._row_h
            self._x = 0
            self._row_h = 0

    def _place(self, w: int, h: int) -> Dict[str, int]:
        if self._x + w > 24:
            self._flush_line()
        pos = {"h": h, "w": w, "x": self._x, "y": self._y}
        self._x += w
        self._row_h = max(self._row_h, h)
        if self._x >= 24:
            self._flush_line()
        return pos

    @staticmethod
    def _targets(queries: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
        return [{"datasource": DS, "expr": expr, "legendFormat": legend,
                 "refId": chr(ord("A") + i), "range": True, "instant": False}
                for i, (expr, legend) in enumerate(queries)]

    def _base(self, kind: str, title: str, description: str, queries, w: int, h: int) -> Dict[str, Any]:
        return {
            "id": self._next_id(), "type": kind, "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h), "targets": self._targets(queries),
        }

    # ---- embedded chat (the Intent Console page in an iframe) -----------------------
    def chat(self, title: str, description: str, url: str, *, h: int = 16) -> None:
        src = url.rstrip("/") + "/?embed=1"
        self.panels.append({
            "id": self._next_id(), "type": "text", "title": title, "description": description,
            "gridPos": self._place(24, h),
            "options": {"mode": "html", "code": {"language": "plaintext", "showLineNumbers": False, "wrapLines": True},
                        "content": f'<iframe src="{src}" title="Intent Console" '
                                   'style="width:100%;height:100%;min-height:420px;border:0;border-radius:6px"></iframe>'},
        })

    # ---- stat ---------------------------------------------------------------
    def stat(self, title: str, description: str, queries: Sequence[Tuple[str, str]], *,
             unit: str = "none", w: int = 4, h: int = 4,
             mappings: Optional[list] = None,
             thresholds: Optional[List[Tuple[Optional[float], str]]] = None,
             color_mode: str = "background", text_mode: str = "auto",
             graph_mode: str = "none", decimals: Optional[int] = None) -> None:
        p = self._base("stat", title, description, queries, w, h)
        steps = thresholds or [(None, "green")]
        defaults: Dict[str, Any] = {
            "unit": unit,
            "mappings": mappings or [],
            "thresholds": {"mode": "absolute",
                           "steps": [{"color": c, "value": v} for v, c in steps]},
            "color": {"mode": "thresholds"},
        }
        if decimals is not None:
            defaults["decimals"] = decimals
        p["fieldConfig"] = {"defaults": defaults, "overrides": []}
        p["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": color_mode, "graphMode": graph_mode, "justifyMode": "center",
            "textMode": text_mode, "orientation": "auto",
        }
        self.panels.append(p)

    def updown_stat(self, title: str, description: str, queries, *, w: int = 4, h: int = 4,
                    text_mode: str = "auto") -> None:
        self.stat(title, description, queries, unit="none", w=w, h=h, mappings=UPDOWN_MAP,
                  thresholds=[(None, "red"), (1, "green")], text_mode=text_mode)

    # ---- timeseries ---------------------------------------------------------
    def timeseries(self, title: str, description: str, queries: Sequence[Tuple[str, str]], *,
                   unit: str, w: int = 12, h: int = 8,
                   colors: Optional[Dict[str, str]] = None,
                   thresholds: Optional[List[Tuple[Optional[float], str]]] = None,
                   threshold_style: str = "off", min_: Optional[float] = None,
                   max_: Optional[float] = None, stack: bool = False) -> None:
        p = self._base("timeseries", title, description, queries, w, h)
        steps = thresholds or [(None, "green")]
        defaults: Dict[str, Any] = {
            "unit": unit,
            "color": {"mode": "palette-classic"},
            "custom": {
                "drawStyle": "line", "lineInterpolation": "linear", "lineWidth": 2,
                "fillOpacity": 12 if not stack else 40, "showPoints": "never", "spanNulls": False,
                "axisPlacement": "auto", "gradientMode": "none",
                "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                "thresholdsStyle": {"mode": threshold_style},
            },
            "thresholds": {"mode": "absolute",
                           "steps": [{"color": c, "value": v} for v, c in steps]},
        }
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        overrides = [{"matcher": {"id": "byName", "options": name},
                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}]}
                     for name, col in (colors or {}).items()]
        p["fieldConfig"] = {"defaults": defaults, "overrides": overrides}
        p["options"] = {
            "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True,
                       "calcs": ["mean", "max", "lastNotNull"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
        self.panels.append(p)


def build(intent_url: str = "http://localhost:8088") -> Dict[str, Any]:
    b = Builder()

    # ------------------------------------------------------------- Ask the network
    b.row("Ask the network (Intent Console)")
    b.chat(
        "Ask a question about the network",
        "Type a question in plain English (for example: is the UPF forwarding everything the UE sends?). The intent "
        "engine turns it into safe Prometheus queries, runs them through Grafana, computes the statistics and "
        "explains the result — with Gemini if a key is configured, otherwise with its offline rules engine. "
        "Collapse this row if you do not need it.",
        intent_url)

    # ------------------------------------------------------------------ Overview
    b.row("Overview")
    b.updown_stat(
        "Network function up grid",
        "One tile per container of the stack. Green UP means Docker reports the container as running; "
        "red DOWN means it exists but is stopped or crashed. Source: ibg_container_up from the custom "
        "exporter (it asks the Docker API). A tile that is missing altogether means the container was never created.",
        [(Q["nf_up"], "{{service}}")], w=24, h=7, text_mode="value_and_name")
    b.updown_stat(
        "SBI reachable (TCP :7777)",
        "Can the exporter open a TCP connection to the Service-Based Interface port 7777 of each core "
        "network function? NFs talk to each other over this HTTP/2 interface, so a red tile means the NF "
        "process is not answering even if its container is running. Source: ibg_nf_sbi_up.",
        [(Q["sbi_up"], "{{service}}")], w=24, h=5, text_mode="value_and_name")
    b.stat(
        "Registered UEs",
        "Number of phones (UEs) currently registered with the AMF, the 5G core entry point. "
        "Should be 1 when the UERANSIM UE is attached. Source: fivegs_amffunction_rm_registeredsubnbr.",
        [(Q["registered_ues"], "registered UEs")], unit="none", w=4, h=5, graph_mode="area",
        thresholds=[(None, "red"), (1, "green")])
    b.stat(
        "PDU sessions (AMF / SMF / UPF)",
        "A PDU session is the data tunnel for one UE. The AMF, SMF and UPF each count them independently; "
        "the three numbers should be equal. If they differ, session setup is stuck in the middle. "
        "Sources: amf_session, pfcp_sessions_active, fivegs_upffunction_upf_sessionnbr.",
        [(Q["pdu_sessions_amf"], "AMF"), (Q["pdu_sessions_smf"], "SMF"), (Q["pdu_sessions_upf"], "UPF")],
        unit="none", w=6, h=5, color_mode="value", text_mode="value_and_name",
        thresholds=[(None, "red"), (1, "green")])
    b.stat(
        "Connected gNBs",
        "Number of radio base stations (gNodeBs) connected to the AMF over N2/NGAP. "
        "Should be 1 for this lab. Source: gnb.",
        [(Q["connected_gnbs"], "gNBs")], unit="none", w=4, h=5, graph_mode="area",
        thresholds=[(None, "red"), (1, "green")])
    b.updown_stat(
        "UE data path up",
        "Result of a 1-packet ping sent from the UE through its 5G tunnel (uesimtun0) to the application "
        "server, refreshed every 10 s. UP means user traffic can really cross gNB, UPF and N6. "
        "Source: ibg_path_up.",
        [(Q["path_up"], "path")], w=3, h=5)
    b.stat(
        "UE -> server RTT",
        "Round-trip time of that ping in milliseconds. It is omitted while the path is down. "
        "In this single-host lab it is normally a few milliseconds. Source: ibg_path_rtt_ms.",
        [(Q["path_rtt_ms"], "RTT")], unit="ms", w=3, h=5, decimals=2,
        thresholds=[(None, "green"), (50, "yellow"), (200, "red")])
    b.updown_stat(
        "UE tunnel exists",
        "Does the interface uesimtun0 exist inside the UE container? It appears when the PDU session is "
        "established and gives the UE its 5G IP address. Source: ibg_ue_session_up.",
        [(UE_SESSION_UP, "uesimtun0")], w=4, h=5)

    # ------------------------------------------------------- User plane in vs out
    b.row("User plane in vs out")
    colors_ul = {"UE sends (uesimtun0 tx)": COL_UE, "UPF receives (ogstun rx)": COL_UPF,
                 "App server receives (eth0 rx)": COL_SRV}
    b.timeseries(
        "User plane in vs out - uplink",
        "Uplink = UE to application server. Three measurement points along the same flow: what the UE "
        "transmits into its tunnel, what the UPF reads from its ogstun device, and what finally arrives at "
        "the server on the N6 side. In a healthy path the three lines overlap. A gap between lines shows "
        "where packets are lost. Sources: ibg_iface_* counters turned into bits per second with rate()*8.",
        [(Q["ue_uplink_bps"], "UE sends (uesimtun0 tx)"),
         (Q["upf_uplink_bps"], "UPF receives (ogstun rx)"),
         (Q["server_rx_bps"], "App server receives (eth0 rx)")],
        unit="bps", w=12, h=9, colors=colors_ul, min_=0)
    colors_dl = {"App server sends (eth0 tx)": COL_SRV, "UPF sends (ogstun tx)": COL_UPF,
                 "UE receives (uesimtun0 rx)": COL_UE}
    b.timeseries(
        "User plane in vs out - downlink",
        "Downlink = application server to UE, measured in the opposite order: what the server sends, "
        "what the UPF writes into ogstun, and what the UE receives on uesimtun0. Overlapping lines mean "
        "nothing is lost; a gap shows the hop where traffic disappears. Sources: ibg_iface_* counters, rate()*8.",
        [(Q["server_tx_bps"], "App server sends (eth0 tx)"),
         (Q["upf_downlink_bps"], "UPF sends (ogstun tx)"),
         (Q["ue_downlink_bps"], "UE receives (uesimtun0 rx)")],
        unit="bps", w=12, h=9, colors=colors_dl, min_=0)
    ratio_steps = [(None, "red"), (90, "yellow"), (95, "green"), (110, "orange")]
    b.timeseries(
        "Delivery ratio (end-to-end)",
        "Share of the traffic that survives the whole 5G path: uplink = server received / UE sent, "
        "downlink = UE received / server sent, in percent. 100 % means perfect delivery. Below 90 % "
        "(red) packets are being lost; above 110 % (orange) the extra bytes are protocol overhead such as "
        "GTP-U encapsulation or TCP ACKs. Shown only while traffic is above 1 kbps, otherwise the ratio "
        "is meaningless and the panel stays empty.",
        [(RATIO_UL, "uplink: server rx / UE tx"), (RATIO_DL, "downlink: UE rx / server tx")],
        unit="percent", w=12, h=8, thresholds=ratio_steps, threshold_style="line+area", min_=0)
    b.stat(
        "Delivery ratio now",
        "Latest value of the two delivery ratios (see the panel on the left). Green is 95-110 %, "
        "yellow 90-95 %, red below 90 %, orange above 110 %. Empty when there is no traffic.",
        [(RATIO_UL, "uplink"), (RATIO_DL, "downlink")],
        unit="percent", w=6, h=8, decimals=1, thresholds=ratio_steps, text_mode="value_and_name")
    b.timeseries(
        "UPF ogstun drops",
        "Packets per second dropped on the UPF user-plane TUN device (rx plus tx). It should stay at 0. "
        "A positive value means the kernel, not the network, discarded packets inside the UPF. "
        "Source: ibg_iface_rx_drop_total / ibg_iface_tx_drop_total.",
        [(Q["upf_ogstun_drops"], "ogstun drops/s")], unit="pps", w=6, h=8,
        thresholds=[(None, "green"), (1, "red")], min_=0)
    b.timeseries(
        "gNB throughput (eth0)",
        "Traffic crossing the gNB container network interface, in bits per second. It carries the GTP-U "
        "tunnel towards the UPF (N3) and the radio-link simulation, so it is slightly larger than the "
        "user traffic. Sources: ibg_iface_rx_bytes_total / tx_bytes_total for service gnb.",
        [(Q["gnb_rx_bps"], "gNB rx"), (Q["gnb_tx_bps"], "gNB tx")], unit="bps", w=12, h=7, min_=0)
    b.timeseries(
        "UPF user-plane packets (ogstun)",
        "User packets per second crossing the UPF's TUN device ogstun: uplink = decapsulated from the gNB, "
        "downlink = going back towards the gNB. (Open5GS's own GTP-U packet counters are stubs that stay at 0, "
        "so this comes from the interface counters.) Source: ibg_iface_*_packets_total for upf/ogstun.",
        [(UPF_PKT_UL, "uplink pkts/s"), (UPF_PKT_DL, "downlink pkts/s")],
        unit="pps", w=12, h=7, min_=0)

    # ------------------------------------------------------------ Core native
    b.row("Core native metrics")
    b.timeseries(
        "Open5GS metrics endpoints up",
        "Prometheus scrape status of the native metrics endpoint (:9091) of AMF, SMF, UPF and PCF. "
        "1 = the endpoint answered the last scrape, 0 = it did not. Source: the standard up metric of job open5gs.",
        [(OPEN5GS_UP, "{{instance}}")], unit="none", w=8, h=7, min_=0, max_=1,
        thresholds=[(None, "red"), (1, "green")], threshold_style="line")
    b.timeseries(
        "AMF - registrations",
        "AMF = Access and Mobility Function. Initial registration requests received and accepted per "
        "second (the attach procedure of a UE). Equal lines mean every attempt succeeded. "
        "Sources: fivegs_amffunction_rm_reginitreq / rm_reginitsucc.",
        [(AMF_REG_REQ, "registration requests/s"), (AMF_REG_OK, "registrations accepted/s")],
        unit="ops", w=8, h=7, min_=0)
    b.timeseries(
        "AMF / SMF / UPF - active sessions",
        "PDU sessions as counted by the three core functions. They should be identical; a lasting "
        "difference means a session is half-created. Sources: amf_session, pfcp_sessions_active, "
        "fivegs_upffunction_upf_sessionnbr.",
        [(Q["pdu_sessions_amf"], "AMF"), (Q["pdu_sessions_smf"], "SMF"), (Q["pdu_sessions_upf"], "UPF")],
        unit="none", w=8, h=7, min_=0)
    b.timeseries(
        "SMF - sessions",
        "SMF = Session Management Function. UEs it manages, PFCP sessions it has opened on the UPF (N4) and "
        "bearers (QoS flows). All three should be 1 for our single UE. Sources: ues_active, "
        "pfcp_sessions_active, bearers_active.",
        [(SMF_UES, "UEs"), (SMF_PFCP, "PFCP sessions"), (SMF_BEARERS, "bearers")],
        unit="none", w=12, h=7, min_=0)
    b.timeseries(
        "Registered UEs and connected gNBs",
        "History of the two basic attach indicators: UEs registered at the AMF and gNBs connected to it. "
        "A drop to 0 shows exactly when the radio side or the UE disconnected. "
        "Sources: fivegs_amffunction_rm_registeredsubnbr, gnb.",
        [(Q["registered_ues"], "registered UEs"), (Q["connected_gnbs"], "connected gNBs")],
        unit="none", w=12, h=7, min_=0)

    # -------------------------------------------------------------- Containers
    b.row("Containers")
    b.timeseries(
        "CPU per container",
        "CPU used by each container in percent of one core (100 % = one full core). The UPF CPU rises "
        "with user-plane traffic because it forwards every packet in user space. Source: "
        "ibg_container_cpu_seconds_total, read by the custom exporter from the Docker stats API.",
        [(Q["nf_cpu_pct"], "{{service}}")], unit="percent", w=12, h=8, min_=0)
    b.timeseries(
        "Memory per container",
        "Working-set memory (RAM actually in use) of each container. A steadily climbing line can mean "
        "a memory leak. Source: ibg_container_memory_bytes (Docker stats API, usage minus reclaimable cache).",
        [(Q["nf_mem_bytes"], "{{service}}")], unit="bytes", w=12, h=8, min_=0)
    b.timeseries(
        "Network receive per container (eth0)",
        "Bits per second received on eth0 by the containers the exporter measures: UPF (N3/N4/N6), "
        "gNB, UE and the application server. Includes signalling as well as user traffic. "
        "Source: ibg_iface_rx_bytes_total.",
        [(CONTAINER_NET_RX, "{{service}}")], unit="bps", w=12, h=8, min_=0)
    b.timeseries(
        "Network transmit per container (eth0)",
        "Bits per second sent on eth0 by the UPF, gNB, UE and application server. "
        "Source: ibg_iface_tx_bytes_total.",
        [(CONTAINER_NET_TX, "{{service}}")], unit="bps", w=12, h=8, min_=0)

    b.timeseries(
        "UPF CPU",
        "CPU of the UPF container alone, in percent of one core. The UPF forwards every user packet in "
        "software, so this line follows the traffic in the user-plane panels: push more traffic and "
        "it rises. Source: ibg_container_cpu_seconds_total{service=\"upf\"}.",
        [(Q["upf_cpu_pct"], "UPF CPU")], unit="percent", w=24, h=6, min_=0)

    # ---------------------------------------------------------------- N6 qdisc
    b.row("N6 qdisc")
    b.timeseries(
        "N6 qdisc backlog (packets)",
        "N6 is the link between the UPF and the application server. This is the number of packets "
        "waiting in the egress queue (qdisc) of eth0 as reported by tc, one line per service: upf "
        "(forwarded user traffic leaving towards the server, the realistic N6 bottleneck) and app-server "
        "(the server's own egress). A growing backlog means "
        "traffic arrives faster than the queue can send, which is what a bandwidth limit produces. "
        "Source: ibg_qdisc_backlog_packets.",
        [(Q["qdisc_backlog_pkts"], "{{service}} ({{kind}})")], unit="short", w=12, h=7, min_=0)
    b.timeseries(
        "N6 qdisc backlog (bytes)",
        "Same queues (upf and app-server eth0), measured in bytes instead of packets. Source: ibg_qdisc_backlog_bytes.",
        [(QDISC_BACKLOG_BYTES, "{{service}} ({{kind}})")], unit="bytes", w=12, h=7, min_=0)
    b.timeseries(
        "N6 qdisc drops",
        "Packets per second each queue (upf and app-server eth0) threw away because it was full or over its configured limit. "
        "Persistent drops during a transfer are the clearest sign of a congested or rate-limited N6 link. "
        "Source: ibg_qdisc_drops_total.",
        [(Q["qdisc_drops_per_s"], "{{service}} ({{kind}})")], unit="pps", w=8, h=7, min_=0,
        thresholds=[(None, "green"), (1, "red")])
    b.timeseries(
        "N6 qdisc overlimits",
        "How often per second the qdisc reported that traffic exceeded its configured rate (an overlimit "
        "event). Typical for a token-bucket (tbf/htb) shaper that is delaying packets. "
        "Source: ibg_qdisc_overlimits_total.",
        [(QDISC_OVERLIMITS, "{{service}} ({{kind}})")], unit="ops", w=8, h=7, min_=0)
    b.timeseries(
        "N6 qdisc sent rate",
        "Bits per second actually dequeued by the qdisc towards the wire. Compared with the offered "
        "load it shows the effective bandwidth cap. Source: ibg_qdisc_sent_bytes_total.",
        [(QDISC_SENT_BPS, "{{service}} ({{kind}})")], unit="bps", w=8, h=7, min_=0)

    # -------------------------------------------------------------------- Host
    b.row("Host")
    b.timeseries(
        "Host CPU",
        "CPU utilisation of the whole machine running the lab, in percent (100 minus idle time). "
        "Source: node_exporter node_cpu_seconds_total.",
        [(HOST_CPU, "host CPU")], unit="percent", w=8, h=7, min_=0, max_=100)
    b.timeseries(
        "Host memory used",
        "Percentage of the machine's RAM in use (total minus available). Source: node_exporter "
        "node_memory_MemAvailable_bytes and node_memory_MemTotal_bytes.",
        [(HOST_MEM, "host memory used")], unit="percent", w=8, h=7, min_=0, max_=100)
    b.timeseries(
        "Host load (1 min)",
        "Load average over the last minute: roughly the number of processes waiting for CPU. "
        "A value above the number of CPU cores means the host is overloaded. Source: node_exporter node_load1.",
        [(HOST_LOAD, "load1")], unit="short", w=8, h=7, min_=0)
    b.timeseries(
        "Host network",
        "Total bits per second received and sent by the host's physical and virtual interfaces "
        "(loopback excluded). Source: node_exporter node_network_*_bytes_total.",
        [(HOST_NET_RX, "host rx"), (HOST_NET_TX, "host tx")], unit="bps", w=12, h=7, min_=0)
    b.timeseries(
        "Exporter health",
        "Health of the custom exporter that feeds most panels of this dashboard: seconds spent on the "
        "last collection and collector failures per second. Failures mean a source such as a container "
        "or the tc tool could not be read and its metric is intentionally missing. "
        "Sources: ibg_exporter_scrape_seconds, ibg_exporter_errors_total.",
        [(EXPORTER_SCRAPE, "collection seconds"), (EXPORTER_ERRORS, "errors/s {{collector}}")],
        unit="short", w=12, h=7, min_=0)

    return {
        "id": None,
        "uid": "ibg-observatory",
        "title": "Open5GS Observatory",
        "description": "Health and user-plane view of the Open5GS 5G core lab "
                       "(UERANSIM gNB/UE, UPF, application server).",
        "tags": ["ibg", "open5gs"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m"]},
        "templating": {"list": []},
        "annotations": {"list": [{
            "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard"}]},
        "links": [],
        "panels": b.panels,
    }


def render(intent_url: str = "http://localhost:8088") -> str:
    return json.dumps(build(intent_url), indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str]) -> int:
    """usage: build_dashboard.py [OUTPUT.json] [--intent-url http://host:8088]"""
    args = list(argv[1:])
    url = os.environ.get("IBG_INTENT_URL", "http://localhost:8088")
    if "--intent-url" in args:
        i = args.index("--intent-url")
        url = args[i + 1]
        del args[i:i + 2]
    out = Path(args[0]) if args else OUT_DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(url), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
