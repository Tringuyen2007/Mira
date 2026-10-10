"""Liveness, runtime stats and Data Guard results."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from fastapi import APIRouter, Depends

from server.logging_setup import get_logger
from server.security import require_token
from server.state import get_state

log = get_logger("api")
router = APIRouter()

PROVIDER_TIMEOUT_S = 2.0
_started_at = time.time()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "time": round(time.time(), 3)}


@router.get("/stats", dependencies=[Depends(require_token)])
async def stats() -> dict[str, Any]:
    """{name: provider()} for every provider registered in AppState.stat_providers."""
    state = get_state()
    out: dict[str, Any] = {"uptime_s": round(time.time() - _started_at, 1)}
    for name, provider in list(state.stat_providers.items()):
        try:
            value = provider()
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, PROVIDER_TIMEOUT_S)
            out[name] = value
        except Exception as exc:  # noqa: BLE001 - one broken provider must not hide the rest
            log.warning("stat provider failed", extra={"provider": name, "error": repr(exc)})
            out[name] = {"error": type(exc).__name__}
    return out


@router.get("/health/data", dependencies=[Depends(require_token)])
async def health_data() -> dict[str, Any]:
    return {"status": "unavailable", "checks": []}
