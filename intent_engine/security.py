"""Request-level protections: Host header allow-list (DNS-rebinding) and request body size cap."""

from __future__ import annotations

import json
from typing import Iterable
from urllib.parse import urlparse

MAX_BODY_BYTES = 16 * 1024
BASE_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]", "intent-engine")


def host_without_port(host_header: str) -> str:
    """'Intent-Engine:8088' -> 'intent-engine'; '[::1]:8088' -> '[::1]'."""
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[: end + 1] if end != -1 else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def build_allowed_hosts(extra: Iterable[str], public_urls: Iterable[str] = ()) -> tuple[str, ...]:
    hosts = [h.lower() for h in BASE_ALLOWED_HOSTS]
    hosts += [e.strip().lower() for e in extra if e and e.strip()]
    for url in public_urls:
        name = urlparse(url).hostname
        if name:
            hosts.append(f"[{name}]" if ":" in name else name.lower())
    return tuple(dict.fromkeys(hosts))


async def _plain(send, status: int, text: str) -> None:
    body = json.dumps({"detail": text}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class HostGuardMiddleware:
    """Rejects requests whose Host header is not allow-listed (400), like Starlette's TrustedHostMiddleware
    but IPv6-literal aware. '*' in the list disables the check."""

    def __init__(self, app, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed_hosts)
        self.any = "*" in self.allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not self.any:
            host = ""
            for k, v in scope.get("headers", []):
                if k == b"host":
                    host = v.decode("latin-1")
                    break
            if host_without_port(host) not in self.allowed:
                if scope["type"] == "http":
                    await _plain(send, 400, "Invalid host header")
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Answers 413 when a request body exceeds `limit` bytes (checked via Content-Length and, for chunked
    bodies, while the app reads the stream)."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    too_big = int(v) > self.limit
                except ValueError:
                    too_big = True
                if too_big:
                    await _plain(send, 413, f"request body larger than {self.limit} bytes")
                    return
        state = {"total": 0, "over": False, "replied": False}

        async def limited_receive():
            msg = await receive()
            if msg["type"] == "http.request":
                state["total"] += len(msg.get("body", b""))
                if state["total"] > self.limit:
                    state["over"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return msg

        async def guarded_send(msg):
            if state["over"]:
                if not state["replied"]:
                    state["replied"] = True
                    await _plain(send, 413, f"request body larger than {self.limit} bytes")
                return
            await send(msg)

        await self.app(scope, limited_receive, guarded_send)
