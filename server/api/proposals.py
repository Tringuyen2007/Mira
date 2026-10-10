"""Plan proposals REST (§7.5, §9.5 humans decide): list cards, approve / edit / dismiss.

Approve and edit insert a NEW prescription version (the old one is kept, active=false), record decided_by and
decided_at on the proposal, log an agent_actions row and emit `proposal_decided` so the device reloads the plan.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from server.agents.copilot import PlanChange
from server.api.db_dep import require_db
from server.db import queries, writes
from server.db.pool import Database
from server.logging_setup import get_logger
from server.state import get_state

log = get_logger("api.proposals")
router = APIRouter()

Status = Literal["pending", "approved", "edited", "dismissed"]
_ID = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class DecideIn(BaseModel):
    therapist_id: str = _ID


class EditIn(DecideIn):
    change: PlanChange


class DismissIn(DecideIn):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/proposals")
async def list_proposals(status: Status | Literal["all"] = "pending", patient_id: str | None = None,
                         db: Database = Depends(require_db)) -> dict[str, Any]:
    rows = await db.fetch(
        "SELECT pp.*, p.name AS patient_name FROM plan_proposals pp JOIN patients p ON p.id = pp.patient_id "
        "WHERE (%(status)s::text IS NULL OR pp.status = %(status)s) "
        "AND (%(pid)s::text IS NULL OR pp.patient_id = %(pid)s) ORDER BY pp.created_at DESC, pp.id DESC LIMIT 100",
        {"status": None if status == "all" else status, "pid": patient_id})
    return {"proposals": rows}


@router.post("/proposals/{proposal_id}/approve")
async def approve(proposal_id: int, body: DecideIn, db: Database = Depends(require_db)) -> dict[str, Any]:
    proposal = await _pending(db, proposal_id, body.therapist_id)
    try:
        change = PlanChange.model_validate(proposal["change"])
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=f"stored change is invalid; use edit: {exc.errors()[0]['msg']}"
                            ) from exc
    return await _apply(db, proposal, change, "approved", body.therapist_id)


@router.post("/proposals/{proposal_id}/edit")
async def edit(proposal_id: int, body: EditIn, db: Database = Depends(require_db)) -> dict[str, Any]:
    proposal = await _pending(db, proposal_id, body.therapist_id)
    if body.change.is_empty():
        raise HTTPException(status_code=400, detail="change has no effect")
    return await _apply(db, proposal, body.change, "edited", body.therapist_id)


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss(proposal_id: int, body: DismissIn, db: Database = Depends(require_db)) -> dict[str, Any]:
    proposal = await _pending(db, proposal_id, body.therapist_id)
    decided = await _decide(db, proposal_id, "dismissed", body.therapist_id)
    await writes.insert_agent_action(db, "copilot", "proposal_dismissed", proposal["patient_id"],
                                     detail={"proposal_id": proposal_id, "decided_by": body.therapist_id,
                                             "reason": body.reason})
    _emit(decided, None)
    return {"proposal": decided, "prescription": None}


# ---- helpers -----------------------------------------------------------------------------------------------

async def _pending(db: Database, proposal_id: int, therapist_id: str) -> dict[str, Any]:
    proposal = await db.fetchrow("SELECT * FROM plan_proposals WHERE id = %s", (proposal_id,))
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"proposal already {proposal['status']}")
    if await db.fetchrow("SELECT 1 FROM therapists WHERE id = %s", (therapist_id,)) is None:
        raise HTTPException(status_code=400, detail="unknown therapist_id")
    return proposal


async def _decide(db: Database, proposal_id: int, status: str, therapist_id: str) -> dict[str, Any]:
    decided = await writes.decide_proposal(db, proposal_id, status, therapist_id)
    if decided is not None:
        return decided
    row = await db.fetchrow("SELECT status FROM plan_proposals WHERE id = %s", (proposal_id,))
    if row is not None and row["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"proposal already {row['status']}")
    raise HTTPException(status_code=503, detail="database unavailable: decision not saved")


def merged_targets(current: dict[str, Any], change: PlanChange) -> dict[str, Any]:
    """The new version's targets: the active ones plus the change (rest days and drills are kept in targets)."""
    targets = {**current, **(change.targets or {})}
    if change.rest_days_between is not None:
        targets["rest_days_between"] = change.rest_days_between
    added = set(targets.get("add") or []) | set(change.add or [])
    removed = set(targets.get("remove") or []) | set(change.remove or [])
    if change.add or change.remove:
        targets["add"] = sorted(added - set(change.remove or []))
        targets["remove"] = sorted(removed - set(change.add or []))
    return targets


async def _apply(db: Database, proposal: dict[str, Any], change: PlanChange, status: str,
                 therapist_id: str) -> dict[str, Any]:
    pid, proposal_id = proposal["patient_id"], proposal["id"]
    active = {r["exercise_id"]: r for r in await queries.prescriptions(db, pid)}
    current = active.get(change.exercise_id)
    if current is None:
        raise HTTPException(status_code=409, detail=f"{change.exercise_id!r} is not an active prescription")
    decided = await _decide(db, proposal_id, status, therapist_id)
    rx = await writes.new_prescription_version(
        db, pid, change.exercise_id, created_by=f"proposal:{proposal_id}", sets=change.sets, reps=change.reps,
        targets=merged_targets(current.get("targets") or {}, change))
    if rx is None:
        log.error("proposal decided but prescription not saved", extra={"proposal_id": proposal_id})
        raise HTTPException(status_code=503, detail="database unavailable: proposal decided, prescription not saved")
    await writes.insert_agent_action(db, "copilot", f"proposal_{status}", pid, detail={
        "proposal_id": proposal_id, "decided_by": therapist_id, "change": change.model_dump(exclude_none=True),
        "original_change": proposal["change"], "prescription_id": rx["id"], "version": rx["version"]})
    _emit(decided, rx)
    return {"proposal": decided, "prescription": rx}


def _emit(decided: dict[str, Any], rx: dict[str, Any] | None) -> None:
    get_state().hub.emit("proposal_decided", {
        "proposal_id": decided["id"], "patient_id": decided["patient_id"], "status": decided["status"],
        "decided_by": decided["decided_by"], "exercise_id": rx["exercise_id"] if rx else None,
        "prescription_version": rx["version"] if rx else None})
