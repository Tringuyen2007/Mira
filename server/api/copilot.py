"""Care Copilot REST (§7.5): POST /copilot/ask {question, patient_id?} → {answer, charts, sql, evidence, cached, …}."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.agents.copilot import Copilot, cached_demo_answer, is_demo_question
from server.config import cfg
from server.state import get_state

router = APIRouter()

NO_DB_DETAIL = "copilot needs the database: set TIGER_DSN and COPILOT_DSN (read-only copilot_ro user)"


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    patient_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


@router.post("/copilot/ask")
async def ask(body: AskIn) -> dict[str, Any]:
    state = get_state()
    db = state.db
    if db is None or not db.has_ro:
        cached = cached_demo_answer() if cfg.COPILOT_CACHED_DEMO and is_demo_question(body.question) else None
        if cached is not None:
            return cached
        raise HTTPException(status_code=503, detail=NO_DB_DETAIL)
    return await Copilot(db, emit=state.hub.emit).ask(body.question.strip(), body.patient_id)
