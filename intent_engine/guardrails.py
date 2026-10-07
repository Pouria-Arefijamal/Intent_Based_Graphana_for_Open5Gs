"""PromQL guardrails (SPEC section 4, guardrail 2) and the metric-name allow-list.

`validate_promql` is a small real tokenizer, not a regex blacklist. Every character of the
expression is consumed by exactly one token class, so nothing can hide between tokens:

* string literals exist only as label-matcher values inside `{...}` and are skipped as a unit;
* label matchers are parsed as `name op "value"` triples; `__name__` (and any `__*` label) is rejected;
* `[...]` range/subquery selectors must be pure durations (rate windows >= 20 s);
* numbers / durations are consumed as one token and must be well formed;
* bare identifiers are either whitelisted functions/keywords, label names (inside `by(...)`,
  `without(...)`, `on(...)`, ...) or metric names, which must be in the allow-list;
* every `{` must directly follow a metric name, so selectors that select everything are impossible;
* brackets must be balanced and correctly nested.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Collection, Iterable

import httpx

MAX_PROMQL_CHARS = 500
MIN_RANGE_SECONDS = 20  # 4 x the 5 s scrape interval
MAX_RANGE_SECONDS = 360 * 60
MIN_SUBQUERY_STEP_SECONDS = 5
MAX_SUBQUERIES = 1
MAX_OFFSET_SECONDS = 360 * 60
MAX_NESTING = 12
MAX_METRIC_NAMES = 4000  # sanity bound on what we accept from /label/__name__/values

FUNCTIONS = frozenset(
    "rate irate increase sum avg min max count topk bottomk quantile "
    "quantile_over_time avg_over_time max_over_time min_over_time delta deriv predict_linear "
    "clamp_min clamp_max abs ceil floor round histogram_quantile time vector scalar".split()
)
LABEL_KEYWORDS = frozenset("by without on ignoring group_left group_right".split())
OTHER_KEYWORDS = frozenset("and or unless bool offset inf nan".split())
KEYWORDS = LABEL_KEYWORDS | OTHER_KEYWORDS

_ID = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
_LABEL_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DUR = re.compile(r"(?:\d+(?:ms|s|m|h|d|w|y))+")
_DUR_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
_NUM = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_UNIT_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_MATCH_OPS = ("=~", "!~", "!=", "=")
_ID_CHAR = re.compile(r"[A-Za-z0-9_]")
_ALLOWED_OPERATOR_CHARS = frozenset("+-*/%^<>=,")
# Open5GS UPF GTP datapkt counters are stubs (always 0) in v2.8.0: never usable for throughput.
_DENIED_METRIC = re.compile(r"^fivegs_ep_n3_gtp_.*datapkt")


class GuardrailError(ValueError):
    """Raised when a PromQL expression violates a guardrail."""


def duration_seconds(text: str) -> float:
    return sum(int(n) * _UNIT_SECONDS[u] for n, u in _DUR_PART.findall(text))


def _check_range(content: str, expr_pos: int) -> bool:
    """Validate a `[...]` selector; return True if it is a subquery (`[range:step]`)."""
    parts = content.split(":")
    if len(parts) > 2 or not parts[0]:
        raise GuardrailError(f"malformed range selector '[{content}]' at {expr_pos}")
    for i, part in enumerate(parts):
        if part == "" and i == 1:
            continue
        if not _DUR.fullmatch(part):
            raise GuardrailError(f"range selector must contain only durations, got '[{content}]'")
    window = duration_seconds(parts[0])
    if window < MIN_RANGE_SECONDS:
        raise GuardrailError(
            f"range window '{parts[0]}' is shorter than {MIN_RANGE_SECONDS}s (scrape interval is 5s)"
        )
    if window > MAX_RANGE_SECONDS:
        raise GuardrailError(f"range window '{parts[0]}' is longer than 360m")
    if len(parts) == 2:
        if parts[1] and duration_seconds(parts[1]) < MIN_SUBQUERY_STEP_SECONDS:
            raise GuardrailError(f"subquery step '{parts[1]}' is shorter than {MIN_SUBQUERY_STEP_SECONDS}s")
        return True
    return False


def _skip_string(expr: str, i: int) -> int:
    """`i` points at an opening quote; return the index just after the closing quote."""
    quote = expr[i]
    j = i + 1
    n = len(expr)
    while j < n:
        c = expr[j]
        if c == "\\" and quote != "`":
            j += 2
            continue
        if c == quote:
            return j + 1
        j += 1
    raise GuardrailError("unterminated string literal")


def validate_promql(expr: str, allowed_metrics: Collection[str]) -> list[str]:
    """Validate `expr`; return the metric names it references. Raise GuardrailError otherwise."""
    if not isinstance(expr, str):
        raise GuardrailError("promql must be a string")
    if not expr.strip():
        raise GuardrailError("empty expression")
    if len(expr) > MAX_PROMQL_CHARS:
        raise GuardrailError(f"expression longer than {MAX_PROMQL_CHARS} characters")
    if "__name__" in expr:
        raise GuardrailError("'__name__' is not allowed")

    n = len(expr)
    i = 0
    stack: list[tuple[str, str]] = []  # (bracket char, kind)
    brace_state = ""  # name | op | value | sep  (only meaningful while top of stack is '{')
    prev = ""  # kind of the previous significant token
    pending_labels = False
    offset_pending = False
    subqueries = 0
    metrics: list[str] = []

    def top() -> tuple[str, str] | None:
        return stack[-1] if stack else None

    while i < n:
        c = expr[i]
        if c in " \t\r\n":
            i += 1
            continue

        if offset_pending and not (c.isdigit() or c == "-"):
            raise GuardrailError("'offset' must be followed by a duration such as 5m")

        t = top()
        # ---- inside a label-matcher block: name op "value" (, name op "value")* ----
        if t and t[0] == "{":
            if c == "}":
                if brace_state not in ("name", "sep"):
                    raise GuardrailError(f"incomplete label matcher before '}}' at {i}")
                stack.pop()
                i += 1
                prev = "close_brace"
                continue
            if brace_state == "name":
                m = _LABEL_ID.match(expr, i)
                if not m:
                    raise GuardrailError(f"expected a label name at {i}")
                if m.group(0).startswith("__"):
                    raise GuardrailError(f"reserved label '{m.group(0)}' is not allowed")
                i = m.end()
                brace_state = "op"
            elif brace_state == "op":
                for op in _MATCH_OPS:
                    if expr.startswith(op, i):
                        i += len(op)
                        brace_state = "value"
                        break
                else:
                    raise GuardrailError(f"expected a matcher operator at {i}")
            elif brace_state == "value":
                if c not in "\"'`":
                    raise GuardrailError(f"expected a quoted label value at {i}")
                i = _skip_string(expr, i)
                brace_state = "sep"
            else:  # sep
                if c != ",":
                    raise GuardrailError(f"expected ',' or '}}' at {i}")
                i += 1
                brace_state = "name"
            continue

        # ---- inside a by()/without()/on()/ignoring()/group_*() label list ----
        if t and t[0] == "(" and t[1] == "labels":
            if c == ")":
                stack.pop()
                i += 1
                prev = "close_paren"
                continue
            if c == ",":
                i += 1
                continue
            m = _LABEL_ID.match(expr, i)
            if not m:
                raise GuardrailError(f"expected a label name in a label list at {i}")
            if m.group(0).startswith("__"):
                raise GuardrailError(f"reserved label '{m.group(0)}' is not allowed")
            i = m.end()
            continue

        # ---- ordinary expression context ----
        if c in "\"'`":
            raise GuardrailError("string literals are only allowed as label-matcher values")

        if c == "(":
            if len(stack) >= MAX_NESTING:
                raise GuardrailError("expression nested too deeply")
            stack.append(("(", "labels" if pending_labels else "call"))
            pending_labels = False
            i += 1
            prev = "open_paren"
            continue
        if c == ")":
            if not t or t[0] != "(":
                raise GuardrailError(f"unbalanced ')' at {i}")
            stack.pop()
            i += 1
            prev = "close_paren"
            pending_labels = False
            continue
        if c == "{":
            if prev != "metric":
                raise GuardrailError(
                    "a label selector must directly follow a metric name (selectors without a metric are not allowed)"
                )
            if len(stack) >= MAX_NESTING:
                raise GuardrailError("expression nested too deeply")
            stack.append(("{", "matchers"))
            brace_state = "name"
            i += 1
            continue
        if c == "}":
            raise GuardrailError(f"unbalanced '}}' at {i}")
        if c == "[":
            if prev not in ("metric", "close_brace", "close_paren"):
                raise GuardrailError(f"range selector at {i} must follow a selector or a parenthesised expression")
            end = expr.find("]", i + 1)
            if end == -1:
                raise GuardrailError("unbalanced '['")
            content = expr[i + 1 : end].strip()
            if _check_range(content, i):
                subqueries += 1
                if subqueries > MAX_SUBQUERIES:
                    raise GuardrailError("at most one subquery is allowed per expression")
            i = end + 1
            prev = "range"
            pending_labels = False
            continue
        if c == "]":
            raise GuardrailError(f"unbalanced ']' at {i}")

        if c.isdigit() or (c == "." and i + 1 < n and expr[i + 1].isdigit()):
            m = _DUR.match(expr, i) or _NUM.match(expr, i)
            if not m:
                raise GuardrailError(f"malformed number at {i}")
            end = m.end()
            if end < n and _ID_CHAR.match(expr[end]):
                raise GuardrailError(f"malformed number/duration near position {i}")
            if offset_pending:
                if not _DUR.fullmatch(m.group(0)):
                    raise GuardrailError("'offset' must be followed by a duration such as 5m")
                if duration_seconds(m.group(0)) > MAX_OFFSET_SECONDS:
                    raise GuardrailError(f"offset '{m.group(0)}' is longer than 360m")
                offset_pending = False
            i = end
            prev = "num"
            pending_labels = False
            continue

        m = _ID.match(expr, i)
        if m:
            word = m.group(0)
            lw = word.lower()
            i = m.end()
            if lw in LABEL_KEYWORDS:
                pending_labels = True
                prev = "kw"
                continue
            pending_labels = False
            if lw in OTHER_KEYWORDS:
                prev = "kw"
                offset_pending = lw == "offset"
                continue
            if lw in FUNCTIONS:
                prev = "func"
                continue
            # anything else must be a metric name
            j = i
            while j < n and expr[j] in " \t\r\n":
                j += 1
            if j < n and expr[j] == "(":
                raise GuardrailError(f"function '{word}' is not allowed")
            if _DENIED_METRIC.match(word):
                raise GuardrailError(f"metric '{word}' is a stub counter (always 0) and must not be used")
            if word not in allowed_metrics:
                raise GuardrailError(f"unknown metric '{word}'")
            metrics.append(word)
            prev = "metric"
            continue

        if c == "!":
            if i + 1 < n and expr[i + 1] == "=":
                i += 2
                prev = "op"
                pending_labels = False
                continue
            raise GuardrailError(f"unexpected '!' at {i}")
        if c in _ALLOWED_OPERATOR_CHARS:
            i += 1
            prev = "op"
            pending_labels = False
            continue
        raise GuardrailError(f"character {c!r} at position {i} is not allowed")

    if offset_pending:
        raise GuardrailError("'offset' must be followed by a duration such as 5m")
    if stack:
        raise GuardrailError("unbalanced brackets")
    if not metrics:
        raise GuardrailError("expression does not reference any metric")
    return metrics


class MetricAllowList:
    """Allow-list = live Prometheus metric names (cached) united with the catalog names.

    If Prometheus is unreachable the catalog alone is used (`live` is False in the result).
    """

    def __init__(
        self,
        prom_url: str,
        catalog_names: Iterable[str],
        *,
        transport: httpx.BaseTransport | None = None,
        ttl: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 3.0,
    ) -> None:
        self._url = prom_url.rstrip("/") + "/api/v1/label/__name__/values"
        self._catalog = frozenset(catalog_names)
        self._ttl = ttl
        self._clock = clock
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._lock = threading.Lock()
        self._live: frozenset[str] | None = None
        self._fetched_at = -1e18

    def _fetch(self) -> frozenset[str] | None:
        try:
            resp = self._client.get(self._url)
            if resp.status_code != 200:
                return None
            body = resp.json()
            data = body.get("data") if isinstance(body, dict) else None
            if body.get("status") != "success" or not isinstance(data, list):
                return None
            names = [d for d in data if isinstance(d, str) and _ID.fullmatch(d)]
            return frozenset(names[:MAX_METRIC_NAMES])
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    def get(self) -> tuple[frozenset[str], bool]:
        """Return (allowed names, live_list_available)."""
        with self._lock:
            now = self._clock()
            if now - self._fetched_at >= self._ttl:
                self._live = self._fetch()
                self._fetched_at = now
            live = self._live
        if live is None:
            return self._catalog, False
        return self._catalog | live, True

    def reachable(self) -> bool:
        return self.get()[1]
