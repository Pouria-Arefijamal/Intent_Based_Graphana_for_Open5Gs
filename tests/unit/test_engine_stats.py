"""Stats math, aggregation, comparisons and the deterministic analysis."""
import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.analyst import analyst_input, parse_analysis, template_analysis  # noqa: E402
from intent_engine.comparisons import compare_pair, compute_comparisons  # noqa: E402
from intent_engine.executor import QueryOutcome, choose_step  # noqa: E402
from intent_engine.grafana import parse_matrix  # noqa: E402
from intent_engine.models import PlanQuery  # noqa: E402
from intent_engine.stats import Series, aggregate_series, compute_stats, percentile  # noqa: E402


def pts(values, step=5.0, t0=1000.0):
    return [(t0 + i * step, float(v)) for i, v in enumerate(values)]


def test_compute_stats_basic():
    st = compute_stats(pts([0, 10, 20, 30, 40]))
    assert st["n"] == 5 and st["min"] == 0 and st["max"] == 40 and st["mean"] == 20 and st["last"] == 40
    assert st["p95"] == pytest.approx(38.0)
    assert st["nonzero_fraction"] == pytest.approx(0.8)
    # 10 units per 5 s step == 120 per minute
    assert st["slope_per_min"] == pytest.approx(120.0)


def test_compute_stats_flat_and_empty():
    st = compute_stats(pts([7, 7, 7]))
    assert st["slope_per_min"] == 0 and st["nonzero_fraction"] == 1.0
    empty = compute_stats([])
    assert empty["n"] == 0 and empty["mean"] is None
    assert compute_stats(pts([5]))["slope_per_min"] == 0


def test_nan_inf_ignored():
    st = compute_stats(pts([1, math.nan, 3, math.inf]))
    assert st["n"] == 2 and st["mean"] == 2


def test_percentile():
    assert percentile([1.0], 0.95) == 1.0
    assert percentile([1.0, 2.0], 0.5) == 1.5
    assert percentile(list(map(float, range(101))), 0.95) == pytest.approx(95.0)


def test_parse_matrix_drops_nan_and_caps_series():
    result = [{"metric": {"service": f"s{i}"}, "values": [[1, "NaN"], [2, str(i)], [3, "+Inf"]]} for i in range(20)]
    series = parse_matrix(result)
    assert len(series) == 12
    assert series[0].labels["service"] == "s19"  # highest peaks kept
    assert all(len(s.points) == 1 for s in series)


def test_aggregate_modes():
    a = Series({"service": "a"}, pts([1, 1, 1]))
    b = Series({"service": "b"}, pts([0, 1, 1]))
    assert aggregate_series([a], "count")[1] == "single"
    p, mode = aggregate_series([a, b], "bool")
    assert mode == "min" and [v for _, v in p] == [0, 1, 1]
    p, mode = aggregate_series([a, b], "bps")
    assert mode == "sum" and [v for _, v in p] == [1, 2, 2]
    assert aggregate_series([a, b], "ms")[1] == "max"
    assert aggregate_series([], "bps") == ([], "none")


@pytest.mark.parametrize("minutes,max_points", [(1, 240), (10, 240), (15, 240), (60, 240), (360, 240)])
def test_step_keeps_points_under_limit(minutes, max_points):
    step = choose_step(minutes)
    assert step >= 5 and step % 5 == 0
    assert minutes * 60 / step + 1 <= max_points


# ---------------------------------------------------------------- comparisons

def test_compare_pair_verdicts():
    a = pts([1e6] * 10)
    assert compare_pair(a, pts([1e6] * 10))["verdict"] == "balanced"
    assert compare_pair(a, pts([0.95e6] * 10))["verdict"] == "balanced"
    assert compare_pair(a, pts([0.5e6] * 10))["verdict"] == "loss"
    assert compare_pair(a, pts([1.3e6] * 10))["verdict"] == "amplified"
    assert compare_pair(a, pts([1.3e6] * 10))["ratio_b_over_a"] == pytest.approx(1.3)


def test_compare_idle_below_10kbps_and_no_data():
    a = pts([3000] * 10)  # ICMP chatter, above the 1 kbps floor but below 10 kbps
    res = compare_pair(a, pts([3000] * 10))
    assert res["verdict"] == "idle" and res["ratio_b_over_a"] is None
    assert compare_pair(pts([0] * 10), pts([0] * 10))["verdict"] == "idle"
    assert compare_pair([], pts([1])) is None


def test_ratio_uses_only_active_window():
    a = pts([0, 0, 2e6, 2e6])
    b = pts([500, 500, 2e6, 2e6])  # b has chatter while a is idle: ignored
    assert compare_pair(a, b)["ratio_b_over_a"] == pytest.approx(1.0)


