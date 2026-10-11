"""Dev-only demo triggers (§4.2). Hidden (404) unless DEMO_TOOLS_ENABLED=1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.config import cfg
from server.logging_setup import get_logger
from server.security import require_token
from server.state import get_state

log = get_logger("demo")
router = APIRouter(dependencies=[Depends(require_token)])


class TriggerFallIn(BaseModel):
    responds_at_s: float | None = Field(default=None, ge=0, le=600)


@router.post("/demo/trigger_fall")
async def trigger_fall(body: TriggerFallIn | None = None) -> dict[str, Any]:
    if not cfg.DEMO_TOOLS_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    state = get_state()
    session = state.sessions.current
    if session is None:
        raise HTTPException(status_code=409, detail="no active session")
    responds_at_s = body.responds_at_s if body else None
    await state.bridge.trigger_fall(responds_at_s)
    log.warning("demo fall triggered", extra={"session_id": session.session_id, "responds_at_s": responds_at_s})
    return {"status": "triggered", "session_id": session.session_id, "responds_at_s": responds_at_s}
