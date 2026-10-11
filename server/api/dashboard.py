"""Therapist dashboard REST (dashboard spec 2026-10-11, Task 8): `GET /caseload` and
`GET /patients/{id}/memory` ("Therapist Memory"). Task 11 adds the progress-note approval routes:
`POST /sessions/{id}/progress-note` (approve the AI-drafted note, optionally edited) and
`GET /sessions/{id}/progress-note` (read the latest approval back). The final review adds
`GET /patients/{id}/session-history` (finished sessions for the time-lapse date pickers; named so it can't
collide with wire-tri's `GET /patients/{id}/sessions`, which is capped at 100 and skips simulated sessions). Pure triage logic lives in
server.dashboard_triage; this module only assembles signals from the database and the live session
snapshot, and formats the response."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

from server.api.db_dep import require_db
from server.dashboard_triage import Signals, triage
from server.db import dashboard_queries, queries, writes
from server.db.pool import Database
from server.state import get_state
from server.watch_limits import apply_hr_limit_change

router = APIRouter()
log = logging.getLogger(__name__)

# Same shape the dashboard uses for every id (query or path): letters, digits, underscore, hyphen.
ID_RE = r"^[A-Za-z0-9_-]{1,64}$"
IDLE_PHASES = frozenset({"idle", "done"})


@router.get("/live/media")
async def live_media() -> dict[str, Any]:
    """Return the current session's therapist video endpoint, never a recording URL.

    This router is mounted with the full dashboard-token dependency. MediaMTX must separately validate that
    same bearer token before accepting WHEP; see docs/vision-media-contract.md.
    """
    from server.config import cfg

    session = get_state().sessions.current
    if session is None or not cfg.MIRA_WHEP_URL:
        raise HTTPException(status_code=404, detail="live camera video unavailable")
    return {"session_id": session.session_id,
            "whep_url": cfg.MIRA_WHEP_URL.replace("{session_id}", session.session_id)}


def _signals(row: dict[str, Any], live_now: bool) -> Signals:
    return Signals(
        open_alert_kind=row["open_alert_kind"],
        last_pain_post=row["last_pain_post"],
        prev_pain_post=row["prev_pain_post"],
        last_effort=list(row["last_effort"] or []),
        flagged_reps_last=int(row["flagged_reps_last"] or 0),
        flagged_reps_prev=int(row["flagged_reps_prev"] or 0),
        reps_last=int(row["reps_last"] or 0),
        top_flag_last=row["top_flag_last"],
        memory=list(row["memory"] or []),
        missed_sessions=int(row["missed_sessions"] or 0),
        last_session_at=row["last_session_at"],
        live_now=live_now,
        affected_side=row["affected_side"],
    )


def _week(row: dict[str, Any]) -> int:
    first_at = row["first_session_at"]
    if first_at is None:
        return 1
    days = (datetime.now(UTC) - first_at).days
    return days // 7 + 1


def _live_now(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The current `/ws/live` session, if any, past idle/done, and only when that patient is in `rows`
    (this therapist's caseload) — otherwise None (I1). The name comes from the matching row, which also
    avoids an extra `queries.get_patient` round trip."""
    state = get_state()
    session = state.sessions.current
    live = state.bridge.live
    if session is None or (live.phase in IDLE_PHASES and not live.streaming):
        return None  # idle AND no frames: nothing to watch; a streaming-but-measuring camera is live
    row = next((r for r in rows if r["id"] == session.patient_id), None)
    if row is None:
        return None
    return {"patient_id": row["id"], "name": row["name"], "exercise_name": live.exercise_name,
            "set": live.set, "sets_target": live.sets_target, "rep": live.rep}


def _last_signal_at(row: dict[str, Any]) -> datetime | None:
    """The most recent of the last session time and any memory event time (Minor 10: the secondary sort
    key should be "last signal time", not just the last session). Memory event times come back from jsonb
    as ISO strings, not datetimes."""
    times = [row["last_session_at"]] if row["last_session_at"] else []
    for event in row["memory"] or []:
        raw = event.get("time")
        if isinstance(raw, datetime):
            times.append(raw)
        elif isinstance(raw, str):
            times.append(datetime.fromisoformat(raw))
    return max(times) if times else None


def _sort_key(patient: dict[str, Any]) -> tuple[int, float]:
    last_at = patient["last_signal_at"]
    return (patient["severity"], last_at.timestamp() if last_at else 0.0)


@router.get("/caseload")
async def caseload(therapist_id: str = Query(pattern=ID_RE, max_length=64),
                   db: Database = Depends(require_db)) -> dict[str, Any]:
    rows = await dashboard_queries.caseload_signals(db, therapist_id)
    live = _live_now(rows)
    live_patient_id = live["patient_id"] if live else None

    patients: list[dict[str, Any]] = []
    for row in rows:
        is_live = row["id"] == live_patient_id
        t = triage(_signals(row, is_live))
        patients.append({
            "id": row["id"], "name": row["name"], "age": row["age"], "condition": row["condition"],
            "affected_side": row["affected_side"], "week": _week(row), "simulated": bool(row["simulated"]),
            "bucket": t.bucket, "severity": t.severity, "kind": t.kind, "reason": t.reason,
            "evidence": t.evidence, "last_session_at": row["last_session_at"],
            "last_session_id": str(row["last_session_id"]) if row["last_session_id"] else None,
            "live_now": is_live, "last_signal_at": _last_signal_at(row),
        })
    patients.sort(key=_sort_key, reverse=True)
    for p in patients:
        del p["last_signal_at"]

    counts = {"all": len(patients), "needs_you": 0, "watch": 0, "on_track": 0}
    for p in patients:
        counts[p["bucket"]] += 1

    sessions_done = await dashboard_queries.sessions_done_today(db, [r["id"] for r in rows])
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {"therapist_id": therapist_id, "generated_at": generated_at,
            "today": {"sessions_done": sessions_done, "live_now": live}, "counts": counts, "patients": patients}


