"""Guardrail tests: PromQL validator matrix, allow-list caching/fallback, catalog consistency."""
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.catalog import default_catalog  # noqa: E402
from intent_engine.guardrails import GuardrailError, MetricAllowList, validate_promql  # noqa: E402

CAT = default_catalog()
ALLOWED = set(CAT.metric_names)

ACCEPT = [
    'ibg_container_up',
    'ibg_container_up{service="upf"}',
    'ibg_container_up{service=~"u.*",role!="ran"}',
    'ibg_container_up{}',
    'sum by (service)(rate(ibg_iface_rx_bytes_total{iface="eth0"}[1m]))',
    'sum(rate(ibg_iface_rx_bytes_total[30s])) by (service, iface)',
    'histogram_quantile(0.95, sum by (le)(rate(ibg_path_rtt_ms[5m])))',
    'topk(3, rate(ibg_iface_rx_bytes_total[30s]))',
    'avg_over_time(ibg_path_rtt_ms[5m:1m])',
    'max_over_time(ibg_path_rtt_ms[10m:15s])',
    'clamp_min(ibg_path_rtt_ms, 0) > bool 5',
    'ibg_container_up offset 5m',
    'ibg_container_up offset 360m',
    'ibg_path_up == 0',
    'rate(ibg_iface_rx_bytes_total{service="upf"}[30s]) / on(service) group_left rate(ibg_iface_tx_bytes_total[30s])',
    'ibg_path_up and ibg_container_up{service="ue"}',
    'deriv(ibg_qdisc_backlog_packets[2m]) * 60',
    # brackets, parentheses and braces inside a quoted matcher value are data, not structure
    'ibg_path_up{note="hello (world) [x] {y}"}',
    'predict_linear(ibg_container_memory_bytes[10m], 3600)',
    'rate(ibg_container_cpu_seconds_total[1h30m])*1e2',
    # newlines are plain whitespace
    'ibg_path_up\nand\nibg_container_up{service="ue"}',
]

REJECT = [
    ('', "empty expression", "empty"),
    ('   ', "empty expression", "blank"),
    ('{__name__=~".+"}', "'__name__' is not allowed", "__name__ selector"),
    ('{__name__="up"}', "'__name__' is not allowed", "__name__ selector exact"),
    ('ibg_container_up{__name__=~".+"}', "'__name__' is not allowed", "__name__ inside matcher"),
    ('sum by (__name__)(ibg_container_up)', "'__name__' is not allowed", "__name__ in by"),
    ('ibg_path_up{note="__name__"}', "'__name__' is not allowed", "__name__ even inside a value"),
    ('{job=~".+"}', "must directly follow a metric name", "selector without metric"),
    ('{}', "must directly follow a metric name", "empty selector"),
    ('ibg_container_up or {job=~".+"}', "must directly follow a metric name", "injection via or"),
    ('ibg_container_up{service="a"}{role="b"}', "must directly follow a metric name", "double selector"),
    ('rate(ibg_iface_rx_bytes_total[30s]', "unbalanced brackets", "unbalanced paren"),
    ('ibg_container_up)', "unbalanced ')'", "stray paren"),
    ('ibg_container_up{service="a"', "unbalanced brackets", "unbalanced brace"),
    ('ibg_container_up}', "unbalanced '}'", "stray brace"),
    ('rate(ibg_iface_rx_bytes_total[30s)', "unbalanced '['", "unbalanced bracket"),
    ('rate(ibg_iface_rx_bytes_total[30s]]', "unbalanced ']'", "stray bracket"),
    ('ibg_container_up{service="a}', "unterminated string", "unterminated string"),
    ('x' * 501, "longer than 500", "too long"),
    ('totally_unknown_metric', "unknown metric", "unknown metric"),
    ('rate(secret_metric[30s])', "unknown metric", "unknown metric in rate"),
    ('label_replace(ibg_container_up, "a", "b", "c", "d")', "function 'label_replace' is not allowed", "non-whitelisted function"),
    ('absent(ibg_container_up)', "function 'absent' is not allowed", "non-whitelisted function absent"),
    ('rate(ibg_iface_rx_bytes_total[5s])', "shorter than 20s", "window below 20s"),
    ('rate(ibg_iface_rx_bytes_total[2d])', "longer than 360m", "window above 6h"),
    ('rate(ibg_iface_rx_bytes_total[abc])', "only durations", "bad range"),
    ('ibg_container_up @ 100', "not allowed", "@ modifier"),
    ('ibg_container_up; drop', "not allowed", "semicolon"),
    ('ibg_container_up # comment', "not allowed", "comment"),
    ('"just a string"', "string literals are only allowed", "string literal"),
    ('1+1', "does not reference any metric", "no metric"),
    ('vector(1)', "does not reference any metric", "no metric vector"),
    ('ibg_container_up{service=~"x"} and on(__name__) ibg_path_up', "'__name__' is not allowed", "__name__ in on()"),
    ('ibg_container_up{service}', "incomplete label matcher", "matcher without operator"),
    ('ibg_container_up{service="a" role="b"}', "expected ','", "missing comma"),
    ('ibg_container_up{"service"="a"}', "expected a label name", "quoted label name"),
    ('ibg_container_up{__meta="a"}', "reserved label", "reserved label"),
    ('fivegs_ep_n3_gtp_indatapktn3upf', "stub counter", "stub GTP counter"),
    ('rate(fivegs_ep_n3_gtp_outdatapktn3upf[30s])', "stub counter", "stub GTP counter in rate"),
    ('ibg_container_up\n; DROP TABLE', "not allowed", "real newline then injection"),
    ('ibg_container_up\nor\n{job=~".+"}', "must directly follow a metric name", "newline before bare selector"),
    ('ibg_container_up\\n; DROP TABLE', "not allowed", "literal backslash-n"),
    ('up{job=~"(?s).*"} ~ x', "not allowed", "tilde operator"),
    ('(' * 30 + 'ibg_container_up' + ')' * 30, "nested too deeply", "nesting bomb"),
    ('rate(ibg_iface_rx_bytes_total[30s]) $(whoami)', "not allowed", "shell injection"),
    # subquery / offset amplification
    ('max_over_time(max_over_time(ibg_path_up[360m:1ms])[360m:1ms])', "subquery step", "subquery 1ms step"),
    ('max_over_time(ibg_path_up[360m:1s])', "subquery step", "subquery 1s step"),
    ('max_over_time(max_over_time(ibg_path_up[10m:15s])[10m:15s])', "at most one subquery", "nested valid subqueries"),
    ('max_over_time(ibg_path_up[10m:15s]) + max_over_time(ibg_path_up[10m:15s])', "at most one subquery", "two subqueries"),
    ('max_over_time(ibg_path_up[7h:1m])', "longer than 360m", "subquery range above cap"),
    ('ibg_path_up offset 1000000w', "offset", "huge offset"),
    ('ibg_path_up offset 361m', "longer than 360m", "offset just above cap"),
    ('ibg_path_up offset', "offset", "dangling offset"),
    ('ibg_path_up offset 5', "offset", "offset without unit"),
    ('ibg_path_up offset (5m)', "offset", "offset with paren"),
]


