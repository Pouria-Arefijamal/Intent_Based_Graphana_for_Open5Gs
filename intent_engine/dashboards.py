"""Grafana dashboard JSON builders and the startup "Intent Console" upsert task."""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from typing import Callable

from .executor import QueryOutcome
from .grafana import GrafanaClient, GrafanaUnreachable

log = logging.getLogger("intent_engine.dashboards")

FOLDER_TITLE = "Intent-Based"
CONSOLE_UID = "ibg-intent-console"
CONSOLE_TITLE = "Intent Console"

UNIT_MAP = {
    "bps": "bps",
    "percent": "percent",
    "bytes": "bytes",
    "bool": "none",
    "count": "none",
    "pkts": "short",
    "pps": "pps",
    "ms": "ms",
    "none": "none",
}


def grafana_unit(unit: str) -> str:
    return UNIT_MAP.get(unit, "none")


_WS = re.compile(r"[\s\u200b-\u200f\u2028-\u202f\u2060\ufeff\x00-\x1f\x7f]+")
_SCHEME = re.compile(r"(j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t|v\s*b\s*s\s*c\s*r\s*i\s*p\s*t|data)\s*:", re.I)
# Markdown metacharacters that get a backslash. '<' and '>' are handled by HTML escaping instead.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~:])")



def _neutralise_schemes(text: str) -> str:
    """'javascript:x' -> 'javascript :x' so it can never parse as a URL scheme."""
    return _SCHEME.sub(lambda m: re.sub(r"\s+", "", m.group(1)) + " :", text)


def md_safe(text: object, limit: int = 1000) -> str:
    """Render user/LLM-derived text as inert Markdown for a Grafana text panel (HTML sanitising is OFF there).

    Whitespace and control characters (including newlines, so no block syntax can start) collapse to single
    spaces; dangerous URL schemes are neutralised; every Markdown metacharacter is backslash-escaped; then
    the result is HTML-escaped, so `<`, `>`, `&` and quotes can never form markup or attributes.
    """
    t = _WS.sub(" ", str(text)).strip()[:limit]
    t = _neutralise_schemes(t)
    t = _MD_SPECIAL.sub(r"\\\1", t)
    return html.escape(t, quote=True)


def text_safe(text: object, limit: int = 100) -> str:
    """Plain-text rendering for titles (not Markdown): control characters collapsed, no angle brackets
    or ampersands, dangerous URL schemes neutralised."""
    t = _WS.sub(" ", str(text)).strip()[:limit]
    t = re.sub(r"[<>&\"'`]", "", t)
    return _neutralise_schemes(t).strip()


def analysis_markdown(
    intent: str, analysis: dict, engine: str, comparisons: list[dict], completed_ids: list[str] | None = None
) -> str:
    """Markdown for the text panel. Every string that came from the user, the LLM or the query plan goes
    through `md_safe`; only fixed skeleton text and engine-formatted numbers are written verbatim."""
    verdict = analysis.get("verdict", "INFO")
    verdict = verdict if verdict in ("OK", "WARN", "CRIT", "INFO") else "INFO"
    lines = [
        f"### {verdict}: {md_safe(analysis.get('summary', ''))}",
        "",
        f"**Intent:** {md_safe(intent, 300)}",
        "",
        f"_Engine: {md_safe(engine, 100)}_",
        "",
    ]
    if completed_ids:
        lines += [
            "_Added automatically so in-vs-out can be computed: " + ", ".join(md_safe(i, 60) for i in completed_ids) + "_",
            "",
        ]
    if comparisons:
        lines += ["**In vs out**", ""]
        for c in comparisons:
            ratio = "n/a" if c.get("ratio_b_over_a") is None else md_safe(f"{float(c['ratio_b_over_a']):.3f}")
            lines.append(f"- {md_safe(c['a'], 60)} to {md_safe(c['b'], 60)}: ratio {ratio} ({md_safe(c['verdict'], 20)})")
        lines.append("")
    if analysis.get("findings"):
        lines += ["**Findings**", ""] + [f"- {md_safe(f, 400)}" for f in analysis["findings"]] + [""]
    if analysis.get("recommendations"):
        lines += ["**Recommendations**", ""] + [f"- {md_safe(r, 400)}" for r in analysis["recommendations"]]
    return "\n".join(lines)


