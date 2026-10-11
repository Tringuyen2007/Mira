"""The patient's own view: profile, care team, plan, device status (Plan 1 §4.2). Routes in Task 5."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from server import exercises
from server.api.calibration import placeholder_status
from server.db import queries
from server.db.pool import Database
from server.state import get_state

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
