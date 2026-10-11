"""Pairing a phone to a patient with a therapist code (Plan 1 §4.2). Public route, rate-limited per IP."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from server import security
from server.api.db_dep import require_db
from server.api.identity_models import ContactIn, ProfileIn
from server.api.me import build_me
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database
from server.logging_setup import get_logger

log = get_logger("pair")
router = APIRouter()

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
CODE_TTL = timedelta(hours=24)
MAX_ATTEMPTS_PER_MIN = 5
MAX_ACTIVE_DEVICES_PER_PATIENT = 10
_attempts: dict[str, list[float]] = defaultdict(list)


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    device_name: str | None = Field(default=None, max_length=60)
    profile: ProfileIn
    emergency_contact: ContactIn | None = None
    alert_therapist: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.strip().upper()
        if not CODE_RE.match(v):
            raise ValueError("code must be 6 letters or digits")
        return v


def _over_limit(ip: str) -> bool:
    """Peek only: does NOT record an attempt (every call to `pair()` does, via `_record_attempt`, successes
    included — a reusable demo code must not let one IP pair unlimited phones)."""
    now = time.monotonic()
    recent = [t for t in _attempts[ip] if now - t < 60.0]
    if recent:
        _attempts[ip] = recent
    else:
        _attempts.pop(ip, None)  # nothing left for this IP: prune it instead of keeping an empty list forever
    return len(recent) >= MAX_ATTEMPTS_PER_MIN


def _record_attempt(ip: str) -> None:
    _attempts[ip].append(time.monotonic())


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


@router.post("/pair")
async def pair(body: PairIn, request: Request, db: Database = Depends(require_db)) -> dict[str, Any]:
    ip = request.client.host if request.client else "?"
    if _over_limit(ip):
        raise HTTPException(status_code=429, detail="too many pairing attempts; try again in a minute")
    _record_attempt(ip)  # every attempt counts, success included: a reusable code must not pair unlimited phones
    row = await queries.pairing_code(db, body.code)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown code")
    if row["expires_at"] is not None and row["expires_at"] < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="code expired")
    if row["used_at"] is not None and not row["reusable"]:
        raise HTTPException(status_code=410, detail="code already used")
    # The SELECT above only picks the precise 404/410 message; the actual claim is atomic so two concurrent
    # requests for the same single-use code can't both succeed (a race the SELECT-then-UPDATE above can't close).
    claimed = await writes.claim_pairing_code(db, body.code)
    if claimed is None:
        raise HTTPException(status_code=410, detail="code already used")
    patient_id = claimed["patient_id"]
    reusable = claimed["reusable"]
    # On a public server, once the demo phone has paired, a reusable code should stop being able to rewrite
    # Maya's profile/emergency contact (Retell dials that number) for every other phone that pairs with it.
    if not reusable or cfg.PAIRING_REUSABLE_UPDATES_PROFILE:
        contact = body.emergency_contact
        ok = await writes.update_patient_profile(
            db, patient_id, name=body.profile.name, height_cm=body.profile.height_cm, mass_kg=body.profile.mass_kg,
            affected_side=body.profile.affected_side, condition=body.profile.condition,
            emergency_contact_name=contact.name if contact else None,
            emergency_contact_phone=contact.phone if contact else None,
            emergency_contact_relationship=contact.relationship if contact else None,
            alert_therapist=body.alert_therapist)
        if not ok:
            raise HTTPException(status_code=503, detail="database unavailable: could not save the profile")
    token = security.new_patient_token()
    if not await writes.insert_patient_device(db, security.token_hash(token), patient_id, body.device_name,
                                              via_reusable=bool(reusable)):
        raise HTTPException(status_code=503, detail="database unavailable: could not pair the phone")
    if await writes.revoke_excess_patient_devices(db, patient_id, keep=MAX_ACTIVE_DEVICES_PER_PATIENT):
        security.invalidate_token_cache()  # an evicted demo phone is refused now, not when its cache entry expires
    log.info("phone paired", extra={"patient_id": patient_id, "device": body.device_name,
                                    "token": security.mask(token)})
    return {"patient_id": patient_id, "token": token, "me": await build_me(db, patient_id)}


@router.post("/patients/{patient_id}/pairing-code", dependencies=[Depends(security.require_token)])
async def mint_pairing_code(patient_id: str, db: Database = Depends(require_db)) -> dict[str, Any]:
    """Therapist/dashboard: a fresh single-use code for this patient, valid 24 h."""
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    code, expires_at = new_code(), datetime.now(UTC) + CODE_TTL
    if not await writes.upsert_pairing_code(db, code, patient_id, patient.get("therapist_id"), expires_at, False):
        raise HTTPException(status_code=503, detail="database unavailable: could not create a code")
    return {"code": code, "expires_at": expires_at}


@router.delete("/patients/{patient_id}/devices", dependencies=[Depends(security.require_token)])
async def revoke_devices(patient_id: str, db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    revoked = await writes.revoke_patient_devices(db, patient_id)
    if revoked is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not revoke")
    security.invalidate_token_cache()
    return {"patient_id": patient_id, "revoked": revoked}
