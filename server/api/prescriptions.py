"""Prescriptions REST (§4.4): versioned. PUT inserts a new version and deactivates the old one."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server import security
from server.api.db_dep import require_db
from server.db import queries, writes
from server.db.pool import Database

router = APIRouter()


class PrescriptionIn(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=64)
    created_by: str = Field(min_length=1, max_length=64, description="therapist id or 'proposal:<id>'")
    sets: int | None = Field(default=None, ge=1, le=20)
    reps: int | None = Field(default=None, ge=1, le=100)
    targets: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    rule_overrides: dict[str, Any] | None = None


@router.get("/prescriptions/{patient_id}")
async def get_prescriptions(patient_id: str, history: bool = False,
                            principal: security.Principal = Depends(security.require_principal),
                            db: Database = Depends(require_db)) -> dict[str, Any]:
    principal.require(patient_id)
    return {"patient_id": patient_id, "prescriptions": await queries.prescriptions(db, patient_id, history)}


@router.put("/prescriptions/{patient_id}")
async def put_prescription(patient_id: str, body: PrescriptionIn,
                           principal: security.Principal = Depends(security.require_principal),
                           db: Database = Depends(require_db)) -> dict[str, Any]:
    security.full_only(principal)
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    try:
        row = await writes.new_prescription_version(
            db, patient_id, body.exercise_id, created_by=body.created_by, sets=body.sets, reps=body.reps,
            targets=body.targets, limits=body.limits, rule_overrides=body.rule_overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=503, detail="database unavailable: prescription not saved")
    return {"prescription": row}
