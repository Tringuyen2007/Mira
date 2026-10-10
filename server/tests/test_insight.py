"""Insight agent: the five SQL rules on crafted data, dedupe, AI path + fallback, number check, notifications,
milestones and proof that every read goes through copilot_ro (the app pool only runs writes.py)."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from server.agents import insight
from server.agents.insight import Candidate, InsightAgent, evidence_numbers, iso_week
from server.ai import gemini
from server.ai.gemini import FunctionCall, Reply
from server.config import reload_config
from server.db.pool import Database
from server.tests.dbutil import fresh_database, prepare, ro_dsn

WRITES_PY = str(Path(insight.writes.__file__).resolve())


def _patient(conn: psycopg.Connection[Any], pid: str, side: str = "right", per_week: int | None = 5,
             knee_deg: float | None = None) -> None:
    conn.execute("INSERT INTO patients (id, name, affected_side, therapist_id, simulated) "
                 "VALUES (%s, %s, %s, 'okafor', true)", (pid, f"Test {pid}", side))
    targets: dict[str, Any] = {}
    if per_week is not None:
        targets["sessions_per_week"] = per_week
    if knee_deg is not None:
        targets["knee_deg"] = knee_deg
    conn.execute("INSERT INTO prescriptions (patient_id, exercise_id, version, sets, reps, targets, created_by) "
                 "VALUES (%s, 'squat', 1, 3, 10, %s, 'okafor')", (pid, Jsonb(targets)))


def _session(conn: psycopg.Connection[Any], pid: str, ago: float, knee: float = 70.0, load_l: float = 50.0,
             pain_post: int | None = None, exercise: str = "squat") -> None:
    """One session `ago` days before now with 20 constant frames (so p95 knee ≈ knee, load_l_pct = load_l)."""
    row = conn.execute("INSERT INTO sessions (patient_id, exercise_id, started_at, ended_at, pain_post, simulated) "
                       "VALUES (%s, %s, now() - make_interval(days => %s, mins => 30), "
                       "now() - make_interval(days => %s, mins => 28), %s, true) RETURNING id, started_at",
                       (pid, exercise, ago, ago, pain_post)).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO pose_frames (time, patient_id, session_id, exercise_id, knee_l, knee_r, load_l_pct, "
        "trunk_lean, confidence) SELECT %s + i * INTERVAL '100 ms', %s, %s, %s, %s, %s, %s, 8, 0.95 "
        "FROM generate_series(0, 19) i", (row[1], pid, row[0], exercise, knee, knee, load_l))


def _craft(conn: psycopg.Connection[Any]) -> None:
    # depth gain: 60° three+ weeks ago, 80° this week
    _patient(conn, "t_depth")
    for ago in range(0, 28):
        _session(conn, "t_depth", ago, knee=60 if ago >= 15 else 70 if ago >= 7 else 80)
    # flat: nothing moves
    _patient(conn, "t_flat")
    for ago in range(1, 22):
        _session(conn, "t_flat", ago, knee=70, load_l=50)
    # asymmetry rising on a patient with no affected side (so no compensation metric)
    _patient(conn, "t_asym", side="none")
    for ago, load in zip(range(5, 0, -1), (52, 54, 56, 58, 60), strict=True):
        _session(conn, "t_asym", ago, knee=70, load_l=load)
    # compensation: right leg load 48 → 40 while depth 60 → 80, pain after back-to-back days
    _patient(conn, "t_comp")
    for ago, load, knee, pain in zip(range(5, 0, -1), (52, 54, 56, 58, 60), (60, 65, 70, 75, 80), (3, 6, 3, 6, 3),
                                     strict=True):
        _session(conn, "t_comp", ago, knee=knee, load_l=load, pain_post=pain)
    # pain: one recent session at 6 / another patient only at 4
    _patient(conn, "t_pain")
    _session(conn, "t_pain", 2, pain_post=6)
    _patient(conn, "t_lowpain")
    _session(conn, "t_lowpain", 2, pain_post=4)
    # missed days: 7 session days out of 10 prescribed in the last 14 full days (first session 20 days ago)
    _patient(conn, "t_missed")
    for ago in (20, 1, 2, 3, 5, 8, 9, 12):
        _session(conn, "t_missed", ago)
    # milestones: target 85 first reached 2 days ago; t_oldgoal reached its 75 target 20 days ago
    _patient(conn, "t_goal", knee_deg=85)
    for ago, knee in ((6, 70), (4, 80), (2, 86), (1, 87)):
        _session(conn, "t_goal", ago, knee=knee)
    _patient(conn, "t_oldgoal", knee_deg=75)
    for ago in (20, 10, 2):
        _session(conn, "t_oldgoal", ago, knee=80)


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            _craft(conn)
            # the refresh policy may already have materialized part of the window; make it whole
            conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM memory_events")
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand-in for F7's server.notify.notifications.create (records calls, returns an id)."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> int:
        calls.append(kwargs)
        return len(calls)

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    return calls


def _by(cands: list[Candidate], pid: str) -> dict[str, Candidate]:
    return {c.kind: c for c in cands if c.patient_id == pid}


# ---- step 1 rules -------------------------------------------------------------------------------------------

async def test_depth_gain_rule(db: Database) -> None:
    cands = await InsightAgent(db, use_ai=False).find_candidates()
    gain = _by(cands, "t_depth")["depth_gain"]
    assert gain.exercise_id == "squat"
    assert gain.evidence["gain_deg"] >= 10 and gain.evidence["to_deg"] > gain.evidence["from_deg"]
    assert gain.note.startswith("Squat depth up ") and "over 3 weeks" in gain.note
    assert gain.dedupe_key == f"t_depth:depth_gain:{iso_week()}"
    assert _by(cands, "t_flat") == {}


async def test_asymmetry_rule(db: Database) -> None:
    kinds = _by(await InsightAgent(db, use_ai=False).find_candidates(), "t_asym")
    asym = kinds["asymmetry_worse"]
    assert asym.evidence["avg_asymmetry"] == [2, 4, 6, 8, 10]
    assert asym.evidence["session_days"] == 5
    assert "compensation" not in kinds  # no affected side → no affected-leg load


async def test_compensation_rule(db: Database) -> None:
    comp = _by(await InsightAgent(db, use_ai=False).find_candidates(), "t_comp")["compensation"]
    ev = comp.evidence
    assert ev["affected_load_pct"] == [48, 46, 44, 42, 40]
    assert [round(x) for x in ev["p95_knee"]] == [60, 65, 70, 75, 80]
    assert ev["pain_post"] == [3, 6, 3, 6, 3]
    assert comp.note.startswith("Affected-leg load fell 48% → 40% over 5 session days")
    assert comp.severity == "high"


async def test_compensation_subsumes_asymmetry_for_the_same_exercise(db: Database, dsn: str) -> None:
    agent = InsightAgent(db, use_ai=False)
    kinds = _by(await agent.find_candidates(), "t_comp")
    assert "compensation" in kinds and "asymmetry_worse" not in kinds  # its asymmetry rises too (2 → 10)
    # A compensation note written earlier this week also suppresses a later asymmetry candidate (same exercise).
    week = iso_week()
    asym = Candidate("t_asym", "asymmetry_worse", "squat", {"exercise_id": "squat"}, "x",
                     dedupe_key=f"t_asym:asymmetry_worse:{week}")
    other = Candidate("t_asym", "asymmetry_worse", "bridge", {"exercise_id": "bridge"}, "x",
                      dedupe_key=f"t_asym:asymmetry_worse:{week}")
    assert await agent.new_candidates([asym]) == [asym]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO memory_events (patient_id, kind, message, evidence, dedupe_key, agent) "
                     "VALUES ('t_asym', 'compensation', 'x', %s, %s, 'insight')",
                     (Jsonb({"exercise_id": "squat"}), f"t_asym:compensation:{week}"))
    assert await agent.new_candidates([asym]) == []
    assert await agent.new_candidates([other]) == [other]


async def test_pain_rule(db: Database) -> None:
    cands = await InsightAgent(db, use_ai=False).find_candidates()
    pain = _by(cands, "t_pain")["pain"]
    assert pain.evidence["max_pain_recent"] == 6 and pain.evidence["high_pain_sessions"] == 1
    assert pain.note == "Pain after exercise reached 6/10 in the last 7 days (1 session at 5 or more)."
    assert "pain" not in _by(cands, "t_lowpain")


async def test_missed_days_rule(db: Database) -> None:
    cands = await InsightAgent(db, use_ai=False).find_candidates()
    missed = _by(cands, "t_missed")["missed_days"]
    assert missed.evidence == {"window_days": 14, "sessions_per_week": 5, "prescribed_days": 10, "session_days": 7,
                               "missed": 3}
    assert missed.note == "3 of 10 prescribed session days missed in the last 14 days."
    assert "missed_days" not in _by(cands, "t_depth")  # trains every day


# ---- dedupe ---------------------------------------------------------------------------------------------------

async def test_dedupe_skips_existing_keys(db: Database, dsn: str) -> None:
    agent = InsightAgent(db, use_ai=False)
    first = await agent.run()
    assert first["written"] == first["new"] == first["candidates"] > 0
    second = await agent.run()
    assert second["candidates"] == first["candidates"] and second["new"] == 0 and second["written"] == 0
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT dedupe_key, kind, message, evidence FROM memory_events").fetchall()
    assert len(rows) == first["written"]
    keys = {r[0] for r in rows}
    assert f"t_comp:compensation:{iso_week()}" in keys
    comp = next(r for r in rows if r[0] == f"t_comp:compensation:{iso_week()}")
    assert comp[1] == "compensation" and comp[3]["note_source"] == "sql" and comp[3]["pain_post"] == [3, 6, 3, 6, 3]


# ---- step 2: agent ------------------------------------------------------------------------------------------

async def _candidate(db: Database, pid: str, kind: str) -> Candidate:
    return _by(await InsightAgent(db, use_ai=False).find_candidates(), pid)[kind]


async def test_ai_note_in_fake_mode_uses_tools_and_stores_their_evidence(db: Database) -> None:
    cand = await _candidate(db, "t_comp", "compensation")
    message, evidence, source = await InsightAgent(db).investigate(cand)
    assert source == "ai"
    assert message.startswith(cand.note) and message.endswith("5 sessions in the last 21 days.")
    [step] = evidence["investigation"]
    assert step["tool"] == "get_patient_summary" and step["result"]["sessions_last_21_days"] == 5
    assert "phone" not in str(step["result"])


async def test_ai_unavailable_falls_back_to_sql_note(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DISABLED", "1")
    reload_config()
    cand = await _candidate(db, "t_depth", "depth_gain")
    message, evidence, source = await InsightAgent(db).investigate(cand)
    assert (message, source) == (cand.note, "sql") and evidence == cand.evidence


async def test_number_check_rejects_invented_numbers(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    discarded: list[Reply] = []

    async def discard(reply: Reply) -> None:
        discarded.append(reply)

    monkeypatch.setattr(gemini, "discard", discard)
    monkeypatch.setitem(gemini._fakes, "insight", lambda m, t: Reply(function_call=FunctionCall(
        "write_note", {"message": "Depth rose 47° in 9 weeks with load down to 31%."})))
    cand = await _candidate(db, "t_comp", "compensation")
    message, _evidence, source = await InsightAgent(db).investigate(cand)
    assert (message, source) == (cand.note, "sql") and len(discarded) == 1


async def test_tools_are_scoped_to_the_candidate_patient(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> Reply:
        results = [m for m in messages if m["role"] == "tool"]
        seen.extend(results)
        if not results:
            return Reply(function_call=FunctionCall("get_patient_summary", {"patient_id": "maya"}))
        if len(results) == 1:
            return Reply(function_call=FunctionCall("run_sql", {"query": "sessions", "days": 7}))
        return Reply(text="Pain after exercise reached 6/10 recently.")

    monkeypatch.setitem(gemini._fakes, "insight", fake)
    cand = await _candidate(db, "t_pain", "pain")
    message, evidence, source = await InsightAgent(db).investigate(cand)
    assert seen[0]["content"] == {"error": "only the candidate's patient can be inspected"}
    assert seen[-1]["content"]["data"][0]["pain_post"] == 6
    assert source == "ai" and message == "Pain after exercise reached 6/10 recently."
    assert [s["tool"] for s in evidence["investigation"]] == ["run_sql"]


async def test_agent_stops_after_max_steps(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> Reply:
        calls.append(1)
        return Reply(function_call=FunctionCall("run_sql", {"query": "form_flags"}))

    monkeypatch.setitem(gemini._fakes, "insight", fake)
    cand = await _candidate(db, "t_depth", "depth_gain")
    message, _evidence, source = await InsightAgent(db).investigate(cand)
    assert len(calls) == insight.MAX_STEPS and (message, source) == (cand.note, "sql")


def test_evidence_numbers_include_dates_and_nested_values() -> None:
    nums = evidence_numbers({"a": [1.5, {"b": 48}], "day": "2026-10-09", "flag": True, "s": "x"})
    assert sorted(nums) == [1.5, 9.0, 10.0, 48.0]


# ---- notifications + milestones --------------------------------------------------------------------------------

async def test_high_severity_kinds_notify_and_milestones(db: Database, notified: list[dict[str, Any]]) -> None:
    result = await InsightAgent(db).run()
    insights = [c for c in notified if c["kind"] == "insight"]
    kinds = {c["dedupe_key"].split(":")[2] for c in insights}
    assert kinds == {"compensation", "pain"}
    comp = next(c for c in insights if c["patient_id"] == "t_comp")
    assert comp["severity"] == "high" and comp["therapist_id"] == "okafor"
    assert comp["source_table"] == "memory_events" and comp["dedupe_key"] == f"insight:t_comp:compensation:{iso_week()}"
    milestones = [c for c in notified if c["kind"] == "milestone"]
    assert [(m["patient_id"], m["severity"], m["dedupe_key"]) for m in milestones] == [
        ("t_goal", "low", "milestone:t_goal:squat:85")]
    assert "85°" in milestones[0]["title"]
    assert result["milestones"] == 1 and result["notifications"] == len(insights)


async def test_notify_is_a_noop_without_the_notifications_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "server.notify.notifications", None)  # import → ImportError
    assert await insight.notify(kind="milestone") is None


# ---- read-only proof ------------------------------------------------------------------------------------------

def _called_from_writes() -> bool:
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename == WRITES_PY:
            return True
        frame = frame.f_back  # type: ignore[assignment]
    return False


async def test_insight_reads_only_through_copilot_ro(db: Database, monkeypatch: pytest.MonkeyPatch,
                                                     notified: list[dict[str, Any]]) -> None:
    """The app pool fails every statement that doesn't come from a fixed writes.py function."""
    app_statements: list[str] = []
    real = {name: getattr(db, name) for name in ("fetch", "fetchrow", "execute")}

    def guarded(name: str) -> Any:
        async def call(sql: Any, *args: Any, **kwargs: Any) -> Any:
            if not _called_from_writes():
                raise AssertionError(f"app pool used outside writes.py: {str(sql)[:80]}")
            app_statements.append(str(sql))
            return await real[name](sql, *args, **kwargs)
        return call

    real_connection = db.connection

    def connection() -> Any:
        if not _called_from_writes():
            raise AssertionError("app connection used outside writes.py")
        return real_connection()

    for name in real:
        monkeypatch.setattr(db, name, guarded(name))
    monkeypatch.setattr(db, "connection", connection)

    with pytest.raises(AssertionError):  # the trap works
        await db.fetch("SELECT 1")
    result = await InsightAgent(db).run()
    assert result["written"] > 0 and result["ai_notes"] == result["written"] and not result["errors"]
    assert app_statements and all(s.startswith("INSERT INTO memory_events") for s in app_statements)


async def test_insight_without_copilot_dsn_is_skipped(dsn: str) -> None:
    database = Database(dsn)
    await database.open()
    try:
        result = await InsightAgent(database).run()
    finally:
        await database.close()
    assert result["written"] == 0 and "COPILOT_DSN" in result["errors"][0]
