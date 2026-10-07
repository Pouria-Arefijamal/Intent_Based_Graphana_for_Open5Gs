"""Orchestration of one /api/intent request (SPEC section 4)."""

from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import datetime, timezone

from . import analyst, dashboards, planner
from .catalog import Catalog
from .comparisons import compute_comparisons
from .config import Settings
from .executor import QueryOutcome, choose_step, clamp_minutes, execute
from .gemini import GeminiClient, GeminiError
from .grafana import GrafanaAuthError, GrafanaClient, GrafanaUnreachable
from .guardrails import GuardrailError, MetricAllowList, validate_promql
from .metrics import DURATION, REQUESTS
from .models import MAX_INTENT_CHARS, IntentRequest, Plan, PlanQuery

log = logging.getLogger("intent_engine.service")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
DEFAULT_RANGE = 15


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def new_intent_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"i-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


class IntentService:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        grafana: GrafanaClient,
        gemini: GeminiClient,
        allowlist: MetricAllowList,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.grafana = grafana
        self.gemini = gemini
        self.allowlist = allowlist

    # ------------------------------------------------------------------ public
    def run(self, req: IntentRequest) -> dict:
        started = time.monotonic()
        engine_metric, outcome = "rules", "error"
        try:
            result = self._run(req)
            engine_metric = "gemini" if result["engine"].startswith("gemini") else "rules"
            oks = [r["ok"] for r in result["results"]]
            outcome = "ok" if all(oks) else ("partial" if any(oks) else "error")
            return result
        except ServiceError as exc:
            outcome = "rejected" if exc.status < 500 else "error"
            raise
        finally:
            REQUESTS.labels(engine=engine_metric, outcome=outcome).inc()
            DURATION.observe(time.monotonic() - started)

    # ------------------------------------------------------------------ pipeline
    def _run(self, req: IntentRequest) -> dict:
        intent = _CTRL.sub(" ", req.intent).strip()
        if not intent:
            raise ServiceError(400, "intent must not be empty")
        if len(intent) > MAX_INTENT_CHARS:
            raise ServiceError(400, f"intent is longer than {MAX_INTENT_CHARS} characters")

        warnings: list[str] = []
        requested = req.range_minutes if req.range_minutes is not None else planner.extract_range_minutes(intent)
        requested = DEFAULT_RANGE if requested is None else requested
        range_minutes = clamp_minutes(requested)
        if range_minutes != requested:
            warnings.append(f"range_minutes clamped from {requested} to {range_minutes} (allowed 1..360).")

        allowed, live = self.allowlist.get()
        if not live:
            warnings.append("Prometheus metric list unavailable; the allow-list is limited to the built-in catalog.")

        plan, engine, llm_down = self._plan(req, intent, range_minutes, allowed, warnings)
        plan = self._guard_plan(plan, allowed, warnings)
        before = {q.id for q in plan.queries}
        plan = planner.complete_plan(plan, intent, self.catalog)
        for gone in sorted(before - {q.id for q in plan.queries}):
            warnings.append(f"Query '{gone}' was dropped to make room for the in-vs-out evidence queries (max 6).")

        end = time.time()
        start = end - range_minutes * 60
        step = choose_step(range_minutes)
        repair = None
        if engine.startswith("gemini"):
            def repair(q: PlanQuery, err: str) -> str | None:  # noqa: E306
                return planner.repair_query(self.gemini, q, err, self.catalog, allowed)

        try:
            outcomes = execute(self.grafana, plan.queries, start, end, step, repair)
        except GrafanaUnreachable as exc:
            raise ServiceError(502, f"{exc}; cannot execute queries") from None
        except GrafanaAuthError as exc:
            raise ServiceError(502, str(exc)) from None
        plan = Plan(goal=plan.goal, queries=[o.query for o in outcomes], completed_ids=plan.completed_ids)
        for o in outcomes:
            if o.repaired:
                warnings.append(f"Query '{o.query.id}' was rejected by Prometheus and repaired once by the LLM.")
            if o.ok and o.stats and o.stats["n"] == 0:
                warnings.append(f"Query '{o.query.id}' returned no data.")
            if len(o.series) >= 12:
                warnings.append(f"Query '{o.query.id}' returned many series; at most 12 are kept.")

        comparisons = compute_comparisons({o.query.id: o.agg_points for o in outcomes if o.ok and o.agg_points})
        analysis = self._analyse(plan, range_minutes, outcomes, comparisons, llm_down, req.engine, warnings)

        intent_id = new_intent_id()
        dashboard = None
        if req.create_dashboard:
            dashboard = self._dashboard(
                intent_id, intent, range_minutes, outcomes, analysis, comparisons, engine, plan.completed_ids, warnings
            )

        return {
            "intent_id": intent_id,
            "engine": engine,
            "intent": intent,
            "range_minutes": range_minutes,
            # the exact time window (unix seconds) that was queried, so every number can be re-checked
            "window": {"start": int(start), "end": int(end)},
            "plan": plan.model_dump(),
            "results": [o.to_result() for o in outcomes],
            "comparisons": comparisons,
            "analysis": analysis,
            "dashboard": dashboard,
            "warnings": warnings,
        }

    def _plan(
        self, req: IntentRequest, intent: str, range_minutes: int, allowed, warnings: list[str]
    ) -> tuple[Plan, str, bool]:
        """Returns (plan, engine label, llm_unavailable)."""
        want_llm = req.engine != "rules"
        if want_llm and not self.gemini.configured:
            if req.engine == "gemini":
                warnings.append("engine=gemini requested but GEMINI_API_KEY is not configured; using the rules engine.")
            want_llm = False
        llm_down = not want_llm
        if want_llm:
            try:
                plan, model, plan_warnings = planner.llm_plan(self.gemini, intent, range_minutes, self.catalog, allowed)
                warnings.extend(plan_warnings)
                return plan, f"gemini:{model}", False
            except GeminiError as exc:
                warnings.append(f"Gemini planning failed ({exc}); used the rules engine.")
                llm_down = True
            except planner.PlannerError as exc:
                warnings.append(f"Gemini plan rejected ({exc}); used the rules engine.")
        plan, notes = planner.rules_plan(intent, self.catalog)
        warnings.extend(notes)
        return plan, "rules", llm_down

    def _guard_plan(self, plan: Plan, allowed, warnings: list[str]) -> Plan:
        """Re-validate every query (rules recipes included) right before execution."""
        good: list[PlanQuery] = []
        for q in plan.queries:
            try:
                validate_promql(q.promql, allowed)
                good.append(q)
            except GuardrailError as exc:
                warnings.append(f"Dropped query '{q.id}': {exc}")
        if not good:
            raise ServiceError(422, "no query passed the guardrails")
        return Plan(goal=plan.goal, queries=good)

    def _analyse(
        self,
        plan: Plan,
        range_minutes: int,
        outcomes: list[QueryOutcome],
        comparisons: list[dict],
        llm_down: bool,
        engine_req: str,
        warnings: list[str],
    ) -> dict:
        template = analyst.template_analysis(outcomes, comparisons, range_minutes)
        if llm_down or engine_req == "rules" or not self.gemini.configured:
            return template
        try:
            analysis, _model, extra = analyst.llm_analysis(
                self.gemini, plan.goal, range_minutes, outcomes, comparisons, template
            )
            warnings.extend(extra)
            return analysis
        except (GeminiError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, GeminiError) else "reply did not match the analysis schema"
            warnings.append(f"Gemini analysis unavailable ({reason}); used the deterministic template analysis.")
            return template

    def _dashboard(
        self, intent_id, intent, range_minutes, outcomes, analysis, comparisons, engine, completed_ids, warnings: list[str]
    ) -> dict | None:
        try:
            folder_uid = self.grafana.ensure_folder(dashboards.FOLDER_TITLE)
            if folder_uid is None:
                warnings.append("Could not resolve the 'Intent-Based' folder; dashboard saved in General.")
            payload = dashboards.build_intent_dashboard(
                intent_id=intent_id,
                intent=intent,
                range_minutes=range_minutes,
                outcomes=outcomes,
                analysis=analysis,
                comparisons=comparisons,
                engine=engine,
                completed_ids=completed_ids,
                ds_uid=self.settings.grafana_ds_uid,
                folder_uid=folder_uid,
            )
            reply = self.grafana.upsert_dashboard(payload)
            uid = payload["dashboard"]["uid"]
            if reply.get("uid") and reply["uid"] != uid:
                # Never advertise a link Grafana did not create.
                raise RuntimeError(f"Grafana stored the dashboard under a different uid ({reply['uid']})")
            return {"uid": uid, "url": f"{self.settings.public_grafana_url}/d/{uid}"}
        except (GrafanaUnreachable, RuntimeError) as exc:
            warnings.append(f"Dashboard not created: {exc}")
            return None
