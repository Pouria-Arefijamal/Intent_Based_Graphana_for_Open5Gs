"""Stored-XSS neutralisation for Grafana text panels, Host allow-list, request size cap."""
import json
import pathlib
import re
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.config import Settings  # noqa: E402
from intent_engine.dashboards import analysis_markdown, build_intent_dashboard, md_safe, text_safe  # noqa: E402
from intent_engine.executor import QueryOutcome  # noqa: E402
from intent_engine.main import create_app  # noqa: E402
from intent_engine.models import PlanQuery  # noqa: E402
from intent_engine.security import MAX_BODY_BYTES, build_allowed_hosts, host_without_port  # noqa: E402
from intent_engine.stats import Series, compute_stats  # noqa: E402

PAYLOADS = [
    "[x](javascript:alert(1))",
    '![i](x" onerror=alert(1))',
    "<img src=x onerror=alert(1)>",
    "<iframe src=//evil.example></iframe>",
    "```\n</div><script>alert(1)</script>\n```",
    "`x` ``` # heading\n- list\n> quote",
    "[a][b]\n\n[b]: javascript:alert(1)",
    "<javascript:alert(1)>",
    "[x](JaVa\tScRiPt:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](vbscript:msgbox(1))",
    "&#106;avascript:alert(1)",
    "<a href=\"javascript:alert(1)\">click</a>",
]
UNESCAPED_SPECIAL = re.compile(r"(?<!\\)[\[\]()`<>*_{}]")


def strip_escapes(text: str) -> str:
    return text.replace("\\\\", "").replace("\\", "\x00")  # drop escaped backslashes, mark escapes


@pytest.mark.parametrize("payload", PAYLOADS)
def test_md_safe_leaves_no_live_markdown_or_html(payload):
    out = md_safe(payload)
    assert "\n" not in out
    assert not UNESCAPED_SPECIAL.search(out), out
    assert "<" not in out and ">" not in out and '"' not in out
    assert not re.search(r"(?i)(javascript|vbscript|data):", out.replace("\\", ""))
    # every '[' is escaped, so no link / image / reference syntax can start
    assert out.count("[") == out.count("\\[")


@pytest.mark.parametrize("payload", PAYLOADS)
def test_analysis_markdown_is_inert_everywhere(payload):
    analysis = {"verdict": "OK", "summary": payload, "findings": [payload], "recommendations": [payload]}
    md = analysis_markdown(payload, analysis, payload, [], [payload])
    assert "<" not in md and ">" not in md
    assert not re.search(r"\]\(", md)
    assert not re.search(r"(?i)(javascript|vbscript|data):", md.replace("\\", ""))
    # the only unescaped brackets/backticks come from nothing: skeleton text contains none
    assert not re.search(r"(?<!\\)[\[\]`]", md)


def test_ordinary_text_stays_readable():
    out = md_safe("UPF forwards 98.5% of traffic (ratio 0.985)")
    assert "UPF forwards 98\\.5% of traffic" in out
    assert md_safe("no data: nothing") == "no data \\: nothing"


def test_dashboard_titles_and_descriptions_are_safe():
    q = PlanQuery(id="x_q", title='<img src=x onerror=1> [x](javascript:alert(1))', promql="ibg_path_up", unit="bool")
    pts = [(1.0, 1.0)]
    o = QueryOutcome(query=q, ok=True, series=[Series({}, pts)], stats=compute_stats(pts), agg_points=pts)
    payload = build_intent_dashboard(
        intent_id="i-20261007-123456-ab12", intent='<script>alert(1)</script> javascript:alert(1)', range_minutes=5,
        outcomes=[o], analysis={"verdict": "OK", "summary": "s", "findings": [], "recommendations": []},
        comparisons=[], engine="rules", ds_uid="ibg-prometheus", folder_uid=None,
    )
    d = payload["dashboard"]
    for text in (d["title"], d["panels"][1]["title"]):
        assert "<" not in text and ">" not in text
        assert not re.search(r"(?i)javascript:", text)
    assert not re.search(r"(?i)javascript:", json.dumps(payload).replace("\\\\", ""))
    assert text_safe("a<b>&c") == "abc"


# ------------------------------------------------------------------ hosts and size limit

def make_client(**settings_kw):
    settings = Settings(grafana_url="http://g:3000", **settings_kw)
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    app = create_app(settings, grafana_transport=ok, prom_transport=ok, start_background=False)
    return TestClient(app, base_url="http://localhost")


@pytest.mark.parametrize("host,status", [
    ("localhost:8088", 200), ("127.0.0.1:8088", 200), ("[::1]:8088", 200), ("intent-engine:8088", 200),
    ("INTENT-ENGINE", 200), ("evil.example", 400), ("evil.example:8088", 400), ("localhost.evil.example", 400),
    ("", 400), ("[::2]:8088", 400),
])
def test_host_allow_list(host, status):
    c = make_client()
    r = c.get("/healthz", headers={"host": host})
    assert r.status_code == status, host


def test_allowed_hosts_env_and_public_url():
    c = make_client(allowed_hosts=("engine.lab.example",))
    assert c.get("/healthz", headers={"host": "engine.lab.example:8088"}).status_code == 200
    c = make_client(public_intent_url="http://192.168.1.50:8088")
    assert c.get("/healthz", headers={"host": "192.168.1.50:8088"}).status_code == 200
    assert Settings.from_env({"ALLOWED_HOSTS": " a.example , b.example,"}).allowed_hosts == ("a.example", "b.example")
    assert make_client(allowed_hosts=("*",)).get("/healthz", headers={"host": "anything.example"}).status_code == 200


def test_host_helpers():
    assert host_without_port("[::1]:8088") == "[::1]" and host_without_port("Intent-Engine:8088") == "intent-engine"
    assert host_without_port("localhost") == "localhost"
    hosts = build_allowed_hosts(["x.example"], ["http://[::5]:8088", "http://myhost:8088"])
    assert "[::5]" in hosts and "myhost" in hosts and "x.example" in hosts and "intent-engine" in hosts


def test_rebinding_attempt_cannot_post_intent():
    c = make_client()
    r = c.post("/api/intent", json={"intent": "traffic"}, headers={"host": "attacker.example"})
    assert r.status_code == 400 and "host" in r.text.lower()


def test_body_size_limit_content_length():
    c = make_client()
    r = c.post("/api/intent", content=b'{"intent": "' + b"a" * (MAX_BODY_BYTES + 10) + b'"}',
               headers={"content-type": "application/json"})
    assert r.status_code == 413
    small = c.post("/api/intent", json={"intent": ""})
    assert small.status_code == 400  # small bodies still reach the handler (empty intent -> 400)


def test_body_size_limit_chunked():
    c = make_client()

    def gen():
        yield b'{"intent": "'
        for _ in range(MAX_BODY_BYTES // 1024 + 4):
            yield b"a" * 1024
        yield b'"}'

    r = c.post("/api/intent", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413
