"""POST /retell/webhook: signature-checked Retell events (no bearer token; §9.6).

Polling `call.retrieve` stays the source of truth; this only records what Retell pushes, as a
`webhook_<event>` step on the matching incident when the call id is one of ours.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from server.logging_setup import get_logger
from server.security import verify_retell
from server.state import get_state

log = get_logger("retell")
router = APIRouter()

CALL_EVENTS = ("call_started", "call_ended", "call_analyzed")


@router.post("/retell/webhook")
async def retell_webhook(request: Request) -> dict[str, Any]:
    raw = await verify_retell(request)
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("event")
    call = payload.get("call")
    if event in CALL_EVENTS and isinstance(call, dict):
        notifier = get_state().notifier
        if notifier is not None:
            await notifier.on_webhook(event, call)
        else:
            log.info("retell webhook", extra={"event": event, "call_id": str(call.get("call_id"))[:80]})
    else:
        log.info("retell webhook", extra={"event": event if isinstance(event, str) else None})
    return {"status": "ok"}
