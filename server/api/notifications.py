"""Dashboard notification REST (§8.7.4): panel list, bell badge, read, read-all, ack.

With a database, reads come from the `notifications` table (history and unread state survive a restart);
without one, from `state.notifications`'s in-memory rows (see `server/notify/notifications.py`).
Token-protected like every other router, but never depends on `require_db` — the one deliberate
difference from `server/api/alerts.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.state import get_state

router = APIRouter()


class AckIn(BaseModel):
    therapist_id: str = Field(min_length=1, max_length=64)


class ReadAllIn(BaseModel):
    therapist_id: str | None = Field(default=None, max_length=64)


def _service() -> Any:
    service = get_state().notifications
    if service is None:
        raise HTTPException(status_code=503, detail="notifications service not available")
    return service


@router.get("/notifications")
async def list_notifications(unread: bool = False, limit: int = Query(50, ge=1, le=500),
                             since_id: int | None = Query(None, ge=0),
                             therapist_id: str | None = None) -> dict[str, Any]:
    rows = await _service().list_notifications(therapist_id=therapist_id, unread=unread, since_id=since_id,
                                               limit=limit)
    return {"notifications": rows}


@router.get("/notifications/count")
async def notifications_count(therapist_id: str | None = None) -> dict[str, int]:
    return await _service().count(therapist_id)


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: int) -> dict[str, Any]:
    if not await _service().mark_read(notification_id):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"id": notification_id, "read": True}


@router.post("/notifications/read-all")
async def mark_all_read(body: ReadAllIn | None = None) -> dict[str, int]:
    body = body or ReadAllIn()
    marked = await _service().mark_all_read(body.therapist_id)
    return {"marked_read": marked}


@router.post("/notifications/{notification_id}/ack")
async def ack(notification_id: int, body: AckIn) -> dict[str, Any]:
    row = await _service().ack(notification_id, body.therapist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    return row
