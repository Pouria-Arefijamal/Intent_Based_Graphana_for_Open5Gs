"""Regression test: two intents with the same (truncated) text must NOT share a dashboard title.

Found by the end-to-end test: Grafana silently replaced an older dashboard when a new one had the same
title in the same folder, so the link returned by the engine pointed at a dashboard that did not exist.
"""
from intent_engine import dashboards

KW = dict(range_minutes=5, outcomes=[], analysis={"verdict": "OK", "summary": "s", "findings": [], "recommendations": []},
          comparisons=[], engine="rules", ds_uid="ibg-prometheus", folder_uid=None)
TEXT = "Is the UPF forwarding everything the UE sends? Compare in vs out traffic and tell me if anything is lost."


def test_same_text_different_ids_gives_distinct_titles_and_uids():
    a = dashboards.build_intent_dashboard(intent_id="i-20261007-102519-8021", intent=TEXT, **KW)["dashboard"]
    b = dashboards.build_intent_dashboard(intent_id="i-20261007-102715-5575", intent=TEXT, **KW)["dashboard"]
    assert a["title"] != b["title"]
    assert a["uid"] != b["uid"]
    assert len(a["title"]) <= 100
