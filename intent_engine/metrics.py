"""Prometheus metrics exposed by the engine at /metrics (SPEC section 4)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUESTS = Counter(
    "ibg_intent_requests_total",
    "Intent requests handled by the engine.",
    ["engine", "outcome"],
)
DURATION = Histogram(
    "ibg_intent_duration_seconds",
    "Wall-clock duration of /api/intent requests.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_ERRORS = Counter(
    "ibg_intent_llm_errors_total",
    "Failed Gemini calls per model.",
    ["model"],
)
