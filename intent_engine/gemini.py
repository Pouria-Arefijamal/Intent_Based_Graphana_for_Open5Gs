"""Minimal Gemini REST client (generateContent) with model fallback.

Security properties:
* the API key is passed in the `x-goog-api-key` header only (never in the URL, body or logs);
* error messages are assembled from status codes / exception class names only, and are passed
  through `redact`, so the key can never appear in an exception string.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .metrics import LLM_ERRORS

log = logging.getLogger("intent_engine.gemini")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RETRYABLE_STATUS = {408, 429}


class GeminiError(Exception):
    """All configured models failed (or Gemini is not configured). Never contains the API key."""

    def __init__(self, message: str, attempts: list[str] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str


class GeminiClient:
    def __init__(
        self,
        api_key: str | None,
        models: tuple[str, ...] | list[str],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 45.0,
        backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = BASE_URL,
    ) -> None:
        self._key = api_key or None
        self.models = tuple(m for m in models if _MODEL_RE.match(m))
        self._backoff = backoff
        self._sleep = sleep
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __repr__(self) -> str:  # never show the key
        return f"GeminiClient(models={self.models!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._key) and bool(self.models)

    def redact(self, text: str) -> str:
        if self._key and self._key in text:
            text = text.replace(self._key, "***")
        return text

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> GeminiResult:
        if not self.configured:
            raise GeminiError("Gemini is not configured (GEMINI_API_KEY not set)")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "maxOutputTokens": max_output_tokens,
            },
        }
        headers = {"x-goog-api-key": self._key or "", "content-type": "application/json"}
        attempts: list[str] = []
        for idx, model in enumerate(self.models):
            reason, transient = self._try_model(model, body, headers)
            if isinstance(reason, GeminiResult):
                return reason
            attempts.append(f"{model}: {reason}")
            LLM_ERRORS.labels(model=model).inc()
            log.warning("gemini model %s failed: %s", model, reason)
            if transient and idx < len(self.models) - 1:
                self._sleep(self._backoff * (idx + 1))
        raise GeminiError(self.redact("all Gemini models failed: " + "; ".join(attempts)), attempts)

    # returns (GeminiResult | failure-reason, transient?)
    def _try_model(self, model: str, body: dict, headers: dict) -> tuple[GeminiResult | str, bool]:
        url = f"{self._base_url}/{model}:generateContent"
        try:
            resp = self._client.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            return "timeout", True
        except httpx.HTTPError as exc:
            return f"transport error ({type(exc).__name__})", True
        if resp.status_code != 200:
            status = ""
            try:
                err = resp.json().get("error", {})
                if isinstance(err, dict) and isinstance(err.get("status"), str):
                    status = " (" + re.sub(r"[^A-Z_]", "", err["status"].upper())[:40] + ")"
            except (ValueError, AttributeError):
                pass
            transient = resp.status_code in _RETRYABLE_STATUS or resp.status_code >= 500
            return f"HTTP {resp.status_code}{status}", transient
        try:
            text = extract_text(resp.json())
        except (ValueError, KeyError, TypeError, IndexError):
            return "malformed response", False
        if not text.strip():
            return "empty response", False
        return GeminiResult(text=text, model=model), False


def extract_text(payload: dict) -> str:
    """Concatenate the text parts of the first candidate, ignoring parts flagged `thought: true`."""
    parts = payload["candidates"][0]["content"]["parts"]
    chunks = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)
