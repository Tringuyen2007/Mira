"""Therapist dashboard REST (dashboard spec 2026-10-11, Task 8): `GET /caseload` and
`GET /patients/{id}/memory` ("Therapist Memory"). Pure triage logic lives in server.dashboard_triage; this
module only assembles signals from the database and the live session snapshot, and formats the response."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from server.api.db_dep import require_db
from server.dashboard_triage import Signals, triage
from server.db import dashboard_queries, queries
from server.db.pool import Database
from server.state import get_state

router = APIRouter()

# Same shape the dashboard uses for every id (query or path): letters, digits, underscore, hyphen.
ID_RE = r"^[A-Za-z0-9_-]{1,64}$"
IDLE_PHASES = frozenset({"idle", "done"})


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
    return max(1, days // 7)


async def _live_now(db: Database) -> dict[str, Any] | None:
    """The current `/ws/live` session, if any, past idle/done. Not limited to one therapist's caseload: a
    therapist may have another therapist's patient live on the shared device."""
    state = get_state()
    session = state.sessions.current
    live = state.bridge.live
    if session is None or live.phase in IDLE_PHASES:
        return None
    patient = await queries.get_patient(db, session.patient_id)
    name = patient["name"] if patient else session.profile.first_name
    return {"patient_id": session.patient_id, "name": name, "exercise_name": live.exercise_name,
            "set": live.set, "sets_target": live.sets_target, "rep": live.rep}


def _sort_key(patient: dict[str, Any]) -> tuple[int, float]:
    last_at = patient["last_session_at"]
    return (patient["severity"], last_at.timestamp() if last_at else 0.0)


@router.get("/caseload")
async def caseload(therapist_id: str = Query(pattern=ID_RE, max_length=64),
                   db: Database = Depends(require_db)) -> dict[str, Any]:
    rows = await dashboard_queries.caseload_signals(db, therapist_id)
    live = await _live_now(db)
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
            "live_now": is_live,
        })
    patients.sort(key=_sort_key, reverse=True)

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
