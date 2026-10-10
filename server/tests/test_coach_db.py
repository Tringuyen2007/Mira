"""Between-Set Coach against the local Docker TimescaleDB: history and prescription limits."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from server.agents import session_coach
from server.agents.session_coach import DEFAULT_LIMITS, SessionCoach
from server.ai import gemini
from server.db import writes
from server.db.pool import Database
from server.state import SessionRegistry
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_coach import FakeBridge, FakeHub, _script

NOW = datetime.now(UTC)
# (days ago, reps_done, pain_pre, pain_post, effort) for Maya's past sessions, oldest first
PAST = [(9, 20, 2, 3, 5), (6, 24, 3, 4, 6), (4, 26, 3, 6, 7), (2, 28, 2, 3, 6), (1, 30, 4, 4, 5)]


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            for days, reps, pain_pre, pain_post, effort in PAST:
                started = NOW - timedelta(days=days)
                conn.execute(
                    "INSERT INTO sessions (patient_id, exercise_id, started_at, ended_at, reps_done, pain_pre, "
                    "pain_post, effort, simulated) VALUES ('maya', 'squat', %s, %s, %s, %s, %s, %s, true)",
                    (started, started + timedelta(minutes=12), reps, pain_pre, pain_post, effort))
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    yield database
    await database.execute("DELETE FROM sessions WHERE NOT simulated")  # each test's live session row
    await database.close()


@pytest.fixture(autouse=True)
def restore_default_coach_fake() -> Iterator[None]:
    yield
    gemini.register_fake("coach", session_coach._fake_coach)


async def _live_session(db: Database) -> tuple[SessionRegistry, Any]:
    """A live session whose row exists, as the session store creates it at session start."""
    registry = SessionRegistry()
    session = await registry.start("maya", "squat")
    assert await writes.insert_session(db, session.session_id, "maya", "squat", NOW)
    return registry, session


async def test_get_history_returns_last_3_past_sessions(db: Database) -> None:
    registry, session = await _live_session(db)
    coach = SessionCoach(FakeHub(), registry, db_provider=lambda: db)
    history = await coach._history(session)
    # newest first, the live session itself excluded
    assert [h["reps_done"] for h in history] == [30, 28, 26]
    assert history[0] == {"date": (NOW - timedelta(days=1)).date().isoformat(), "exercise": "squat",
                          "reps_done": 30, "pain_pre": 4, "pain_post": 4, "effort": 5}


async def test_get_history_tool_feeds_the_agent(db: Database) -> None:
    registry, session = await _live_session(db)
    seen: list[dict[str, Any]] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        seen.extend(m["content"] for m in messages if m.get("role") == "tool" and m.get("name") == "get_history")
        return _script(("get_history", {}))(messages, tools)

    gemini.register_fake("coach", fake)
    coach = SessionCoach(FakeHub(), registry, db_provider=lambda: db)
    await coach.on_event({"type": "set_done", "session_id": session.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    await coach.drain()
    assert [h["reps_done"] for h in seen[0]["sessions"]] == [30, 28, 26]


async def test_get_history_for_a_patient_without_history(db: Database) -> None:
    registry = SessionRegistry()
    session = await registry.start("nobody", "squat")
    coach = SessionCoach(FakeHub(), registry, db_provider=lambda: db)
    assert await coach._history(session) == []


async def test_limits_come_from_the_active_prescription(db: Database) -> None:
    limits = {"min_reps": 10, "max_reps": 12, "min_rest_s": 45, "max_rest_s": 90}
    assert await writes.new_prescription_version(db, "maya", "squat", created_by="okafor", limits=limits)
    try:
        registry, session = await _live_session(db)
        hub, bridge = FakeHub(), FakeBridge()
        gemini.register_fake("coach", _script(("adjust_next_set", {"reps": 6, "rest_s": 200})))
        coach = SessionCoach(hub, registry, bridge, db_provider=lambda: db)
        await coach.on_event({"type": "set_done", "session_id": session.session_id, "set": 1, "reps": 10,
                              "exercise_id": "squat"})
        await coach.drain()
        [adapt] = hub.of("coach_adapt")
        assert adapt["detail"]["reps_applied"] == 10 and adapt["detail"]["rest_s_applied"] == 90
        assert bridge.sent[0]["reps"] == 10 and bridge.sent[0]["rest_s"] == 90
    finally:
        assert await writes.new_prescription_version(db, "maya", "squat", created_by="okafor",
                                                     limits=DEFAULT_LIMITS)


async def test_adaptation_is_written_to_agent_actions(db: Database) -> None:
    registry, session = await _live_session(db)
    coach = SessionCoach(FakeHub(), registry, FakeBridge(), db_provider=lambda: db)
    await coach.on_event({"type": "hr", "session_id": session.session_id, "bpm": 125})
    await coach.drain()
    await asyncio.gather(*writes._tasks)
    rows = await db.fetch("SELECT agent, action, detail FROM agent_actions WHERE session_id = %s",
                          (uuid.UUID(session.session_id),))
    assert [(r["agent"], r["action"]) for r in rows] == [("coach", "extend_rest")]
    assert rows[0]["detail"]["applied_rest_s"] <= DEFAULT_LIMITS["max_rest_s"]
