"""Care Copilot REST (§7.5): POST /copilot/ask {question, patient_id?, context?} → {answer, charts, sql, evidence,
cached, proposals, drafts, …}; POST /copilot/review/{patient_id} → the same shape for one patient's weekly
review."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from server.agents.copilot import MAX_CONTEXT_TURNS, Copilot, cached_demo_answer, is_demo_question
from server.config import cfg
from server.db import queries
from server.state import get_state

router = APIRouter()

NO_DB_DETAIL = "copilot needs the database: set TIGER_DSN and COPILOT_DSN (read-only copilot_ro user)"
PATIENT_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"


class ContextTurn(BaseModel):
    """One earlier turn of the conversation (follow-ups). Context only: its numbers are never evidence."""
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(default="", max_length=1200)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    patient_id: str | None = Field(default=None, max_length=64, pattern=PATIENT_ID_PATTERN)
    context: list[ContextTurn] = Field(default_factory=list, max_length=MAX_CONTEXT_TURNS)


@router.post("/copilot/ask")
async def ask(body: AskIn) -> dict[str, Any]:
    state = get_state()
    db = state.db
    if db is None or not db.has_ro:
        cached = await cached_demo_answer(db) if cfg.COPILOT_CACHED_DEMO and is_demo_question(body.question) else None
        if cached is not None:
            return cached
        raise HTTPException(status_code=503, detail=NO_DB_DETAIL)
    return await Copilot(db, emit=state.hub.emit).ask(body.question.strip(), body.patient_id,
                                                      context=[t.model_dump() for t in body.context])


@router.post("/copilot/review/{patient_id}")
async def review(patient_id: Annotated[str, PathParam(max_length=64, pattern=PATIENT_ID_PATTERN)]) -> dict[str, Any]:
    """One patient's last week vs the weeks before (Copilot weekly review); may leave one pending proposal."""
    state = get_state()
    db = state.db
    if db is None or not db.has_ro:
        raise HTTPException(status_code=503, detail=NO_DB_DETAIL)
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return await Copilot(db, emit=state.hub.emit).review_patient(patient_id)
