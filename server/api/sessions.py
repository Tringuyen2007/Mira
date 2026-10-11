"""Sessions REST (§4.4): start/stop work without a database; reads need it."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from server import security
from server.ai.validators import AI_DRAFT_LABEL, label_note
from server.api.db_dep import require_db
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database
from server.exercises import display_name
from server.state import get_state

router = APIRouter()


class SessionIn(BaseModel):
    patient_id: str = Field(default_factory=lambda: cfg.MOCK_PATIENT_ID, min_length=1, max_length=64)
    exercise_id: str = Field(default="squat", min_length=1, max_length=64)


class CheckinIn(BaseModel):
    pain_post: int | None = Field(default=None, ge=0, le=10)
    effort: int | None = Field(default=None, ge=1, le=10)
    note: str | None = Field(default=None, max_length=1000)


async def _owned_session(db: Database, sid: str, principal: security.Principal) -> dict[str, Any]:
    session = await queries.get_session(db, sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    principal.require(session["patient_id"])
    return session


@router.post("/sessions")
async def start_session(body: SessionIn | None = None,
                        principal: security.Principal = Depends(security.require_principal)) -> dict[str, Any]:
    body = body or SessionIn()
    state = get_state()
    if principal.scope == "patient":
        assert principal.patient_id is not None
        body.patient_id = principal.patient_id
        if state.db is not None:  # a phone may only start exercises on its plan
            active = {rx["exercise_id"] for rx in await queries.prescriptions(state.db, body.patient_id)}
            if body.exercise_id not in active:
                raise HTTPException(status_code=422, detail=f"{body.exercise_id!r} is not in your plan")
        current = state.sessions.current
        if current is not None and current.patient_id != body.patient_id:
            raise HTTPException(status_code=409, detail="another patient's session is running")
    registry = state.sessions
    if registry.current is not None:
        await registry.stop(registry.current.session_id)
    session = await registry.start(body.patient_id, body.exercise_id)
    return {"session_id": session.session_id, "patient_id": session.patient_id,
            "exercise_id": session.exercise_id, "started_at": session.started_at,
            "first_name": session.profile.first_name}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: uuid.UUID,
                       principal: security.Principal = Depends(security.require_principal)) -> dict[str, Any]:
    sid = str(session_id)
    state = get_state()
    live = state.sessions.get(sid)
    if live is not None:
        principal.require(live.patient_id)
        await state.sessions.stop(sid)
        return {"session_id": sid, "stopped": True}
    db = require_db()
    await _owned_session(db, sid, principal)
    if not await writes.end_session(db, sid, None):
        raise HTTPException(status_code=503, detail="database unavailable: could not stop the session")
    return {"session_id": sid, "stopped": True}


@router.post("/sessions/{session_id}/checkin")
async def checkin_session(session_id: uuid.UUID, body: CheckinIn,
                          principal: security.Principal = Depends(security.require_principal),
                          db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    await _owned_session(db, sid, principal)
    await writes.record_session_feedback(db, sid, pain_post=body.pain_post, effort=body.effort, note=body.note)
    if body.pain_post is not None and body.pain_post >= 7:
        get_state().bridge.inject({"type": "command", "name": "pain",
                                   "payload": {"level": body.pain_post, "when": "post"}, "session_id": sid})
    return {"session_id": sid, "pain_post": body.pain_post, "effort": body.effort,
            "note_saved": body.note is not None}


@router.get("/sessions/{session_id}")
async def get_session(session_id: uuid.UUID,
                      principal: security.Principal = Depends(security.require_principal),
                      db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    session = await _owned_session(db, sid, principal)
    reps, flags, tw, per_set_tw, analysis = await asyncio.gather(
        queries.session_reps(db, sid), queries.session_form_events(db, sid),
        queries.session_tw_load(db, sid), queries.session_tw_load_per_set(db, sid),
        queries.session_analysis(db, sid))
    reps_by_set: dict[Any, list[dict[str, Any]]] = {}
    for rep in reps:
        reps_by_set.setdefault(rep["set_no"], []).append(rep)
    per_set = [{"set_no": row["set_no"], "tw_load_left_pct": row["tw_load_left_pct"],
                "reps": len(reps_by_set.get(row["set_no"], [])),
                "good_reps": sum(1 for r in reps_by_set.get(row["set_no"], []) if (r["quality"] or 0) >= 1.0)}
               for row in per_set_tw]
    if analysis is not None:
        analysis = {**analysis, "note": label_note(analysis["note"])}
    return {
        "session": session,
        "exercise_name": display_name(session["exercise_id"]),
        "analysis": analysis,
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


@router.get("/sessions/{session_id}/frames", response_model=None)
async def get_frames(session_id: uuid.UUID, downsample: int = Query(default=300, ge=0, le=5000),
                     t0: datetime | None = None, t1: datetime | None = None,
                     principal: security.Principal = Depends(security.require_principal),
                     db: Database = Depends(require_db)) -> dict[str, Any] | Response:
    """downsample > 0: knee curve with lttb (charts); downsample=0: one page of full-rate frames between t0 and t1
    (replay). A full page has `next_t0`: request again with t0=next_t0 for the rest."""
    sid = str(session_id)
    await _owned_session(db, sid, principal)
    if downsample > 0:
        points = await queries.session_knee_lttb(db, sid, downsample)
        return {"session_id": sid, "mode": "lttb", "series": "knee_r", "points": points}
    page = await queries.session_frames(db, sid, t0, t1)
    # Thousands of frames × 99 floats: decode, reshape and encode off the event loop (pose/live streams stay live).
    body = await asyncio.to_thread(render_frames, sid, page)
    return Response(body, media_type="application/json")


def render_frames(session_id: str, page: dict[str, Any]) -> bytes:
    next_t0 = page["next_t0"]
    return json.dumps({"session_id": session_id, "mode": "full", "count": page["count"],
                       "next_t0": next_t0.isoformat() if next_t0 else None,
                       "frames": queries.frames_from_json(page["frames_json"])}, separators=(",", ":")).encode()


@router.post("/sessions/{session_id}/share")
async def share_session(session_id: uuid.UUID,
                        principal: security.Principal = Depends(security.require_principal),
                        db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    await _owned_session(db, sid, principal)
    if not await writes.set_session_shared(db, sid, True):
        raise HTTPException(status_code=503, detail="database unavailable: could not share the session")
    return {"session_id": sid, "shared": True}
