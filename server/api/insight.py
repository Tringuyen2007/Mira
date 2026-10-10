"""POST /insight/run: on-demand Insight pass (dev / demo; token-protected)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from server.api.db_dep import NO_DB_DETAIL
from server.state import get_state

router = APIRouter()


@router.post("/insight/run")
async def insight_run() -> dict[str, Any]:
    scheduler = get_state().scheduler
    if scheduler is None:
        raise HTTPException(status_code=503, detail=NO_DB_DETAIL)
    result = await scheduler.run_insight_now()
    if result is None:
        raise HTTPException(status_code=500, detail=scheduler.jobs["insight"].last_error or "insight run failed")
    return result
