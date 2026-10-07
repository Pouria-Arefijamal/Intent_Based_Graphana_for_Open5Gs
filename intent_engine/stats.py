"""Deterministic statistics over query_range results."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]  # (unix timestamp seconds, value)

EPS = 1e-12


@dataclass
class Series:
    labels: dict[str, str]
    points: list[Point] = field(default_factory=list)


def percentile(values_sorted: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0,1]) of an already sorted, non-empty list."""
    if len(values_sorted) == 1:
        return values_sorted[0]
    pos = q * (len(values_sorted) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


def slope_per_minute(points: list[Point]) -> float:
    """Least-squares slope of value vs time, expressed per minute."""
    n = len(points)
    if n < 2:
        return 0.0
    t0 = points[0][0]
    xs = [(t - t0) / 60.0 for t, _ in points]
    ys = [v for _, v in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def _r(x: float) -> float:
    return round(x, 6)


def compute_stats(points: list[Point]) -> dict:
    """n, min, mean, p95, max, last, slope_per_min, nonzero_fraction (None values when n == 0)."""
    vals = [v for _, v in points if math.isfinite(v)]
    if not vals:
        return {
            "n": 0, "min": None, "mean": None, "p95": None, "max": None,
            "last": None, "slope_per_min": None, "nonzero_fraction": None,
        }
    pts = [(t, v) for t, v in points if math.isfinite(v)]
    sorted_vals = sorted(vals)
    return {
        "n": len(vals),
        "min": _r(sorted_vals[0]),
        "mean": _r(sum(vals) / len(vals)),
        "p95": _r(percentile(sorted_vals, 0.95)),
        "max": _r(sorted_vals[-1]),
        "last": _r(pts[-1][1]),
        "slope_per_min": _r(slope_per_minute(pts)),
        "nonzero_fraction": _r(sum(1 for v in vals if abs(v) > EPS) / len(vals)),
    }


def aggregate_mode(unit: str) -> str:
    """How several series of one query are collapsed into a single series for the headline stats."""
    if unit == "bool":
        return "min"  # all-up semantics: any 0 shows
    if unit == "ms":
        return "max"
    return "sum"


def aggregate_series(series: list[Series], unit: str) -> tuple[list[Point], str]:
    """Collapse series pointwise (matching timestamps). A single series is returned as-is."""
    if not series:
        return [], "none"
    if len(series) == 1:
        return list(series[0].points), "single"
    mode = aggregate_mode(unit)
    buckets: dict[float, list[float]] = {}
    for s in series:
        for t, v in s.points:
            buckets.setdefault(t, []).append(v)
    fn = {"sum": sum, "min": min, "max": max}[mode]
    return [(t, fn(vs)) for t, vs in sorted(buckets.items())], mode
