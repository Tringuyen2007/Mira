"""Patients REST (§4.4): caseload, profile, daily progress series."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from server.api.db_dep import require_db
from server.db import queries
from server.db.pool import Database

router = APIRouter()


@router.get("/patients")
async def list_patients(db: Database = Depends(require_db)) -> dict[str, Any]:
    return {"patients": await queries.list_patients(db)}


@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str, db: Database = Depends(require_db)) -> dict[str, Any]:
    patient, rx, recent = await asyncio.gather(queries.get_patient(db, patient_id),
                                               queries.prescriptions(db, patient_id),
                                               queries.recent_sessions(db, patient_id))
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return {"patient": patient, "prescriptions": rx, "recent_sessions": recent}


@router.get("/patients/{patient_id}/progress")
async def get_progress(patient_id: str, exercise_id: str | None = None,
                       days: int = Query(default=90, ge=1, le=365),
                       db: Database = Depends(require_db)) -> dict[str, Any]:
    """Daily series from progress_v (falls back to a live time_bucket query with the same columns)."""
    source, series = await queries.progress_series(db, patient_id, exercise_id, days)
    return {"patient_id": patient_id, "exercise_id": exercise_id, "days": days, "source": source,
            "series": series}
