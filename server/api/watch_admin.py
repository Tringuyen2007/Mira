"""Therapist watch routes (full token; registered with `require_token`):
- `PATCH /patients/{id}/watch-features` — tremor monitoring on/off (`watch_features_changed` push).
- `GET /patients/{id}/watch` — last 20 HR recoveries, last 20 rotation sets, 14 days of tremor.

`PATCH /patients/{id}/limits` already exists as the therapist-page contract in `server/api/dashboard.py`
(`set_limits`); it is extended there (via `server.watch_limits.apply_hr_limit_change`) rather than duplicated
here."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from server.api.db_dep import require_db
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state

router = APIRouter()

ID_RE = r"^[A-Za-z0-9_-]{1,64}$"  # the dashboard's id shape (same as therapist-page)


class WatchFeaturesIn(BaseModel):
    tremor_monitoring: bool


@router.patch("/patients/{patient_id}/watch-features")
async def set_watch_features(body: WatchFeaturesIn, patient_id: str = Path(pattern=ID_RE, max_length=64),
                             db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if not await writes.set_tremor_monitoring(db, patient_id, body.tremor_monitoring):
        raise HTTPException(status_code=503, detail="database unavailable: watch features not saved")
    await writes.insert_agent_action(db, "therapist", "watch_features_set", patient_id,
                                     detail={"tremor_monitoring": body.tremor_monitoring})
    get_state().hub.emit("watch_features_changed", {"patient_id": patient_id,
                                                    "tremor_monitoring": body.tremor_monitoring,
                                                    "t": round(time.time(), 3)})
    return {"patient_id": patient_id, "tremor_monitoring": body.tremor_monitoring}


@router.get("/patients/{patient_id}/watch")
async def get_patient_watch(patient_id: str = Path(pattern=ID_RE, max_length=64),
                            db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    recovery, rotation, tremor = await asyncio.gather(queries.patient_hr_recovery(db, patient_id),
                                                      queries.patient_rotation_sets(db, patient_id),
                                                      queries.patient_tremor_days(db, patient_id, 14))
    return {"patient_id": patient_id, "hr_recovery": recovery, "rotation_sets": rotation, "tremor": tremor}
