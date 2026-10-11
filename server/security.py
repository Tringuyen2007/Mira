"""API token (REST + WS), tunnel path filter and Retell webhook signature check (§9.6)."""

from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import HTTPException, Request, WebSocket
from retell import Retell
from starlette.types import ASGIApp, Receive, Scope, Send

from server.config import cfg
from server.logging_setup import get_logger

log = get_logger("security")

WEBHOOK_PATH = "/retell/webhook"
MAX_WEBHOOK_BYTES = 1_000_000


def _token_matches(given: str, expected: str) -> bool:
    if not cfg.MIRA_API_TOKEN:
        return False
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


async def require_token(request: Request) -> None:
    """FastAPI dependency: `Authorization: Bearer <MIRA_API_TOKEN>` or 401."""
    given = request.headers.get("authorization", "")
    if not _token_matches(given, f"Bearer {cfg.MIRA_API_TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})


async def ws_token_ok(ws: WebSocket) -> bool:
    """Check `?token=` before `ws.accept()` (browsers can't set headers on WebSockets)."""
    return _token_matches(ws.query_params.get("token", ""), cfg.MIRA_API_TOKEN)


@lru_cache(maxsize=1)
def _retell_client(api_key: str) -> Retell:
    return Retell(api_key=api_key)


async def verify_retell(request: Request) -> str:
    """Return the raw webhook body if `X-Retell-Signature` is valid, else 401 (always 401 without a key)."""
    api_key = cfg.RETELL_API_KEY
    if not api_key:
        raise HTTPException(status_code=401, detail="webhook disabled")
    body = await _read_capped(request, MAX_WEBHOOK_BYTES)
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=401, detail="invalid signature") from exc
    signature = request.headers.get("x-retell-signature", "")
    if not _retell_client(api_key).verify(raw, api_key=api_key, signature=signature):
        log.warning("retell webhook rejected: bad signature")
        raise HTTPException(status_code=401, detail="invalid signature")
    return raw


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, or 413 as soon as it exceeds `limit` (the webhook is public via the tunnel)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="payload too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def normalize_host(value: str) -> str:
    """Bare lower-case host name: no scheme, path, port or trailing dot ("https://A.b.:443/" → "a.b")."""
    host = value.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if host.startswith("["):  # IPv6 literal, optionally with a port
        host = host[: host.find("]") + 1] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def _request_hosts(raw_headers: list[tuple[bytes, bytes]]) -> set[str]:
    """Every host the request claims: each Host and X-Forwarded-Host value and list entry."""
    hosts: set[str] = set()
    for key, value in raw_headers:
        if key.lower() in (b"host", b"x-forwarded-host"):
            hosts.update(normalize_host(v) for v in value.decode("latin-1").split(",") if v.strip())
    return hosts


class TunnelFilterMiddleware:
    """Requests arriving via the public tunnel host may only reach /retell/webhook; anything else → 403."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        tunnel_host = normalize_host(cfg.PUBLIC_WEBHOOK_HOST or "")
        if scope["type"] not in ("http", "websocket") or not tunnel_host:
            await self.app(scope, receive, send)
            return
        via_tunnel = tunnel_host in _request_hosts(scope.get("headers", []))
        if not via_tunnel or (scope["type"] == "http" and scope["path"] == WEBHOOK_PATH):
            await self.app(scope, receive, send)
            return
        log.warning("tunnel request blocked", extra={"path": scope["path"]})
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"forbidden"}'})
