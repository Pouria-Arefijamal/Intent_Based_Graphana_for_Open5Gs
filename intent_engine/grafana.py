"""Grafana HTTP API client. All PromQL goes through Grafana's datasource proxy, never to Prometheus."""

from __future__ import annotations

import math
import re
import threading
from urllib.parse import quote

import httpx

from .config import Settings
from .stats import Series

MAX_SERIES = 12
ERROR_TEXT_LIMIT = 300


class GrafanaUnreachable(Exception):
    """Grafana itself cannot be reached (transport failure)."""


class GrafanaAuthError(Exception):
    """Grafana rejected the engine's credentials."""


class PromQueryError(Exception):
    """The datasource rejected or failed a query."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message

    @property
    def is_client_error(self) -> bool:
        """4xx from Prometheus (bad PromQL, too many points...): worth one LLM repair round."""
        return 400 <= self.status < 500 and self.status not in (401, 403)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:ERROR_TEXT_LIMIT]


class GrafanaClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._base = settings.grafana_url.rstrip("/")
        self._ds_uid = settings.grafana_ds_uid
        headers = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if settings.grafana_token:
            headers["Authorization"] = f"Bearer {settings.grafana_token}"
        else:
            auth = httpx.BasicAuth(settings.grafana_user, settings.grafana_password)
        self._client = httpx.Client(
            base_url=self._base, headers=headers, auth=auth, transport=transport, timeout=timeout
        )
        self._folder_cache: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ health
    def reachable(self) -> bool:
        try:
            return self._client.get("/api/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ queries
    def query_range(self, promql: str, start: float, end: float, step: int) -> list[Series]:
        """Run a range query via /api/datasources/proxy/uid/<uid>/api/v1/query_range."""
        path = f"/api/datasources/proxy/uid/{quote(self._ds_uid, safe='')}/api/v1/query_range"
        params = {"query": promql, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step)}
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise GrafanaUnreachable(f"Grafana is unreachable ({type(exc).__name__})") from None
        if resp.status_code in (401, 403):
            raise GrafanaAuthError(f"Grafana rejected the engine credentials (HTTP {resp.status_code})")
        body = None
        try:
            body = resp.json()
        except ValueError:
            pass
        if resp.status_code in (502, 503, 504):
            raise PromQueryError(resp.status_code, "Prometheus is unreachable through the Grafana datasource proxy")
        if resp.status_code != 200 or not isinstance(body, dict) or body.get("status") != "success":
            msg = ""
            if isinstance(body, dict):
                msg = str(body.get("error") or body.get("message") or "")
            status = resp.status_code if resp.status_code != 200 else 400
            raise PromQueryError(status, _clean(msg) or f"query failed with HTTP {resp.status_code}")
        data = body.get("data") or {}
        if data.get("resultType") != "matrix":
            raise PromQueryError(400, f"unexpected result type {data.get('resultType')!r}; expected a matrix")
        return parse_matrix(data.get("result") or [])

    # ------------------------------------------------------------------ dashboards
    def ensure_folder(self, title: str) -> str | None:
        """Return the uid of folder `title`, creating it if needed. None if it cannot be resolved."""
        with self._lock:
            if title in self._folder_cache:
                return self._folder_cache[title]
        uid = self._find_folder(title)
        if uid is None:
            try:
                resp = self._client.post("/api/folders", json={"title": title})
            except httpx.HTTPError as exc:
                raise GrafanaUnreachable(f"Grafana is unreachable ({type(exc).__name__})") from None
            if resp.status_code in (200, 201):
                uid = resp.json().get("uid")
            else:  # 409/412: created concurrently; look again
                uid = self._find_folder(title)
        if uid:
            with self._lock:
                self._folder_cache[title] = uid
        return uid

    def _find_folder(self, title: str) -> str | None:
        try:
            resp = self._client.get("/api/folders", params={"limit": 1000})
        except httpx.HTTPError as exc:
            raise GrafanaUnreachable(f"Grafana is unreachable ({type(exc).__name__})") from None
        if resp.status_code != 200:
            return None
        for folder in resp.json():
            if isinstance(folder, dict) and folder.get("title") == title:
                return folder.get("uid")
        return None

    def upsert_dashboard(self, payload: dict) -> dict:
        """POST /api/dashboards/db (payload must carry overwrite=true). Returns Grafana's JSON reply."""
        try:
            resp = self._client.post("/api/dashboards/db", json=payload)
        except httpx.HTTPError as exc:
            raise GrafanaUnreachable(f"Grafana is unreachable ({type(exc).__name__})") from None
        if resp.status_code not in (200, 201):
            detail = ""
            try:
                detail = str(resp.json().get("message", ""))
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f"Grafana refused the dashboard (HTTP {resp.status_code}) {_clean(detail)}".strip())
        return resp.json()


def parse_matrix(result: list) -> list[Series]:
    """Parse a Prometheus matrix result; drop NaN/Inf samples; keep at most MAX_SERIES series.

    When more than MAX_SERIES series come back, the ones with the highest peak are kept.
    """
    series: list[Series] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        labels = {str(k): str(v) for k, v in (item.get("metric") or {}).items()}
        pts = []
        for pair in item.get("values") or []:
            try:
                t = float(pair[0])
                v = float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(v):
                pts.append((t, v))
        if pts:
            series.append(Series(labels=labels, points=pts))
    if len(series) > MAX_SERIES:
        series.sort(key=lambda s: max(v for _, v in s.points), reverse=True)
        series = series[:MAX_SERIES]
    return series
