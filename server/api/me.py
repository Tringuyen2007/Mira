"""The patient's own view: profile, care team, plan, device status (Plan 1 §4.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from server import exercises, security
from server.api.calibration import placeholder_status
from server.api.db_dep import require_db
from server.api.identity_models import MeUpdate
from server.api.scope import patient_for as _patient_for
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state
from server.watch_prefs import merge_watch_prefs

router = APIRouter()


def _first_name(name: str | None) -> str:
    return (name or "").split(" ")[0] or "there"


def prescription_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"exercise_id": row["exercise_id"], "exercise_name": exercises.display_name(row["exercise_id"]),
            "sets": row["sets"], "reps": row["reps"], "targets": row["targets"] or {}, "limits": row["limits"] or {},
            "version": row["version"], **{k: v for k, v in exercises.describe(row["exercise_id"]).items()
                                          if k in ("metric_label", "unit", "target_key")}}


async def build_me(db: Database, patient_id: str) -> dict[str, Any]:
    row, rx = await asyncio.gather(queries.patient_me_row(db, patient_id), queries.prescriptions(db, patient_id))
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    state = get_state()
    lcd = state.devices.status() if state.devices is not None else {"online": False, "last_seen": None}
    contact = ({"name": row["emergency_contact_name"], "phone": row["emergency_contact_phone"],
                "relationship": row["emergency_contact_relationship"]} if row["emergency_contact_name"] else None)
    return {
        "patient": {
            "id": row["id"], "name": row["name"], "first_name": _first_name(row["name"]), "age": row["age"],
            "height_cm": row["height_cm"], "mass_kg": row["mass_kg"], "affected_side": row["affected_side"],
            "condition": row["condition"], "hr_limit": row["hr_limit"], "emergency_contact": contact,
            "alert_therapist": row["alert_therapist"], "share_by_default": row["share_by_default"],
            "device_prefs": row["device_prefs"] or {}, "next_visit_at": row["next_visit_at"],
        },
        "therapist": ({"id": row["therapist_id"], "name": row["therapist_name"], "clinic": row["therapist_clinic"],
                       "phone": row["therapist_phone"]} if row["therapist_id"] else None),
        "prescriptions": [prescription_view(r) for r in rx],
        "lcd": {"online": bool(lcd.get("online")), "last_seen": lcd.get("last_seen")},
        "calibration": placeholder_status(),
    }


@router.get("/me")
async def get_me(principal: security.Principal = Depends(security.require_principal),
                 patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    return await build_me(db, _patient_for(principal, patient_id))


@router.put("/me")
async def put_me(body: MeUpdate, principal: security.Principal = Depends(security.require_principal),
                 patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = _patient_for(principal, patient_id)
    p, c = body.profile, body.emergency_contact
    if principal.locked_demo:  # a demo-code phone may not rewrite who Retell dials (other fields still apply)
        p, c = None, None
    device_prefs = body.device_prefs
    if device_prefs is not None:
        # The iPhone Settings screen sends device_prefs without the watch's watch_* keys; keep those as stored
        # so an iPhone-originated save doesn't wipe the watch's settings (iPhone behaviour is unchanged).
        existing = await queries.patient_me_row(db, pid)
        device_prefs = merge_watch_prefs(existing["device_prefs"] if existing else None, device_prefs)
    ok = await writes.update_patient_profile(
        db, pid, name=p.name if p else None, height_cm=p.height_cm if p else None, mass_kg=p.mass_kg if p else None,
        affected_side=p.affected_side if p else None, condition=p.condition if p else None,
        emergency_contact_name=c.name if c else None, emergency_contact_phone=c.phone if c else None,
        emergency_contact_relationship=c.relationship if c else None, alert_therapist=body.alert_therapist,
        share_by_default=body.share_by_default, device_prefs=device_prefs)
    if not ok:
        raise HTTPException(status_code=503, detail="database unavailable: could not save")
    me = await build_me(db, pid)
    state = get_state()
    current = state.sessions.current
    if current is not None and current.patient_id == pid:
        state.bridge.live.device_prefs = me["patient"]["device_prefs"]
    return me


@router.get("/me/export")
async def export_me(principal: security.Principal = Depends(security.require_principal),
                    patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = _patient_for(principal, patient_id)
    me = await build_me(db, pid)
    return {"exported_at": datetime.now(UTC), **me, **await queries.patient_export(db, pid)}


@router.delete("/me")
async def delete_me(principal: security.Principal = Depends(security.require_principal),
                    patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    """Caveat (mock): when this stops Maya's live mock session under MIRA_SOURCE=mock, nothing restarts it in
    this plan — that is Plan 2's auto-restart."""
    pid = _patient_for(principal, patient_id)
    if principal.locked_demo:
        raise HTTPException(status_code=403, detail="demo phone: deleting this patient is disabled on this server")
    if await queries.patient_me_row(db, pid) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    state = get_state()
    current = state.sessions.current
    if current is not None and current.patient_id == pid:
        await state.sessions.stop(current.session_id)
    counts = await writes.purge_patient_data(db, pid)
    if counts is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not delete")
    security.invalidate_token_cache()
    return {"deleted": True, "sessions_deleted": counts.get("sessions", 0), "counts": counts}
