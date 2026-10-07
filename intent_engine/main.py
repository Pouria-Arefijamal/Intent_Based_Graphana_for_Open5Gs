"""FastAPI application: routes of SPEC section 4."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .catalog import Catalog, default_catalog
from .config import Settings
from .dashboards import start_console_task
from .gemini import GeminiClient
from .grafana import GrafanaClient
from .guardrails import MetricAllowList
from .models import IntentRequest
from .security import BodyLimitMiddleware, HostGuardMiddleware, build_allowed_hosts
from .service import IntentService, ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    settings: Settings | None = None,
    *,
    catalog: Catalog | None = None,
    grafana_transport: httpx.BaseTransport | None = None,
    gemini_transport: httpx.BaseTransport | None = None,
    prom_transport: httpx.BaseTransport | None = None,
    gemini_sleep=None,
    start_background: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    catalog = catalog or default_catalog()
    grafana = GrafanaClient(settings, transport=grafana_transport)
    gemini_kwargs = {"transport": gemini_transport}
    if gemini_sleep is not None:
        gemini_kwargs["sleep"] = gemini_sleep
    gemini = GeminiClient(settings.gemini_api_key, settings.gemini_models, **gemini_kwargs)
    allowlist = MetricAllowList(settings.prom_url, catalog.metric_names, transport=prom_transport)
    service = IntentService(settings, catalog, grafana, gemini, allowlist)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background:
            start_console_task(grafana, settings.public_intent_url)
        yield

    app = FastAPI(title="Intent Engine", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.allowed_hosts = build_allowed_hosts(
        settings.allowed_hosts, (settings.public_intent_url,)
    )
    # added last = outermost: host check first, then the body size cap
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(HostGuardMiddleware, allowed_hosts=app.state.allowed_hosts)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "gemini_configured": gemini.configured,
            "grafana_reachable": grafana.reachable(),
            "prometheus_reachable": allowlist.reachable(),
        }

    @app.get("/api/catalog")
    def api_catalog() -> dict:
        return {
            "recipes": [r.public() for r in catalog.recipes.values()],
            "metrics": [dict(m) for m in catalog.metrics],
        }

    @app.post("/api/intent")
    def api_intent(req: IntentRequest) -> dict:
        try:
            return service.run(req)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from None

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