@pytest.mark.parametrize("expr", ACCEPT)
def test_accepts_valid(expr):
    assert validate_promql(expr, ALLOWED)


@pytest.mark.parametrize("expr,reason,why", REJECT, ids=[r[2] for r in REJECT])
def test_rejects_invalid_with_reason(expr, reason, why):
    with pytest.raises(GuardrailError) as ei:
        validate_promql(expr, ALLOWED)
    assert reason in str(ei.value), f"{why}: got {ei.value}"


def test_every_reject_case_really_differs_from_accepts():
    assert not {r[0] for r in REJECT} & set(ACCEPT)


def test_validator_is_not_fooled_by_stripping_the_bad_part():
    # the same expressions with the offending part removed are valid, so the rejections above are caused by it
    assert validate_promql('ibg_path_up offset 5m', ALLOWED)
    assert validate_promql('max_over_time(ibg_path_up[10m:15s])', ALLOWED)
    assert validate_promql('ibg_container_up{service="a"}', ALLOWED)


def test_all_catalog_recipes_pass_guardrails():
    for r in CAT.recipes.values():
        metrics = validate_promql(r.promql, ALLOWED)
        assert metrics, r.id


def test_allowed_set_is_enforced_case_sensitively():
    assert validate_promql("my_new_metric", ALLOWED | {"my_new_metric"}) == ["my_new_metric"]
    with pytest.raises(GuardrailError):
        validate_promql("MY_NEW_METRIC", ALLOWED | {"my_new_metric"})


def test_returns_metric_names():
    assert validate_promql('sum by (service)(rate(ibg_iface_rx_bytes_total[30s])) / ibg_path_up', ALLOWED) == [
        "ibg_iface_rx_bytes_total", "ibg_path_up"]


def test_allowlist_union_cache_and_fallback():
    calls = {"n": 0}
    state = {"up": True}
    now = {"t": 0.0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert req.url.path == "/api/v1/label/__name__/values"
        if not state["up"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"status": "success", "data": ["live_metric", "bad name!", 5]})

    al = MetricAllowList("http://prom:9090", ["cat_metric"], transport=httpx.MockTransport(handler),
                         clock=lambda: now["t"])
    names, live = al.get()
    assert live and names == {"cat_metric", "live_metric"}
    al.get()
    assert calls["n"] == 1  # cached
    now["t"] = 59
    al.get()
    assert calls["n"] == 1
    state["up"] = False
    now["t"] = 61
    names, live = al.get()
    assert not live and names == {"cat_metric"}  # catalog-only fallback
    assert calls["n"] == 2


def test_allowlist_bad_status_falls_back():
    al = MetricAllowList("http://prom:9090", ["a"], transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert al.get() == (frozenset({"a"}), False)
