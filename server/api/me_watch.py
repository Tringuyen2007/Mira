"""The patient's watch (contract `rest_get_me_watch`, `rest_post_me_tremor`): `GET /me/watch` is what the iPhone
pushes to the watch as its `setup` context; `POST /me/tremor` stores the watch's daily tremor summaries."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import exercises, security
from server.api import ratelimit
from server.api.db_dep import require_db
from server.api.scope import patient_for
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state
from server.watch_prefs import watch_prefs

router = APIRouter()

MAX_TREMOR_DAYS = 14
MINUTES_PER_DAY = 1440


def _first_name(name: str | None) -> str:
    return (name or "").split(" ")[0] or "there"


def plan_item(rx: dict[str, Any], done: dict[str, int]) -> dict[str, Any]:
    exercise_id = rx["exercise_id"]
    item: dict[str, Any] = {"exercise_id": exercise_id, "name": exercises.display_name(exercise_id),
                            "sets": rx["sets"], "reps": rx["reps"], "done_sets": int(done.get(exercise_id, 0)),
                            "watch": exercises.is_watch_exercise(exercise_id)}
    if item["watch"]:
        item["goal_deg"] = exercises.rotation_goal_deg(rx.get("targets"))
    return item


def catalog_watch_item(exercise_id: str, done: dict[str, int]) -> dict[str, Any]:
    """A catalog watch exercise with no prescription row (MOCK_WRIST=1 demo): catalog defaults for sets/reps/goal,
    `done_sets` still from today's actual rows so a demo session already run shows progress."""
    return {"exercise_id": exercise_id, "name": exercises.display_name(exercise_id),
            "sets": exercises.CATALOG_DEFAULT_SETS, "reps": exercises.CATALOG_DEFAULT_REPS,
            "done_sets": int(done.get(exercise_id, 0)), "watch": True,
            "goal_deg": exercises.rotation_goal_deg(None)}


async def build_me_watch(db: Database, patient_id: str) -> dict[str, Any]:
    row, rx, done = await asyncio.gather(queries.me_watch_row(db, patient_id), queries.prescriptions(db, patient_id),
                                         queries.sets_done_today(db, patient_id))
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    plan = [plan_item(r, done) for r in rx]
    if cfg.MOCK_WRIST:
        # The demo (no watch) needs something for the watch's Rotation list even without a prescribed watch
        # exercise: append the catalog's watch exercises not already prescribed. Flag off: unchanged.
        prescribed = {r["exercise_id"] for r in rx}
        plan += [catalog_watch_item(eid, done) for eid in sorted(exercises.WATCH_EXERCISES) if eid not in prescribed]
    return {
        "first_name": _first_name(row["name"]),
        "hr_limit": row["hr_limit"],
        "hr_limit_set_by_name": row["hr_limit_set_by_name"],
        "therapist_name": row["therapist_name"],
        "tremor_monitoring": bool(row["tremor_monitoring"]),
        "prefs": watch_prefs(row["device_prefs"]),
        "plan": plan,
    }


@router.get("/me/watch")
async def get_me_watch(principal: security.Principal = Depends(security.require_principal),
                       patient_id: str | None = Query(default=None),
                       db: Database = Depends(require_db)) -> dict[str, Any]:
    return await build_me_watch(db, patient_for(principal, patient_id))


class TremorDayIn(BaseModel):
    day: date
    none_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    slight_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    mild_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    strong_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    unknown_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    dyskinesia: Literal["none", "few", "some", "many"]
    source: Literal["device", "simulated"]


class TremorIn(BaseModel):
    days: list[TremorDayIn] = Field(min_length=1, max_length=MAX_TREMOR_DAYS)


@router.post("/me/tremor")
async def post_tremor(body: TremorIn, principal: security.Principal = Depends(security.require_principal),
                      patient_id: str | None = Query(default=None),
                      db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    ratelimit.check_patient(get_state().patient_limiter, principal.scope, "tremor", pid)
    row = await queries.me_watch_row(db, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if not row["tremor_monitoring"]:
        raise HTTPException(status_code=403, detail="tremor monitoring off")
    stored = await writes.upsert_tremor_days(db, pid, [d.model_dump() for d in body.days])
    if stored is None:
        raise HTTPException(status_code=503, detail="database unavailable: tremor not saved")
    return {"stored": stored}
