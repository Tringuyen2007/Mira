"""Helper for the therapist-page `PATCH /patients/{id}/limits` route (`server/api/dashboard.py::set_limits`),
which already implements that contract. tri-watch extends it here instead of adding a second route: who set the
limit (`patients.hr_limit_set_by`), a refresh of the live session's cached profile, and a `limits_changed`
push — so the dashboard route stays a minimal edit and the therapist-page merge has nothing to resolve."""

from __future__ import annotations

import time
from typing import Any

from server.db import queries
from server.db.pool import Database
from server.state import get_state


def refresh_live_limit(patient_id: str, hr_limit: int) -> bool:
    """Apply a new limit to the live session at once. The Safety Guardian (`_profile`), the Coach (`_on_hr`) and an
    open incident all read this one `PatientProfile` object; SessionPersistence and LiveState cache no limit."""
    current = get_state().sessions.current
    if current is None or current.patient_id != patient_id:
        return False
    current.profile.hr_limit = hr_limit
    return True


async def apply_hr_limit_change(db: Database, patient: dict[str, Any], patient_id: str, therapist_id: str,
                                new_limit: int) -> None:
    """Call after the write/audit succeed: refreshes the live session, then pushes `limits_changed`."""
    refresh_live_limit(patient_id, new_limit)
    set_by_name = (patient["therapist_name"] if patient["therapist_id"] == therapist_id
                   else await queries.therapist_name(db, therapist_id))
    get_state().hub.emit("limits_changed", {"patient_id": patient_id, "hr_limit": new_limit,
                                            "set_by_name": set_by_name, "t": round(time.time(), 3)})
