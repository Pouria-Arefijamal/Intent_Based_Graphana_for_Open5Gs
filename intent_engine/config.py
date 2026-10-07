"""Runtime configuration, read only from environment variables (SPEC section 4)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

DEFAULT_MODELS = "gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-flash-latest"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def parse_models(raw: str) -> tuple[str, ...]:
    """Split a comma list of model ids; drop anything that is not a plain model id."""
    models = []
    for part in raw.split(","):
        part = part.strip()
        if part and _MODEL_RE.match(part) and part not in models:
            models.append(part)
    return tuple(models)


@dataclass(frozen=True)
class Settings:
    # Secrets are excluded from repr so they can never reach a log line by accident.
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_models: tuple[str, ...] = parse_models(DEFAULT_MODELS)
    grafana_url: str = "http://grafana:3000"
    grafana_user: str = "admin"
    grafana_password: str = field(default="admin", repr=False)
    grafana_token: str | None = field(default=None, repr=False)
    grafana_ds_uid: str = "ibg-prometheus"
    public_grafana_url: str = "http://localhost:3000"
    public_intent_url: str = "http://localhost:8088"
    prom_url: str = "http://prometheus:9090"
    port: int = 8088
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env

        def get(name: str, default: str) -> str:
            value = e.get(name, "").strip()
            return value or default

        key = e.get("GEMINI_API_KEY", "").strip() or None
        token = e.get("GRAFANA_TOKEN", "").strip() or None
        models = parse_models(get("GEMINI_MODELS", DEFAULT_MODELS)) or parse_models(DEFAULT_MODELS)
        try:
            port = int(get("INTENT_PORT", "8088"))
        except ValueError:
            port = 8088
        return cls(
            gemini_api_key=key,
            gemini_models=models,
            grafana_url=get("GRAFANA_URL", "http://grafana:3000").rstrip("/"),
            grafana_user=get("GRAFANA_USER", "admin"),
            grafana_password=get("GRAFANA_PASSWORD", "admin"),
            grafana_token=token,
            grafana_ds_uid=get("GRAFANA_DS_UID", "ibg-prometheus"),
            public_grafana_url=get("PUBLIC_GRAFANA_URL", "http://localhost:3000").rstrip("/"),
            public_intent_url=get("PUBLIC_INTENT_URL", "http://localhost:8088").rstrip("/"),
            prom_url=get("PROM_URL", "http://prometheus:9090").rstrip("/"),
            port=port,
            allowed_hosts=tuple(h.strip() for h in e.get("ALLOWED_HOSTS", "").split(",") if h.strip()),
        )
