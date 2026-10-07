"""Executes planned queries through Grafana and computes deterministic statistics."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .grafana import GrafanaClient, PromQueryError
from .models import PlanQuery
from .stats import Point, Series, aggregate_series, compute_stats

MAX_POINTS = 240
MIN_STEP = 5  # scrape interval
MIN_MINUTES = 1
MAX_MINUTES = 360

# repair(query, prometheus_error) -> new, already-guardrail-checked PromQL, or None
RepairFn = Callable[[PlanQuery, str], "str | None"]


def clamp_minutes(minutes: int) -> int:
    return max(MIN_MINUTES, min(MAX_MINUTES, int(minutes)))


def choose_step(range_minutes: int) -> int:
    """Smallest multiple of 5 s such that a range of `range_minutes` yields <= 240 points (inclusive ends)."""
    seconds = clamp_minutes(range_minutes) * 60
    step = math.ceil(seconds / (MAX_POINTS - 1) / MIN_STEP) * MIN_STEP
    return max(MIN_STEP, step)


@dataclass
class QueryOutcome:
    query: PlanQuery
    ok: bool = False
    error: str | None = None
    series: list[Series] = field(default_factory=list)
    stats: dict | None = None
    stats_mode: str = "none"
    agg_points: list[Point] = field(default_factory=list)
    repaired: bool = False

    def to_result(self) -> dict:
        q = self.query
        res = {
            "id": q.id,
            "title": q.title,
            "promql": q.promql,
            "unit": q.unit,
            "ok": self.ok,
            "error": self.error,
            "stats": self.stats,
            "series_count": len(self.series),
            "stats_mode": self.stats_mode,
        }
        if self.repaired:
            res["repaired"] = True
        if len(self.series) > 1:
            res["series"] = [{"labels": s.labels, "stats": compute_stats(s.points)} for s in self.series]
        return res


def run_query(
    client: GrafanaClient,
    query: PlanQuery,
    start: float,
    end: float,
    step: int,
    repair: RepairFn | None = None,
) -> QueryOutcome:
    out = QueryOutcome(query=query)
    attempt_query = query
    for round_no in range(2):
        try:
            series = client.query_range(attempt_query.promql, start, end, step)
        except PromQueryError as exc:
            if round_no == 0 and repair is not None and exc.is_client_error:
                fixed = repair(attempt_query, exc.message)
                if fixed and fixed != attempt_query.promql:
                    attempt_query = attempt_query.model_copy(update={"promql": fixed})
                    out.query = attempt_query
                    out.repaired = True
                    continue
            out.error = exc.message
            return out
        out.ok = True
        out.series = series
        agg, mode = aggregate_series(series, attempt_query.unit)
        out.agg_points = agg
        out.stats_mode = mode
        out.stats = compute_stats(agg)
        return out
    return out  # pragma: no cover


def execute(
    client: GrafanaClient,
    queries: list[PlanQuery],
    start: float,
    end: float,
    step: int,
    repair: RepairFn | None = None,
) -> list[QueryOutcome]:
    """Run all queries (in parallel, order preserved). GrafanaUnreachable / GrafanaAuthError propagate."""
    if not queries:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
        futures = [pool.submit(run_query, client, q, start, end, step, repair) for q in queries]
        return [f.result() for f in futures]
