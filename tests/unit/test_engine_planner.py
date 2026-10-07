"""Rules planner mapping, range extraction, LLM plan validation."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.catalog import default_catalog  # noqa: E402
from intent_engine.planner import (  # noqa: E402
    PlannerError, build_plan_from_data, extract_json, extract_range_minutes, planner_system_prompt, rules_plan,
)

CAT = default_catalog()
ALLOWED = set(CAT.metric_names)


def ids(intent):
    plan, _ = rules_plan(intent, CAT)
    return [q.id for q in plan.queries]


def test_traffic_in_vs_out_uses_both_three_point_chains():
    got = ids("Is the UPF forwarding everything the UE sends? Compare in vs out traffic for the last 10 minutes")
    assert set(got) == {"ue_uplink_bps", "upf_uplink_bps", "server_rx_bps",
                        "server_tx_bps", "upf_downlink_bps", "ue_downlink_bps"}
    assert len(got) == 6


def test_uplink_only_and_downlink_only():
    assert ids("show uplink throughput") == ["ue_uplink_bps", "upf_uplink_bps", "server_rx_bps"]
    assert ids("show downlink throughput") == ["server_tx_bps", "upf_downlink_bps", "ue_downlink_bps"]


def test_down_or_overloaded():
    got = ids("Is any network function down or overloaded?")
    assert got[:3] == ["nf_up", "sbi_up", "path_up"]
    assert "nf_cpu_pct" in got and "nf_mem_bytes" in got


def test_ues_and_sessions():
    got = ids("How many UEs are registered and how many PDU sessions are active?")
    assert "registered_ues" in got and "pdu_sessions_amf" in got and "pdu_sessions_smf" in got
    assert "smf_ues_active" in got


def test_congestion():
    assert set(ids("Is there congestion on the N6 link?")) == {
        "qdisc_backlog_pkts", "qdisc_drops_per_s", "path_rtt_ms", "upf_ogstun_drops"}


def test_latency_cpu_memory():
    assert ids("what is the latency?") == ["path_rtt_ms", "path_up"]
    assert ids("show cpu usage") == ["nf_cpu_pct"]
    assert ids("memory please") == ["nf_mem_bytes"]


def test_cpu_of_upf_does_not_trigger_traffic():
    assert ids("What is the UPF CPU?") == ["nf_cpu_pct"]


def test_default_overview_and_cap():
    plan, notes = rules_plan("hello world", CAT)
    assert len(plan.queries) == 6 and notes
    plan, notes = rules_plan("traffic cpu memory sessions congestion latency health", CAT)
    assert len(plan.queries) == 6 and any("first 6" in n for n in notes)


def test_gtp_stub_counters_never_in_catalog():
    assert not any("fivegs_ep_n3_gtp" in r.promql for r in CAT.recipes.values())
    assert not any("fivegs_ep_n3_gtp" in n for n in ALLOWED)


def test_recipes_follow_spec():
    r = CAT.recipes
    assert r["pdu_sessions_smf"].promql == "pfcp_sessions_active"
    assert r["smf_ues_active"].promql == "ues_active"
    assert r["nf_cpu_pct"].promql == "rate(ibg_container_cpu_seconds_total[30s])*100"
    assert r["upf_cpu_pct"].promql == 'rate(ibg_container_cpu_seconds_total{service="upf"}[30s])*100'
    assert r["nf_mem_bytes"].promql == "ibg_container_memory_bytes"


def test_prompt_mentions_catalog_and_isolates_intent():
    p = planner_system_prompt(CAT)
    assert "ue_uplink_bps" in p and "DATA" in p and "__name__" in p


@pytest.mark.parametrize("text,expected", [
    ("for the last 10 minutes", 10), ("over the past 2 hours", 120), ("in the last 45 min", 45),
    ("last hour", 60), ("nothing here", None),
])
def test_extract_range(text, expected):
    assert extract_range_minutes(text) == expected


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} hope it helps') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("not json")


def _q(**kw):
    base = {"id": "x_q", "title": "T", "promql": "ibg_path_up", "unit": "bool", "kind": "stat"}
    base.update(kw)
    return base


def test_plan_recipe_id_only_is_expanded():
    plan, w = build_plan_from_data({"goal": "g", "queries": [{"id": "ue_uplink_bps"}]}, CAT, ALLOWED)
    q = plan.queries[0]
    assert q.promql == CAT.recipes["ue_uplink_bps"].promql and q.unit == "bps" and not w


def test_plan_schema_violations():
    for bad in (
        [], "x", {"goal": "g", "queries": []},
        {"goal": "g", "queries": [_q(id=f"q{i}") for i in range(7)]},
        {"goal": "g", "queries": [_q(title="t" * 81)]},
        {"goal": "g", "queries": [_q(id="Bad Id")]},
        {"goal": "g", "queries": [_q(), _q()]},
        {"goal": "", "queries": [_q()]},
        {"queries": [_q()]},
        {"goal": "g", "queries": ["nope"]},
    ):
        with pytest.raises(PlannerError):
            build_plan_from_data(bad, CAT, ALLOWED)


def test_plan_drops_unsafe_queries_but_keeps_good_ones():
    data = {"goal": "g", "queries": [_q(id="bad", promql='{__name__=~".+"}'), _q(id="good")]}
    plan, warnings = build_plan_from_data(data, CAT, ALLOWED)
    assert [q.id for q in plan.queries] == ["good"]
    assert any("bad" in w for w in warnings)


def test_plan_all_unsafe_raises():
    with pytest.raises(PlannerError):
        build_plan_from_data({"goal": "g", "queries": [_q(promql="made_up_metric")]}, CAT, ALLOWED)


def test_plan_unknown_unit_kind_are_normalised():
    plan, _ = build_plan_from_data({"goal": "g", "queries": [_q(unit="furlongs", kind="pie")]}, CAT, ALLOWED)
    assert plan.queries[0].unit == "none" and plan.queries[0].kind == "timeseries"
    json.dumps(plan.model_dump())