def test_compute_comparisons_chain_pairs():
    data = {
        "ue_uplink_bps": pts([1e7] * 6), "upf_uplink_bps": pts([1e7] * 6), "server_rx_bps": pts([5e6] * 6),
        "server_tx_bps": pts([0] * 6),
    }
    comps = compute_comparisons(data)
    pairs = {(c["a"], c["b"]): c for c in comps}
    assert pairs[("ue_uplink_bps", "server_rx_bps")]["verdict"] == "loss"
    assert pairs[("ue_uplink_bps", "upf_uplink_bps")]["verdict"] == "balanced"
    assert pairs[("upf_uplink_bps", "server_rx_bps")]["verdict"] == "loss"
    assert comps[0]["a"] == "ue_uplink_bps" and comps[0]["b"] == "server_rx_bps"  # end-to-end first
    assert not any(c["direction"] == "downlink" for c in comps)  # downlink chain incomplete


# ---------------------------------------------------------------- analysis

def outcome(qid, unit, series_values, labels=None, title=None):
    q = PlanQuery(id=qid, title=title or qid, promql="ibg_path_up", unit=unit)
    series = [Series(lab, pts(v)) for lab, v in zip(labels or [{}], series_values)]
    agg, mode = aggregate_series(series, unit)
    return QueryOutcome(query=q, ok=True, series=series, stats=compute_stats(agg), stats_mode=mode, agg_points=agg)


def test_verdict_crit_when_nf_down():
    o = outcome("nf_up", "bool", [[1, 1, 1], [1, 1, 0]], [{"service": "amf"}, {"service": "upf"}])
    res = template_analysis([o], [], 15)
    assert res["verdict"] == "CRIT" and "upf" in res["summary"] + " ".join(res["findings"])
    assert res["source"] == "template"


def test_verdict_crit_on_heavy_loss_and_warn_on_light_loss():
    loss50 = [{"a": "ue_uplink_bps", "b": "server_rx_bps", "direction": "uplink", "ratio_b_over_a": 0.3,
               "verdict": "loss", "mean_a_bps": 1e7, "mean_b_bps": 3e6, "samples": 10}]
    assert template_analysis([], loss50, 10)["verdict"] == "CRIT"
    light = [dict(loss50[0], ratio_b_over_a=0.7, mean_b_bps=7e6)]
    assert template_analysis([], light, 10)["verdict"] == "WARN"


def test_verdict_warn_on_drops_and_cpu():
    drops = outcome("qdisc_drops_per_s", "pps", [[0, 0, 3]])
    assert template_analysis([drops], [], 10)["verdict"] == "WARN"
    cpu = outcome("nf_cpu_pct", "percent", [[95] * 5, [10] * 5], [{"service": "upf"}, {"service": "amf"}])
    res = template_analysis([cpu], [], 10)
    assert res["verdict"] == "WARN" and "upf" in " ".join(res["findings"])


def test_verdict_ok_balanced_and_info_idle():
    bal = [{"a": "ue_uplink_bps", "b": "server_rx_bps", "direction": "uplink", "ratio_b_over_a": 0.98,
            "verdict": "balanced", "mean_a_bps": 1e7, "mean_b_bps": 9.8e6, "samples": 10}]
    assert template_analysis([], bal, 10)["verdict"] == "OK"
    idle = [dict(bal[0], ratio_b_over_a=None, verdict="idle")]
    assert template_analysis([], idle, 10)["verdict"] == "INFO"
    assert template_analysis([], [], 10)["verdict"] == "INFO"


def test_failed_and_empty_queries_reported():
    q = PlanQuery(id="a_b", title="Broken", promql="ibg_path_up")
    failed = QueryOutcome(query=q, ok=False, error="boom")
    empty = QueryOutcome(query=q, ok=True, stats=compute_stats([]))
    res = template_analysis([failed, empty], [], 5)
    assert res["verdict"] == "INFO"
    assert any("boom" in f for f in res["findings"]) and any("No data" in f for f in res["findings"])


def test_healthy_nfs_ok():
    o = outcome("nf_up", "bool", [[1, 1], [1, 1]], [{"service": "amf"}, {"service": "upf"}])
    assert template_analysis([o], [], 5)["verdict"] == "OK"


def test_parse_analysis_validation():
    good = {"verdict": "ok", "summary": "s", "findings": ["a"], "recommendations": []}
    assert parse_analysis(good)["verdict"] == "OK"
    for bad in ({}, {**good, "verdict": "GREAT"}, {**good, "summary": ""}, {**good, "findings": "x"}, [], {**good, "recommendations": [1]}):
        with pytest.raises(ValueError):
            parse_analysis(bad)


def test_analyst_input_contains_only_stats_not_intent():
    o = outcome("ue_uplink_bps", "bps", [[1e6, 2e6]])
    text = analyst_input("goal text", 10, [o], [])
    assert "goal text" in text and "ue_uplink_bps" in text and '"mean"' in text
