"""SessionAnalyst (§5.3): on session end, one `session_summary` event, a session_analyses row and a therapist
notification; computed form score; template fallback on AI failure or invented numbers; false starts skipped."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from server.agents import session_analyst
from server.agents.session_analyst import SessionAnalyst, form_score
from server.ai import cue_library, gemini
from server.ai.validators import AI_DRAFT_LABEL
from server.db.pool import Database
from server.state import LiveSession, SessionRegistry
from server.tests.dbutil import fresh_database, prepare, ro_dsn

NOW = datetime.now(UTC).replace(microsecond=0)


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class FakeNotifications:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> int:
        self.created.append(kwargs)
        return len(self.created)


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    gemini.reset_for_tests()
    gemini.register_fake("analyst", session_analyst._fake_analysis)
    cue_library.reload()
    yield
    gemini.register_fake("analyst", session_analyst._fake_analysis)


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def _session_row(dsn: str, sid: str, started_at: datetime, ended_at: datetime | None, *, pain_pre: int | None = None,
                 exercise_id: str = "squat") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at, pain_pre) "
                     "VALUES (%s, 'maya', %s, %s, %s, %s)", (sid, exercise_id, started_at, ended_at, pain_pre))


def _reps(dsn: str, sid: str, qualities: list[float], *, rom: float = 80.0, per_set: int = 6) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i, q in enumerate(qualities, start=1):
            conn.execute("INSERT INTO reps (session_id, rep, set_no, quality, rom, asym_avg) "
                         "VALUES (%s, %s, %s, %s, %s, 5.0)", (sid, i, (i - 1) // per_set + 1, q, rom))


def _form_event(dsn: str, sid: str, rep: int, at: datetime, rule_id: str = "knee_cave_l") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO form_events (time, patient_id, session_id, rep, rule_id, severity, duration_s) "
                     "VALUES (%s, 'maya', %s, %s, %s, 'warning', 0.5)", (at, sid, rep, rule_id))


async def _full_session(dsn: str, *, exercise_id: str = "squat") -> tuple[SessionRegistry, LiveSession]:
    """12 reps over 2 sets (reps 3 and 8 fix_form with a knee_cave_l event), after 2 older ended sessions."""
    registry = SessionRegistry()
    session = await registry.start("maya", exercise_id)
    for days_ago in (3, 2):
        old = str(uuid.uuid4())
        start = NOW - timedelta(days=days_ago)
        _session_row(dsn, old, start, start + timedelta(minutes=10), exercise_id=exercise_id)
        _reps(dsn, old, [1.0] * 6 + [0.5] * 4, rom=74.0)
    started = NOW - timedelta(minutes=10)
    _session_row(dsn, session.session_id, started, NOW, pain_pre=2, exercise_id=exercise_id)
    _reps(dsn, session.session_id, [0.5 if i in (3, 8) else 1.0 for i in range(1, 13)])
    _form_event(dsn, session.session_id, 3, started + timedelta(seconds=40))
    _form_event(dsn, session.session_id, 8, started + timedelta(seconds=200))
    return registry, session


def _analyst(registry: SessionRegistry, db: Any, notes: FakeNotifications | None = None) -> tuple[SessionAnalyst,
                                                                                                   FakeHub]:
    hub = FakeHub()
    return SessionAnalyst(hub, registry, db_provider=lambda: db, notifications_provider=lambda: notes,
                          delay_s=0), hub


def test_form_score_is_mean_quality_half_up() -> None:
    assert form_score([1.0] * 10 + [0.5] * 2) == 92
    assert form_score([]) is None
    assert form_score([0.5, 1.0]) == 75


async def test_on_stop_emits_one_session_summary_stores_row_and_notifies(dsn: str, db: Database) -> None:
    registry, session = await _full_session(dsn, exercise_id="glute_bridge")
    notes = FakeNotifications()
    analyst, hub = _analyst(registry, db, notes)
    registry.on_stop.append(analyst.on_stop)
    await registry.stop(session.session_id)
    await analyst.drain()

    [event] = hub.of("session_summary")
    assert event["session_id"] == session.session_id and event["exercise_id"] == "glute_bridge"
    assert event["exercise_name"] == "Glute bridge"
    assert event["form_score"] == 92 and event["good_reps"] == 10 and event["reps_total"] == 12
    assert event["sets_done"] == 2
    assert event["source"] == "ai" and event["model"]
    assert event["note"].startswith(AI_DRAFT_LABEL)
    assert "Maya" in event["spoken"]
    assert 1 <= len(event["highlights"]) <= 3 and all({"kind", "text"} <= set(h) for h in event["highlights"])
    moments = event["flagged_moments"]
    assert [(m["set"], m["rep"], m["rule_id"]) for m in moments] == [(1, 3, "knee_cave_l"), (2, 8, "knee_cave_l")]
    assert all(m["label"] and m["label"] != "knee_cave_l" for m in moments)
    assert moments[0]["t"] == 40.0 and moments[0]["severity"] == "warning"
    comparison = event["comparison"]
    assert comparison["prev_sessions"] == 2
    assert comparison["rom_delta_deg"] == 6.0
    assert comparison["good_rep_rate_delta"] == pytest.approx(0.23)
    assert set(comparison) == {"prev_sessions", "good_rep_rate_delta", "rom_delta_deg", "asym_delta_pct",
                               "form_score_delta"}
    assert event["audio_url"] is None  # no ElevenLabs key in tests
    json.dumps(event)  # the payload goes over the websocket as-is

    row = await db.fetchrow("SELECT * FROM session_analyses WHERE session_id = %s", (session.session_id,))
    assert row is not None and row["form_score"] == 92 and row["source"] == "ai"
    assert not row["note"].startswith(AI_DRAFT_LABEL)  # stored without the label
    assert row["flagged_moments"] == moments and row["comparison"] == comparison

    [note] = notes.created
    assert note["kind"] == "session_summary" and note["severity"] == "info" and note["therapist_id"] == "okafor"
    assert note["title"] == "Maya finished Glute bridge" and note["body"] == "Form 92/100 · 10/12 good reps"
    assert note["dedupe_key"] == f"session_summary:{session.session_id}"
    assert note["link"] == f"/sessions/{session.session_id}" and note["source_table"] == "session_analyses"


async def test_template_fallback_when_ai_unavailable(dsn: str, db: Database) -> None:
    def boom(messages: Any, tools: Any) -> gemini.Reply:
        raise RuntimeError("down")

    gemini.register_fake("analyst", boom)
    registry, session = await _full_session(dsn, exercise_id="single_leg_stance")
    analyst, hub = _analyst(registry, db)
    payload = await analyst.analyze(session.session_id, "maya", "single_leg_stance")
    assert payload is not None and payload["source"] == "template" and payload["model"] is None
    assert payload["form_score"] == 92
    assert "12 reps" in payload["spoken"] and "10 had good form" in payload["spoken"]
    assert payload["note"].startswith(AI_DRAFT_LABEL)
    assert payload["next_focus"] == "Push your left knee out"
    assert hub.of("session_summary") == [{"type": "session_summary", **payload}]


async def test_invented_number_falls_back_to_template(dsn: str, db: Database) -> None:
    def invents(messages: Any, tools: Any) -> gemini.Reply:
        return gemini.Reply(text=json.dumps({"spoken": "You did 99 reps today!", "note": "Reps: 12.",
                                             "highlights": ["Great work"], "next_focus": None}))

    gemini.register_fake("analyst", invents)
    registry, session = await _full_session(dsn, exercise_id="arm_raise")
    analyst, hub = _analyst(registry, db)
    payload = await analyst.analyze(session.session_id, "maya", "arm_raise")
    assert payload is not None and payload["source"] == "template"
    assert "99" not in payload["spoken"] and payload["spoken"].startswith("Nice work, Maya!")


async def test_false_start_is_skipped(dsn: str, db: Database) -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "heel_raise")
    _session_row(dsn, session.session_id, NOW - timedelta(seconds=5), NOW, exercise_id="heel_raise")
    notes = FakeNotifications()
    analyst, hub = _analyst(registry, db, notes)
    assert await analyst.analyze(session.session_id, "maya", "heel_raise") is None
    assert hub.events == [] and notes.created == []
    assert await db.fetchrow("SELECT 1 FROM session_analyses WHERE session_id = %s", (session.session_id,)) is None


async def test_empty_but_long_session_gets_a_template_analysis(dsn: str, db: Database) -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "bird_dog")
    _session_row(dsn, session.session_id, NOW - timedelta(minutes=2), NOW, exercise_id="bird_dog")
    analyst, hub = _analyst(registry, db)
    payload = await analyst.analyze(session.session_id, "maya", "bird_dog")
    assert payload is not None and payload["reps_total"] == 0 and payload["form_score"] is None
    assert payload["source"] == "template"  # nothing to analyse: no AI call
    assert payload["spoken"].startswith("No reps were counted")
    assert payload["comparison"]["prev_sessions"] == 0 and payload["flagged_moments"] == []
    assert len(hub.of("session_summary")) == 1


async def test_without_a_database_nothing_is_emitted() -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "squat")
    analyst, hub = _analyst(registry, None)
    registry.on_stop.append(analyst.on_stop)
    await registry.stop(session.session_id)
    await analyst.drain()
    assert hub.events == []


async def test_hook_never_raises_into_stop() -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "squat")

    def broken_db() -> Any:
        raise RuntimeError("provider bug")

    analyst = SessionAnalyst(FakeHub(), registry, db_provider=broken_db, delay_s=0)
    registry.on_stop.append(analyst.on_stop)
    await registry.stop(session.session_id)  # must not raise
    await analyst.drain()
    assert await analyst.analyze(session.session_id, "maya", "squat") is None
