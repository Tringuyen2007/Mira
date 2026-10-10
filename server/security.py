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
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        raw = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=401, detail="invalid signature") from exc
    signature = request.headers.get("x-retell-signature", "")
    if not _retell_client(api_key).verify(raw, api_key=api_key, signature=signature):
        log.warning("retell webhook rejected: bad signature")
        raise HTTPException(status_code=401, detail="invalid signature")
    return raw


def _host_of(value: str) -> str:
    return value.split(",")[0].strip().rsplit(":", 1)[0].lower() if value else ""


class TunnelFilterMiddleware:
    """Requests arriving via the public tunnel host may only reach /retell/webhook; anything else → 403."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        tunnel_host = (cfg.PUBLIC_WEBHOOK_HOST or "").lower()
        if scope["type"] not in ("http", "websocket") or not tunnel_host:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        hosts = {_host_of(headers.get("host", "")), _host_of(headers.get("x-forwarded-host", ""))}
        if tunnel_host not in hosts or (scope["type"] == "http" and scope["path"] == WEBHOOK_PATH):
            await self.app(scope, receive, send)
            return
        log.warning("tunnel request blocked", extra={"path": scope["path"]})
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"forbidden"}'})
