"""Sessions REST (§4.4): start/stop work without a database; reads need it."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server.api.db_dep import require_db
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state

router = APIRouter()

AI_DRAFT_LABEL = "AI draft — review before use"


class SessionIn(BaseModel):
    patient_id: str = Field(default_factory=lambda: cfg.MOCK_PATIENT_ID, min_length=1, max_length=64)
    exercise_id: str = Field(default="squat", min_length=1, max_length=64)


@router.post("/sessions")
async def start_session(body: SessionIn | None = None) -> dict[str, Any]:
    body = body or SessionIn()
    registry = get_state().sessions
    if registry.current is not None:
        await registry.stop(registry.current.session_id)
    session = await registry.start(body.patient_id, body.exercise_id)
    return {"session_id": session.session_id, "patient_id": session.patient_id,
            "exercise_id": session.exercise_id, "started_at": session.started_at,
            "first_name": session.profile.first_name}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: uuid.UUID) -> dict[str, Any]:
    sid = str(session_id)
    state = get_state()
    if state.sessions.get(sid) is not None:
        await state.sessions.stop(sid)
        return {"session_id": sid, "stopped": True}
    db = require_db()
    if await queries.get_session(db, sid) is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not await writes.end_session(db, sid, None):
        raise HTTPException(status_code=503, detail="database unavailable: could not stop the session")
    return {"session_id": sid, "stopped": True}


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID, db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    session = await queries.get_session(db, sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    reps, flags, tw, per_set_tw = await asyncio.gather(
        queries.session_reps(db, sid), queries.session_form_events(db, sid),
        queries.session_tw_load(db, sid), queries.session_tw_load_per_set(db, sid))
    reps_by_set: dict[Any, list[dict[str, Any]]] = {}
    for rep in reps:
        reps_by_set.setdefault(rep["set_no"], []).append(rep)
    per_set = [{"set_no": row["set_no"], "tw_load_left_pct": row["tw_load_left_pct"],
                "reps": len(reps_by_set.get(row["set_no"], [])),
                "good_reps": sum(1 for r in reps_by_set.get(row["set_no"], []) if (r["quality"] or 0) >= 1.0)}
               for row in per_set_tw]
    return {
        "session": session,
        "reps": reps,
        "flags": flags,
        "summary": {
            "reps_total": len(reps),
            "good_reps": sum(1 for r in reps if (r["quality"] or 0) >= 1.0),
            "flag_counts": dict(Counter(f["rule_id"] for f in flags)),
            "ai_summary": session.get("ai_summary"),
            "ai_note": session.get("ai_note"),
            "ai_note_label": AI_DRAFT_LABEL,
        },
        "tw_load_left_pct": tw,
        "per_set": per_set,
    }


@router.get("/sessions/{session_id}/frames")
async def get_frames(session_id: uuid.UUID, downsample: int = Query(default=300, ge=0, le=5000),
                     t0: datetime | None = None, t1: datetime | None = None,
                     db: Database = Depends(require_db)) -> dict[str, Any]:
    """downsample > 0: knee curve with lttb (charts); downsample=0: full-rate frames between t0 and t1 (replay)."""
    sid = str(session_id)
    if downsample > 0:
        points = await queries.session_knee_lttb(db, sid, downsample)
        return {"session_id": sid, "mode": "lttb", "series": "knee_r", "points": points}
    frames = await queries.session_frames(db, sid, t0, t1)
    return {"session_id": sid, "mode": "full", "count": len(frames), "frames": frames}


@router.post("/sessions/{session_id}/share")
async def share_session(session_id: uuid.UUID, db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    if await queries.get_session(db, sid) is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not await writes.set_session_shared(db, sid, True):
        raise HTTPException(status_code=503, detail="database unavailable: could not share the session")
    return {"session_id": sid, "shared": True}
