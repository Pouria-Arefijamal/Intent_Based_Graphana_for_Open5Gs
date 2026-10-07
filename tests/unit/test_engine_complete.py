"""complete_plan: deterministic in-vs-out evidence completion."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.catalog import default_catalog  # noqa: E402
from intent_engine.models import Plan, PlanQuery  # noqa: E402
from intent_engine.planner import DOWNLINK_SET, UPLINK_SET, complete_plan, plan_query_from_recipe  # noqa: E402

CAT = default_catalog()
INTENT = "Is the UPF forwarding everything the UE sends? Compare in vs out traffic."


def plan_of(*ids):
    qs = []
    for i in ids:
        if i in CAT.recipes:
            qs.append(plan_query_from_recipe(CAT.recipes[i]))
        else:
            qs.append(PlanQuery(id=i, title=i, promql="ibg_path_up", unit="bool", kind="stat"))
    return Plan(goal="g", queries=qs)


def ids(plan):
    return [q.id for q in plan.queries]


def test_partial_plan_completes_only_that_chain():
    out = complete_plan(plan_of("ue_uplink_bps", "upf_downlink_bps"), INTENT, CAT)
    assert set(ids(out)) == set(UPLINK_SET) | set(DOWNLINK_SET)
    assert out.completed_ids == ["upf_uplink_bps", "server_rx_bps", "server_tx_bps", "ue_downlink_bps"]
    out = complete_plan(plan_of("upf_uplink_bps"), "show me something", CAT)  # members present: keywords not needed
    assert set(ids(out)) == set(UPLINK_SET)
    assert out.completed_ids == ["ue_uplink_bps", "server_rx_bps"]


def test_no_members_plus_keywords_adds_both_chains():
    out = complete_plan(plan_of("nf_up"), INTENT, CAT)
    assert set(UPLINK_SET + DOWNLINK_SET) <= set(ids(out))
    assert len(out.queries) == 6 and "nf_up" not in ids(out)  # extra dropped, chain members win
    assert out.completed_ids == UPLINK_SET + DOWNLINK_SET


def test_cap_of_six_respected_and_extras_dropped_last_first():
    out = complete_plan(plan_of("nf_up", "sbi_up", "path_up", "path_rtt_ms", "ue_uplink_bps"), "x", CAT)
    assert len(out.queries) == 6
    assert set(UPLINK_SET) <= set(ids(out))
    assert ids(out)[:3] == ["nf_up", "sbi_up", "path_up"] and "path_rtt_ms" not in ids(out)
    assert out.completed_ids == ["upf_uplink_bps", "server_rx_bps"]


def test_no_keywords_and_no_members_untouched():
    p = plan_of("nf_up", "registered_ues")
    out = complete_plan(p, "How many UEs are registered?", CAT)
    assert out == p and out.completed_ids == []


def test_added_queries_are_catalog_verbatim():
    out = complete_plan(plan_of("ue_uplink_bps"), "anything", CAT)
    for q in out.queries:
        r = CAT.recipes[q.id]
        assert (q.promql, q.title, q.unit, q.kind) == (r.promql, r.title, r.unit, r.kind)
    assert [q.id for q in out.queries if q.id in out.completed_ids] == out.completed_ids


def test_idempotent():
    once = complete_plan(plan_of("ue_uplink_bps", "upf_downlink_bps"), INTENT, CAT)
    twice = complete_plan(once, INTENT, CAT)
    assert twice == once


def test_complete_plan_never_exceeds_cap_for_any_subset():
    for members in ([], ["server_rx_bps"], ["ue_downlink_bps", "upf_uplink_bps"], UPLINK_SET + DOWNLINK_SET):
        for extra in (0, 3, 6):
            fillers = ["nf_up", "sbi_up", "path_up", "path_rtt_ms", "registered_ues", "connected_gnbs"][:extra]
            base = (members + fillers)[:6] or ["nf_up"]
            out = complete_plan(plan_of(*base), INTENT, CAT)
            assert 1 <= len(out.queries) <= 6
            assert len(set(ids(out))) == len(out.queries)
            assert set(UPLINK_SET) <= set(ids(out)) or set(DOWNLINK_SET) <= set(ids(out))


def test_keyword_variants_trigger_completion():
    for text in ("Compare in vs out", "is anything lost?", "end-to-end check", "uplink problems", "throughput please"):
        assert complete_plan(plan_of("nf_up"), text, CAT).completed_ids, text
