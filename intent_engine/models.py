"""Pydantic models shared across the engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

UNITS = ("bps", "percent", "bytes", "bool", "count", "pkts", "pps", "ms", "none")
KINDS = ("timeseries", "stat")

MAX_QUERIES = 6
MAX_TITLE = 80
MAX_INTENT_CHARS = 1000

Unit = Literal["bps", "percent", "bytes", "bool", "count", "pkts", "pps", "ms", "none"]
Kind = Literal["timeseries", "stat"]


class PlanQuery(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    promql: str = Field(min_length=1)
    unit: Unit = "none"
    kind: Kind = "timeseries"


class Plan(BaseModel):
    goal: str = Field(min_length=1, max_length=300)
    queries: list[PlanQuery] = Field(min_length=1, max_length=MAX_QUERIES)
    # ids added deterministically by complete_plan so in-vs-out can be computed
    completed_ids: list[str] = Field(default_factory=list)


class IntentRequest(BaseModel):
    intent: str
    range_minutes: int | None = None
    create_dashboard: bool = True
    engine: Literal["auto", "gemini", "rules"] = "auto"
