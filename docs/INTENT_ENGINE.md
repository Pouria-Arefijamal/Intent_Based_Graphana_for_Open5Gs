# Intent engine

Service in `intent_engine/` (FastAPI, port 8088). Contract: `docs/SPEC.md` section 4. This page describes how it is built.

Error behaviour: the API answers 400 (empty/oversized intent or invalid Host), 413 (body too large), 422 (schema), 502 (Grafana unreachable or rejecting the engine's credentials). Whenever the LLM is missing or fails, the rules engine and template analysis are used instead, so the LLM being down never produces an error response.

## Pipeline

```
intent -> planner (Gemini | rules) -> guardrails -> executor (Grafana datasource proxy)
       -> stats + comparisons (deterministic) -> analyst (Gemini | template) -> Grafana dashboard
```

| module | role |
|---|---|
| `config.py` | Settings from env only. Secrets are excluded from `repr`. |
| `catalog.yaml`, `catalog.py` | Recipes (vetted PromQL, SPEC table) and the static metric allow-list. |
| `guardrails.py` | PromQL tokenizer/validator and `MetricAllowList` (live names cached 60 s, catalog fallback). |
| `gemini.py` | REST client, model fallback, `thought` parts ignored, key only in the `x-goog-api-key` header. |
| `planner.py` | Rules planner (keyword to recipes, range extraction) and LLM planner / repair round. |
| `grafana.py` | Datasource-proxy `query_range`, folder and dashboard upserts. |
| `executor.py` | Step selection (<= 240 points, multiple of 5 s), parallel execution, one repair round. |
| `stats.py`, `comparisons.py` | n/min/mean/p95/max/last/slope_per_min/nonzero_fraction; in-vs-out ratios. |
| `analyst.py` | LLM analysis constrained to stats, and the deterministic template analysis. |
| `dashboards.py` | Per-intent dashboard JSON and the "Intent Console" iframe dashboard (background retry thread). |
| `service.py`, `main.py` | Orchestration and routes. |

## Guardrails (all enforced before any query leaves the engine)

* Plan: JSON schema, at most 6 queries, titles at most 80 chars, snake_case unique ids. A schema violation falls back to the rules engine.
* PromQL: at most 500 chars; every identifier must be a whitelisted function/keyword, a label name or an allow-listed metric
  (live Prometheus names united with the catalog); `__name__` and any `__*` label rejected; every `{` must follow a metric name
  (so `{job=~".+"}` style select-all is impossible); balanced and correctly nested brackets (depth <= 12); string literals only as
  matcher values; range windows between 20 s and 6 h; `@`, `;`, `#`, `~` outside matchers rejected; the UPF GTP counters
  `fivegs_ep_n3_gtp_*datapkt` (stubs, always 0) are denied outright. Unsafe queries in an LLM plan are dropped with a warning; if none remain the rules engine is used.
* Data is read only through `query_range` via Grafana's datasource proxy; the one direct Prometheus call is the
  metric-name listing (`/api/v1/label/__name__/values`) used for the allow-list. The engine also **writes** to Grafana:
  the "Intent-Based" folder, per-intent dashboards and the "Intent Console" dashboard, and nothing else.
* Subqueries: at most one per expression, step >= 5 s, range <= 360 m; `offset` must be a duration <= 360 m.
* Intent text is passed as a JSON data field, never inside the system prompt, and never to the analyst. The analyst sees computed numbers only.
* Grafana runs with HTML sanitising disabled (needed for the console iframe), so every user/LLM-derived string in a text
  panel or panel description goes through `md_safe`: whitespace/newlines collapsed, `javascript:`/`vbscript:`/`data:` schemes
  neutralised, all Markdown metacharacters backslash-escaped, then HTML-escaped. Dashboard and panel titles use `text_safe`
  (angle brackets, ampersands and quotes removed, schemes neutralised).
* HTTP hardening (`security.py`): the Host header must be `localhost`, `127.0.0.1`, `[::1]`, `intent-engine`, the host of
  `PUBLIC_INTENT_URL`, or one of the comma list in `ALLOWED_HOSTS` (`*` disables the check); otherwise 400 (DNS-rebinding
  protection; the compose healthcheck uses 127.0.0.1 and the Prometheus scrape uses `intent-engine:8088`). Request bodies
  over 16 KB get 413, whether announced by Content-Length or streamed chunked.

## Plan completion (`planner.complete_plan`)

An LLM must not decide whether the evidence for a verdict exists, so after the plan is parsed and guarded (and before
execution, which covers repaired queries as they keep their ids) the engine deterministically completes it:

* Chains: uplink = `ue_uplink_bps`, `upf_uplink_bps`, `server_rx_bps`; downlink = `server_tx_bps`, `upf_downlink_bps`, `ue_downlink_bps`.
* If the plan holds at least one member of a chain, the missing members of that chain are added.
* If it holds no member of either chain and the intent matches the traffic keywords (traffic, throughput, forward, loss/lost, compare, end-to-end, in vs out, uplink, downlink...), both chains are added.
* The 6-query cap holds: chain members win, non-chain extras are dropped (last first) with a warning.
* Added queries are the catalog recipes verbatim; their ids are listed in `plan.completed_ids`, shown in the UI and in the dashboard text ("added automatically so in-vs-out can be computed"). The function is idempotent.

## Behaviour details and spec interpretations

* `range_minutes` is optional: when omitted the engine parses "last 10 minutes" / "past 2 hours" from the intent, else 15. Always clamped to 1..360.
* `engine` in the response is `gemini:<model>` when the plan came from Gemini, otherwise `rules`. The analysis object carries an extra
  `source` field (`template` or `gemini:<model>`). A Gemini analysis can never lower a deterministic WARN/CRIT verdict.
* Several series per query: at most 12 are kept (highest peak first). Headline `stats` are computed over one aggregate series
  (`stats_mode`): `sum` for additive units, `min` for `bool` (any 0 shows), `max` for `ms`; per-series stats are in `series[]`.
* Comparisons: for each chain (a, b, c) the pairs (a, c), (a, b), (b, c) are emitted. `ratio_b_over_a = mean(b)/mean(a)` over samples where
  a > 1 kbps; `idle` when the mean of those samples is under 10 kbps (or none exist).
* Verdict: CRIT if any bool target is down now or a ratio is < 0.5; WARN for ratio 0.5..0.9, drops > 0, CPU p95 > 90 %, or a target that flapped;
  OK if balanced/healthy; INFO if idle or no data. If Gemini fails entirely during planning, the analyst skips Gemini too.
* Dashboards: folder "Intent-Based", uid `intent-<intent_id>`. The "Intent Console" dashboard (`ibg-intent-console`) is upserted by a daemon thread at startup (retry up to 120 s).

## Running the tests

```
docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c \
  "pip install -q -r intent_engine/requirements.txt pytest && python -m pytest tests/unit -q -k engine"
```

No network, no Docker, no real key are needed; Gemini and Grafana are `httpx.MockTransport`s.
