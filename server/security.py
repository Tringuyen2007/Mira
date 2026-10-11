"""API token (REST + WS), tunnel path filter and Retell webhook signature check (§9.6)."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from fastapi import HTTPException, Request, WebSocket
from retell import Retell
from starlette.types import ASGIApp, Receive, Scope, Send

from server.config import cfg
from server.db.pool import DBError
from server.logging_setup import get_logger

log = get_logger("security")

WEBHOOK_PATH = "/retell/webhook"
MAX_WEBHOOK_BYTES = 1_000_000

PATIENT_TOKEN_PREFIX = "pt_"
TOKEN_CACHE_S = 60.0
TOUCH_INTERVAL_S = 60.0

PrincipalScope = Literal["full", "device", "patient"]


@dataclass(frozen=True)
class Principal:
    """Who a token belongs to. `full` = MIRA_API_TOKEN (dashboard/ops), `device` = the LCD, `patient` = one phone."""

    scope: PrincipalScope
    patient_id: str | None = None

    def owns(self, patient_id: str) -> bool:
        return self.scope == "full" or (self.scope == "patient" and self.patient_id == patient_id)

    def require(self, patient_id: str) -> None:
        if not self.owns(patient_id):
            raise HTTPException(status_code=403, detail="forbidden")


def _token_matches(given: str, expected: str) -> bool:
    if not expected:
        return False
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def new_patient_token() -> str:
    return PATIENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask(token: str) -> str:
    """For logs: never the token, only its tail."""
    return "…" + token[-4:]


# token hash → (Principal, expires_at, last_touched). Positive results only; a revoked token is refused as soon as
# the entry expires (≤ 60 s) or invalidate_token_cache() runs (DELETE /me, device revocation).
_cache: dict[str, tuple[Principal, float, float]] = {}


def invalidate_token_cache() -> None:
    _cache.clear()


async def _patient_principal(token: str) -> Principal | None:
    from server.db import queries, writes  # local import: security is imported by the db layer's callers
    from server.state import get_state

    digest = token_hash(token)
    now = time.monotonic()
    cached = _cache.get(digest)
    if cached is not None and cached[1] > now:
        principal, expires, touched = cached
        if now - touched > TOUCH_INTERVAL_S:
            _cache[digest] = (principal, expires, now)
            writes.fire(writes.touch_patient_device(get_state().db, digest))
        return principal
    db = get_state().db
    if db is None:
        return None
    row = await queries.patient_device(db, digest)  # DBError propagates → 503, never a false 401
    if row is None or row["revoked_at"] is not None:
        return None
    principal = Principal("patient", row["patient_id"])
    _cache[digest] = (principal, now + TOKEN_CACHE_S, now)
    writes.fire(writes.touch_patient_device(db, digest))
    return principal


async def resolve_token(token: str) -> Principal | None:
    """Which principal a bearer/`?token=` value is, or None. Static tokens never touch the database."""
    if not token:
        return None
    if _token_matches(token, cfg.MIRA_API_TOKEN):
        return Principal("full")
    if _token_matches(token, cfg.MIRA_DEVICE_TOKEN):
        return Principal("device")
    if token.startswith(PATIENT_TOKEN_PREFIX):
        return await _patient_principal(token)
    return None


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency for routes patients may call: the full token or a patient token, else 401. The LCD's
    device token is never accepted on REST."""
    principal = await resolve_token(_bearer(request))
    if principal is None or principal.scope == "device":
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return principal


async def require_token(request: Request) -> None:
    """FastAPI dependency: `Authorization: Bearer <MIRA_API_TOKEN>` or 401 (unchanged: full scope only)."""
    if not _token_matches(_bearer(request), cfg.MIRA_API_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})


async def ws_token_ok(ws: WebSocket) -> bool:
    """Check `?token=` before `ws.accept()` (browsers can't set headers on WebSockets). Full token only."""
    return _token_matches(ws.query_params.get("token", ""), cfg.MIRA_API_TOKEN)


async def ws_scope(ws: WebSocket) -> Principal | None:
    """The principal behind `?token=`: full, the LCD device token, a patient's phone, or None. Device scope is
    only honoured on the channels that opt in (see server.main). A database failure while checking a patient
    token returns None (the phone reconnects with backoff)."""
    try:
        return await resolve_token(ws.query_params.get("token", ""))
    except DBError as exc:
        log.warning("ws token check failed (database)", extra={"error": str(exc)})
        return None


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
