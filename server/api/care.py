"""Care team view and two-way messaging (Plan 3 §6.4): therapist card, notes, AI-draft highlights, milestones,
the patient/therapist message thread, and the "send a test alert" button."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import security
from server.api import ratelimit
from server.api.db_dep import require_db
from server.api.me import _first_name
from server.api.scope import patient_for
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database
from server.notify import notifications
from server.state import get_state

router = APIRouter()

MESSAGE_KINDS = ("message", "note", "visit_highlight")


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class TherapistMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    kind: str = Field(default="message")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _message_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "sender": row["sender"], "kind": row["kind"], "body": row["body"],
            "created_at": _iso(row["created_at"]), "read_at": _iso(row["read_at"])}


def _emit_message(patient_id: str, row: dict[str, Any]) -> None:
    get_state().hub.emit("message", {"patient_id": patient_id, "message": _message_view(row)})


async def _notify_therapist_of_message(db: Database, patient_id: str, therapist_id: str | None, patient_name: str,
                                       body: str, message_id: int) -> None:
    if not therapist_id:
        return
    await notifications.create(
        kind="message", severity="normal", therapist_id=therapist_id, patient_id=patient_id,
        title=f"Message from {_first_name(patient_name)}", body=body[:140],
        link=f"/patients/{patient_id}#messages", source_table="messages", source_id=str(message_id),
        dedupe_key=f"message:{message_id}")


@router.get("/me/care")
async def get_care(principal: security.Principal = Depends(security.require_principal),
                   patient_id: str | None = Query(default=None), db: Database = Depends(require_db)
                   ) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    me_row = await queries.patient_me_row(db, pid)
    if me_row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    notes, highlights, unread = await asyncio.gather(
        queries.care_notes(db, pid), queries.latest_highlights(db, pid), queries.unread_messages(db, pid))
    contact = ({"name": me_row["emergency_contact_name"], "phone": me_row["emergency_contact_phone"],
                "relationship": me_row["emergency_contact_relationship"]} if me_row["emergency_contact_name"]
               else None)
    return {
        "therapist": ({"id": me_row["therapist_id"], "name": me_row["therapist_name"],
                       "clinic": me_row["therapist_clinic"], "phone": me_row["therapist_phone"]}
                      if me_row["therapist_id"] else None),
        "next_visit_at": me_row["next_visit_at"],
        "hr_limit": me_row["hr_limit"],
        "emergency_contact": contact,
        "alert_therapist": me_row["alert_therapist"],
        "notes": [_message_view(n) for n in notes],
        "highlights": highlights,
        "unread_messages": unread,
    }


@router.get("/me/messages")
async def get_messages(principal: security.Principal = Depends(security.require_principal),
                       patient_id: str | None = Query(default=None), since_id: int | None = Query(default=None),
                       mark_read: bool = Query(default=False), db: Database = Depends(require_db)
                       ) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    if mark_read:
        await writes.mark_messages_read(db, pid, sender_not="patient")
    rows = await queries.list_messages(db, pid, kind="message", since_id=since_id)
    return {"messages": [_message_view(r) for r in rows]}


@router.post("/me/messages")
async def post_message(body: MessageIn, principal: security.Principal = Depends(security.require_principal),
                       patient_id: str | None = Query(default=None), db: Database = Depends(require_db)
                       ) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    ratelimit.check_patient(get_state().patient_limiter, principal.scope, "messages", pid)
    me_row = await queries.patient_me_row(db, pid)
    if me_row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    row = await writes.insert_message(db, pid, sender="patient", kind="message", body=body.body)
    if row is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not save")
    _emit_message(pid, row)
    await _notify_therapist_of_message(db, pid, me_row["therapist_id"], me_row["name"], body.body, row["id"])
    return {"message": _message_view(row)}


@router.post("/patients/{patient_id}/messages")
async def post_therapist_message(patient_id: str, body: TherapistMessageIn,
                                 principal: security.Principal = Depends(security.require_principal),
                                 db: Database = Depends(require_db)) -> dict[str, Any]:
    security.full_only(principal)
    if body.kind not in MESSAGE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {MESSAGE_KINDS}")
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    row = await writes.insert_message(db, patient_id, sender="therapist", kind=body.kind, body=body.body)
    if row is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not save")
    _emit_message(patient_id, row)
    return {"message": _message_view(row)}


@router.get("/patients/{patient_id}/milestones")
async def get_milestones(patient_id: str, principal: security.Principal = Depends(security.require_principal),
                         db: Database = Depends(require_db)) -> dict[str, Any]:
    principal.require(patient_id)
    rows = await queries.patient_milestones(db, patient_id)
    return {"patient_id": patient_id, "milestones": rows}


@router.post("/me/test-alert")
async def test_alert(principal: security.Principal = Depends(security.require_principal),
                     patient_id: str | None = Query(default=None), db: Database = Depends(require_db)
                     ) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    ratelimit.check_patient(get_state().patient_limiter, principal.scope, "test_alert", pid)
    me_row = await queries.patient_me_row(db, pid)
    if me_row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if me_row["therapist_id"]:
        await notifications.create(
            kind="test", severity="low", therapist_id=me_row["therapist_id"], patient_id=pid,
            title=f"Test alert from {_first_name(me_row['name'])}",
            body="This is a test of Mira's alert system.", link=None, source_table=None, source_id=None,
            dedupe_key=None)
    called = False
    note = "Test alert logged. Calls are disabled on this server."
    notifier = get_state().notifier
    test_call = getattr(notifier, "test_call", None) if notifier is not None else None
    may_call = not cfg.CALLS_DISABLED and cfg.ALLOW_TEST_CALLS and not principal.locked_demo
    if may_call and test_call is not None and me_row["emergency_contact_phone"]:
        await test_call(me_row["emergency_contact_phone"])
        called = True
        note = "Calling your emergency contact now."
    return {"logged": True, "called": called, "note": note}
