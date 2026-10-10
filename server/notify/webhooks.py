"""POST /retell/webhook: signature-checked Retell events (no bearer token; §9.6).

Polling `call.retrieve` stays the source of truth; this only records what Retell pushes.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from server.logging_setup import get_logger
from server.security import verify_retell

log = get_logger("retell")
router = APIRouter()


@router.post("/retell/webhook")
async def retell_webhook(request: Request) -> dict[str, Any]:
    raw = await verify_retell(request)
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    event = payload.get("event") if isinstance(payload, dict) else None
    log.info("retell webhook", extra={"event": event if isinstance(event, str) else None})
    return {"status": "ok"}
