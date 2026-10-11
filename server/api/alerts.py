"""Alerts panel REST (§4.4): incidents grouped by alert_id with every step; therapist resolve."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.api.db_dep import require_db
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state

router = APIRouter()

RESOLVED_STEP = "resolved_by_therapist"


class ResolveIn(BaseModel):
    resolved_by: str = Field(default="therapist", min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


@router.get("/alerts")
async def list_alerts(open: bool = True, db: Database = Depends(require_db)) -> dict[str, Any]:  # noqa: A002
    steps = await queries.alert_steps(db, open_only=open)
    grouped: dict[str, dict[str, Any]] = {}
    for step in steps:
        aid = str(step["alert_id"])
        alert = grouped.setdefault(aid, {"alert_id": aid, "patient_id": step["patient_id"],
                                         "session_id": step["session_id"], "kind": step["kind"],
                                         "started_at": step["time"], "steps": []})
        alert["steps"].append({k: step[k] for k in ("time", "step", "channel", "payload")})
        alert["last_step"], alert["updated_at"] = step["step"], step["time"]
        alert["open"] = step["step"] not in queries.CLOSED_ALERT_STEPS
    alerts = sorted(grouped.values(), key=lambda a: a["updated_at"], reverse=True)
    return {"alerts": alerts}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: uuid.UUID, body: ResolveIn | None = None,
                        db: Database = Depends(require_db)) -> dict[str, Any]:
    body = body or ResolveIn()
    aid = str(alert_id)
    latest = await queries.alert_latest(db, aid)
    if latest is None:
        raise HTTPException(status_code=404, detail="alert not found")
    payload = {"by": body.resolved_by, "note": body.note}
    now = time.time()
    if not await writes.insert_alert_step(db, now, aid, latest["patient_id"],
                                          str(latest["session_id"]) if latest["session_id"] else None,
                                          latest["kind"], RESOLVED_STEP, "dashboard", payload):
        raise HTTPException(status_code=503, detail="database unavailable: resolve not recorded")
    get_state().hub.emit("alert", {"alert_id": aid, "kind": latest["kind"], "step": RESOLVED_STEP,
                                   "patient_id": latest["patient_id"], "message": "Resolved by therapist"})
    return {"alert_id": aid, "step": RESOLVED_STEP, "time": now}