@router.get("/patients/{patient_id}/memory")
async def patient_memory(patient_id: str = Path(pattern=ID_RE, max_length=64),
                         days: int = Query(default=21, ge=1, le=90),
                         db: Database = Depends(require_db)) -> dict[str, Any]:
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    events = await dashboard_queries.memory_events(db, patient_id, days)
    return {"patient_id": patient_id, "days": days, "events": events}


@router.get("/patients/{patient_id}/session-history")
async def patient_session_history(
        patient_id: str = Path(pattern=ID_RE, max_length=64),
        exercise_id: str | None = Query(default=None, pattern=ID_RE, max_length=64),
        limit: int = Query(default=200, ge=1, le=dashboard_queries.SESSION_HISTORY_LIMIT_MAX),
        db: Database = Depends(require_db)) -> dict[str, Any]:
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    sessions = await dashboard_queries.session_history(db, patient_id, exercise_id, limit)
    return {"patient_id": patient_id, "exercise_id": exercise_id, "sessions": sessions}


# ---- progress-note approval (Task 11) --------------------------------------------------------------------

class ProgressNoteIn(BaseModel):
    therapist_id: str = Field(pattern=ID_RE, max_length=64)
    text: str = Field(max_length=8000)  # raw cap before stripping; the 1-4000 rule below is the real one

    @field_validator("text")
    @classmethod
    def _stripped_1_to_4000(cls, value: str) -> str:
        stripped = value.strip()
        if not 1 <= len(stripped) <= 4000:
            raise ValueError("text must be 1-4000 characters after stripping whitespace")
        return stripped


async def _owned_session(db: Database, session_id: str, therapist_id: str) -> dict[str, Any]:
    """The session, 404 if unknown, 403 if its patient belongs to a different therapist (proposals.py's
    pattern: an unassigned patient — therapist_id None — is not a mismatch)."""
    session = await queries.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    patient = await queries.get_patient(db, session["patient_id"])
    if patient is not None and patient["therapist_id"] not in (None, therapist_id):
        raise HTTPException(status_code=403, detail="this patient belongs to another therapist")
    return session


@router.post("/sessions/{session_id}/progress-note")
async def approve_progress_note(session_id: uuid.UUID, body: ProgressNoteIn,
                                db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    session = await _owned_session(db, sid, body.therapist_id)
    edited = body.text != session.get("ai_note")
    ok = await writes.insert_agent_action(db, "therapist", "progress_note_approved", session["patient_id"], sid,
                                         {"text": body.text, "edited": edited, "approved_by": body.therapist_id})
    if not ok:
        raise HTTPException(status_code=503, detail="database unavailable: could not save the approval")
    return {"ok": True, "session_id": sid, "approved_by": body.therapist_id,
            "approved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


@router.get("/sessions/{session_id}/progress-note")
async def get_progress_note(session_id: uuid.UUID, db: Database = Depends(require_db)) -> dict[str, Any]:
    sid = str(session_id)
    if await queries.get_session(db, sid) is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = await dashboard_queries.latest_progress_note(db, sid)
    if row is None:
        return {"approved": False, "text": None, "approved_by": None, "approved_at": None, "edited": False}
    detail = row["detail"]
    return {"approved": True, "text": detail["text"], "approved_by": detail.get("approved_by"),
            "approved_at": row["time"].isoformat().replace("+00:00", "Z"), "edited": bool(detail.get("edited"))}


# ---- heart-rate limit (Task R3, Prescribe screen) --------------------------------------------------------

class LimitsIn(BaseModel):
    therapist_id: str = Field(pattern=ID_RE, max_length=64)
    hr_limit: int = Field(ge=40, le=220)


@router.patch("/patients/{patient_id}/limits")
async def set_limits(patient_id: str = Path(pattern=ID_RE, max_length=64), body: LimitsIn = ...,
                     db: Database = Depends(require_db)) -> dict[str, Any]:
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if patient["therapist_id"] not in (None, body.therapist_id):
        raise HTTPException(status_code=403, detail="this patient belongs to another therapist")
    before = patient["hr_limit"]
    new_limit = await writes.set_patient_hr_limit(db, patient_id, body.hr_limit, body.therapist_id)
    if new_limit is None:
        raise HTTPException(status_code=503, detail="database unavailable: heart-rate limit not saved")
    audited = await writes.insert_agent_action(db, "therapist", "hr_limit_set", patient_id,
                                               detail={"from": before, "to": new_limit, "by": body.therapist_id})
    if not audited:
        log.warning("hr_limit_set for %s saved but not audited", patient_id)
    await apply_hr_limit_change(db, patient, patient_id, body.therapist_id, new_limit)
    return {"patient_id": patient_id, "hr_limit": new_limit}
