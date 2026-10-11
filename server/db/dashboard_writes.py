"""Writes for the therapist dashboard (dashboard spec 2026-10-11, Task R3): `PATCH /patients/{id}/limits`
(the heart-rate limit on the Prescribe screen). Kept separate from server/db/writes.py per the spec's
"Server additions" so the `wire-tri` merge (another branch editing server/db/writes.py) stays clean.

Follows writes.py's contract: takes `db` first, never raises on database failure (uses `_safe`), and
returns None on failure so the route can turn that into a 503."""

from __future__ import annotations

from typing import Any

from server.db.pool import Database
from server.db.writes import _safe


async def set_hr_limit(db: Database | None, patient_id: str, hr_limit: int) -> int | None:
    """Sets `patients.hr_limit` and returns the new value, or None if the patient doesn't exist (or on
    database failure). The caller (server.api.dashboard) is responsible for the 403 ownership check and
    for logging the `agent_actions` row, since it also needs the *previous* value for that row's detail."""
    sql = "UPDATE patients SET hr_limit = %s WHERE id = %s RETURNING hr_limit"

    async def run(d: Database) -> int | None:
        row = await d.fetchrow(sql, (hr_limit, patient_id))
        return int(row["hr_limit"]) if row else None

    return await _safe(db, "set_hr_limit", run, None, patient_id=patient_id)
