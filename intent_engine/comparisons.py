"""Deterministic in-vs-out comparisons (SPEC section 4).

Chains: (ue_uplink_bps -> upf_uplink_bps -> server_rx_bps) and
        (server_tx_bps -> upf_downlink_bps -> ue_downlink_bps).

For every chain (a, b, c) three pairs are produced: end-to-end (a, c) first, then each hop
(a, b) and (b, c). For a pair, `ratio_b_over_a = mean(b) / mean(a)` computed over the samples
where a > 1 kbps. The pair is `idle` when there is no such sample or mean(a) over that window is
below the 10 kbps idle threshold (ICMP probe chatter is a few kbps).
"""

from __future__ import annotations

from .stats import Point

ACTIVE_FLOOR_BPS = 1_000.0
IDLE_THRESHOLD_BPS = 10_000.0
BALANCED_LOW = 0.9
BALANCED_HIGH = 1.1

CHAINS: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("uplink", ("ue_uplink_bps", "upf_uplink_bps", "server_rx_bps")),
    ("downlink", ("server_tx_bps", "upf_downlink_bps", "ue_downlink_bps")),
)


def classify(ratio: float) -> str:
    if ratio < BALANCED_LOW:
        return "loss"
    if ratio > BALANCED_HIGH:
        return "amplified"
    return "balanced"


def compare_pair(a_points: list[Point], b_points: list[Point]) -> dict | None:
    """Return {ratio_b_over_a, verdict, mean_a, mean_b, samples}, or None if b has no overlapping data."""
    if not a_points or not b_points:
        return None
    b_map = dict(b_points)
    window = [(t, v) for t, v in a_points if v > ACTIVE_FLOOR_BPS and t in b_map]
    active_a = [v for _, v in a_points if v > ACTIVE_FLOOR_BPS]
    if not active_a:
        return {"ratio_b_over_a": None, "verdict": "idle", "mean_a": 0.0, "mean_b": 0.0, "samples": 0}
    mean_active_a = sum(active_a) / len(active_a)
    if mean_active_a < IDLE_THRESHOLD_BPS:
        return {"ratio_b_over_a": None, "verdict": "idle", "mean_a": mean_active_a, "mean_b": 0.0, "samples": len(active_a)}
    if not window:
        return None
    mean_a = sum(v for _, v in window) / len(window)
    mean_b = sum(b_map[t] for t, _ in window) / len(window)
    ratio = mean_b / mean_a
    return {
        "ratio_b_over_a": round(ratio, 4),
        "verdict": classify(ratio),
        "mean_a": mean_a,
        "mean_b": mean_b,
        "samples": len(window),
    }


def compute_comparisons(series_by_id: dict[str, list[Point]]) -> list[dict]:
    """`series_by_id` maps query id -> aggregate points (only queries that succeeded)."""
    out: list[dict] = []
    for direction, (a, b, c) in CHAINS:
        for x, y in ((a, c), (a, b), (b, c)):
            if x not in series_by_id or y not in series_by_id:
                continue
            res = compare_pair(series_by_id[x], series_by_id[y])
            if res is None:
                continue
            out.append(
                {
                    "a": x,
                    "b": y,
                    "direction": direction,
                    "ratio_b_over_a": res["ratio_b_over_a"],
                    "verdict": res["verdict"],
                    "mean_a_bps": round(res["mean_a"], 2),
                    "mean_b_bps": round(res["mean_b"], 2),
                    "samples": res["samples"],
                }
            )
    return out
