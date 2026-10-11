"""Plan proposals REST (§7.5, §9.5 humans decide): list cards, approve / edit / dismiss.

Approve and edit insert a NEW prescription version (the old one is kept, active=false), record decided_by and
decided_at on the proposal and log an agent_actions row, all in one transaction; then emit `proposal_decided` so
the device reloads the plan. Re-deciding a proposal is a 409.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from server.agents.copilot import PlanChange
from server.api.db_dep import require_db
from server.db import writes
from server.db.pool import Database
from server.state import get_state

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
    await _pending(db, proposal_id, body.therapist_id)
    return await _decide(db, proposal_id, "dismissed", body.therapist_id, detail={"reason": body.reason})


# ---- helpers -----------------------------------------------------------------------------------------------

async def _pending(db: Database, proposal_id: int, therapist_id: str) -> dict[str, Any]:
    """Early, friendly checks; the decision itself re-checks `pending` inside its transaction."""
    proposal = await db.fetchrow("SELECT * FROM plan_proposals WHERE id = %s", (proposal_id,))
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"proposal already {proposal['status']}")
    owner = await db.fetchrow("SELECT t.id, p.therapist_id FROM therapists t LEFT JOIN patients p ON p.id = %s "
                              "WHERE t.id = %s", (proposal["patient_id"], therapist_id))
    if owner is None:
        raise HTTPException(status_code=400, detail="unknown therapist_id")
    if owner["therapist_id"] not in (None, therapist_id):
        raise HTTPException(status_code=403, detail="this patient belongs to another therapist")
    return proposal


def merged_targets(current: dict[str, Any], change: PlanChange) -> dict[str, Any]:
    """The new version's targets: the active ones plus the change (rest days and drills are kept in targets)."""
    targets = {**current, **(change.targets or {})}
    if change.rest_days_between is not None:
        targets["rest_days_between"] = change.rest_days_between
    added = set(targets.get("add") or []) | set(change.add or [])
    removed = set(targets.get("remove") or []) | set(change.remove or [])
    for key, items in (("add", added - set(change.remove or [])), ("remove", removed - set(change.add or []))):
        if items:
            targets[key] = sorted(items)
        else:
            targets.pop(key, None)
    return targets


async def _apply(db: Database, proposal: dict[str, Any], change: PlanChange, status: str,
                 therapist_id: str) -> dict[str, Any]:
    return await _decide(db, proposal["id"], status, therapist_id, change=change, detail={
        "change": change.model_dump(exclude_none=True), "original_change": proposal["change"]})


async def _decide(db: Database, proposal_id: int, status: str, therapist_id: str, *,
                  change: PlanChange | None = None, detail: dict[str, Any]) -> dict[str, Any]:
    """One transaction (writes.decide_proposal_and_apply), then `proposal_decided` for the device and dashboard."""
    try:
        out = await writes.decide_proposal_and_apply(
            db, proposal_id, status, therapist_id, exercise_id=change.exercise_id if change else None,
            sets=change.sets if change else None, reps=change.reps if change else None,
            targets=(lambda current: merged_targets(current, change)) if change else None, detail=detail)
    except writes.ProposalConflict as exc:
        raise HTTPException(status_code=404 if exc.status is None else 409, detail=str(exc)) from exc
    if out is None:
        raise HTTPException(status_code=503, detail="database unavailable: nothing was saved, try again")
    decided, rx = out["proposal"], out["prescription"]
    get_state().hub.emit("proposal_decided", {
        "proposal_id": decided["id"], "patient_id": decided["patient_id"], "status": decided["status"],
        "decided_by": decided["decided_by"], "exercise_id": rx["exercise_id"] if rx else None,
        "prescription_version": rx["version"] if rx else None})
    return out