def build_intent_dashboard(
    *,
    intent_id: str,
    intent: str,
    range_minutes: int,
    outcomes: list[QueryOutcome],
    analysis: dict,
    comparisons: list[dict],
    engine: str,
    ds_uid: str,
    folder_uid: str | None,
    completed_ids: list[str] | None = None,
) -> dict:
    """Payload for POST /api/dashboards/db (overwrite=true)."""
    uid = f"intent-{intent_id}"
    panels: list[dict] = [
        {
            "id": 1,
            "type": "text",
            "title": "Analysis",
            "gridPos": {"h": 9, "w": 24, "x": 0, "y": 0},
            "options": {"mode": "markdown", "content": analysis_markdown(intent, analysis, engine, comparisons, completed_ids)},
        }
    ]
    y = 9
    for idx, o in enumerate(outcomes):
        q = o.query
        col = idx % 2
        if col == 0 and idx > 0:
            y += 8
        panel_type = "stat" if q.kind == "stat" else "timeseries"
        panel = {
            "id": idx + 2,
            "type": panel_type,
            "title": text_safe(q.title, 80),
            "description": md_safe(q.promql, 500),
            "datasource": {"type": "prometheus", "uid": ds_uid},
            "gridPos": {"h": 8, "w": 12, "x": 12 * col, "y": y},
            "targets": [
                {
                    "refId": "A",
                    "datasource": {"type": "prometheus", "uid": ds_uid},
                    "expr": q.promql,
                    "range": True,
                    "instant": False,
                    "legendFormat": "__auto",
                }
            ],
            "fieldConfig": {"defaults": {"unit": grafana_unit(q.unit)}, "overrides": []},
        }
        if panel_type == "stat":
            panel["options"] = {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "colorMode": "value",
                "graphMode": "area",
            }
        else:
            panel["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
        panels.append(panel)

    dashboard = {
        "uid": uid,
        # The id suffix keeps titles unique: Grafana treats a same-title dashboard in the same folder as
        # a conflict and (with overwrite=true) silently replaces the OLD dashboard instead of making a new one.
        "title": f"Intent: {text_safe(intent, 60)} [{intent_id[-9:]}]"[:100],
        "tags": ["ibg", "intent"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 0,
        "editable": True,
        "refresh": "10s",
        "time": {"from": f"now-{int(range_minutes)}m", "to": "now"},
        "panels": panels,
    }
    payload: dict = {
        "dashboard": dashboard,
        "overwrite": True,
        "message": f"Created by the intent engine ({intent_id})",
    }
    if folder_uid:
        payload["folderUid"] = folder_uid
    return payload


def build_console_dashboard(public_intent_url: str) -> dict:
    src = html.escape(public_intent_url.rstrip("/") + "/", quote=True)
    content = (
        f'<iframe src="{src}" title="Intent Console" '
        'style="width:100%;height:calc(100vh - 160px);min-height:760px;border:0;border-radius:6px"></iframe>'
    )
    return {
        "dashboard": {
            "uid": CONSOLE_UID,
            "title": CONSOLE_TITLE,
            "tags": ["ibg", "intent"],
            "timezone": "browser",
            "schemaVersion": 39,
            "version": 0,
            "editable": True,
            "time": {"from": "now-15m", "to": "now"},
            "panels": [
                {
                    "id": 1,
                    "type": "text",
                    "title": "",
                    "transparent": True,
                    "gridPos": {"h": 38, "w": 24, "x": 0, "y": 0},
                    "options": {"mode": "html", "content": content},
                }
            ],
        },
        "overwrite": True,
        "message": "Intent Console (managed by the intent engine)",
    }


def upsert_console_with_retry(
    grafana: GrafanaClient,
    public_intent_url: str,
    *,
    deadline_s: float = 120.0,
    interval_s: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Retry until Grafana accepts the console dashboard or the deadline passes."""
    start = clock()
    payload = build_console_dashboard(public_intent_url)
    while True:
        try:
            if grafana.reachable():
                grafana.upsert_dashboard(payload)
                log.info("Intent Console dashboard upserted")
                return True
        except (GrafanaUnreachable, RuntimeError) as exc:
            log.info("Intent Console upsert not ready yet: %s", exc)
        if clock() - start >= deadline_s:
            log.warning("Gave up upserting the Intent Console dashboard after %.0fs", deadline_s)
            return False
        sleep(interval_s)


def start_console_task(grafana: GrafanaClient, public_intent_url: str) -> threading.Thread:
    """Run the upsert in a daemon thread so application startup never blocks."""
    t = threading.Thread(
        target=upsert_console_with_retry,
        args=(grafana, public_intent_url),
        name="intent-console-upsert",
        daemon=True,
    )
    t.start()
    return t
