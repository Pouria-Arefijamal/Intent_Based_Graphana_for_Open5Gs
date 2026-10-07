#!/usr/bin/env python3
"""Make a copy of the Observatory dashboard that can be published on grafana.com / imported anywhere.

Same thing Grafana's "Export -> Share dashboard with another instance" does: the hard-coded datasource uid
(`ibg-prometheus`) becomes the input `${DS_PROMETHEUS}`, so the importer picks THEIR Prometheus.
Usage: python3 grafana/export_for_grafana_com.py   ->  grafana/community/open5gs-observatory.grafana-com.json
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dashboards", "open5gs_observatory.json")
DST = os.path.join(HERE, "community", "open5gs-observatory.grafana-com.json")
LOCAL_UID, INPUT = "ibg-prometheus", "DS_PROMETHEUS"


def swap(node):
    if isinstance(node, dict):
        return {k: ("${%s}" % INPUT if k == "uid" and v == LOCAL_UID else swap(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [swap(x) for x in node]
    return node


def main():
    d = swap(json.load(open(SRC)))
    # the embedded chat needs this project's intent engine, so it is not part of the shared copy
    d["panels"] = [p for p in d["panels"] if p.get("type") != "text" and p.get("title") != "Ask the network (Intent Console)"]
    d["id"] = None
    d["title"] = "Open5GS 5G SA Core - User-plane Observatory"
    d["__inputs"] = [{"name": INPUT, "label": "Prometheus", "description": "Prometheus that scrapes Open5GS, the ibg exporter and node_exporter",
                      "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus"}]
    d["__requires"] = [{"type": "grafana", "id": "grafana", "name": "Grafana", "version": "11.2.0"},
                       {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"}]
    kinds = sorted({p["type"] for p in d.get("panels", []) if p.get("type") != "row"})
    d["__requires"] += [{"type": "panel", "id": k, "name": k, "version": ""} for k in kinds]
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w") as f:
        json.dump(d, f, indent=2, sort_keys=False)
    print(f"wrote {DST}  ({len(d['panels'])} panels incl. rows)")


if __name__ == "__main__":
    main()
