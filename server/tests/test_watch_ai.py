"""AI context for the watch: the analyst sees hr_recovery, rotation_sets and 7 days of tremor; Insight's
tremor-trend rule (mild+strong minutes up ≥ 30 % week over week) writes a memory event and notifies the therapist;
the Copilot prompt and the guard know the new tables."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest

from server.agents.insight import InsightAgent, iso_week
from server.agents.session_analyst import SessionAnalyst
from server.agents.sql_guard import check_sql
from server.ai.prompts import load_prompt
from server.db.pool import Database
from server.state import SessionRegistry
from server.tests.dbutil import fresh_database, prepare, ro_dsn


def _tremor(conn: psycopg.Connection[Any], pid: str, ago: int, mild: int, strong: int = 0,
            source: str = "device") -> None:
    # Days relative to the database's current_date (the rule's clock), not the test machine's local date.
    conn.execute("INSERT INTO tremor_daily (patient_id, day, none_min, slight_min, mild_min, strong_min, unknown_min, "
                 "dyskinesia, source) VALUES (%s, current_date - %s::int, 600, 100, %s, %s, 30, 'few', %s)",
                 (pid, ago, mild, strong, source))


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            for pid in ("t_trem", "t_flat", "t_sparse"):
                conn.execute("INSERT INTO patients (id, name, therapist_id, simulated) "
                             "VALUES (%s, %s, 'okafor', true)", (pid, f"Test {pid}"))
            for ago in range(14):  # this week 50+10 a day, last week 30+10: +50 %
                _tremor(conn, "t_trem", ago, 50 if ago < 7 else 30, 10, "simulated")
                _tremor(conn, "t_flat", ago, 30)
            for ago in (1, 2, 8, 9):  # only 2 days a week: not enough
                _tremor(conn, "t_sparse", ago, 90 if ago < 7 else 10)
            for ago in range(7):
                _tremor(conn, "maya", ago, 20)
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    try:
        yield database
    finally:
        await database.close()


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


async def test_analyst_context_has_the_watch_data(dsn: str, db: Database) -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "forearm_rotation")
    sid = session.session_id
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (%s, 'maya', 'forearm_rotation', now() - interval '10 minutes', now())", (sid,))
        conn.execute("INSERT INTO reps (session_id, rep, set_no, quality, rom) VALUES (%s, 1, 1, 1.0, 82), "
                     "(%s, 2, 1, 0.5, 62)", (sid, sid))
        conn.execute("INSERT INTO hr_recovery (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm) "
                     "VALUES (%s, 'maya', 1, 126, 104, 22)", (sid,))
        conn.execute("INSERT INTO rotation_sets (session_id, patient_id, exercise_id, set_index, angles, best_deg, "
                     "avg_deg, goal_deg) VALUES (%s, 'maya', 'forearm_rotation', 1, '{82,62}', 82, 72, 80)", (sid,))
    analyst = SessionAnalyst(FakeHub(), registry, db_provider=lambda: db, notifications_provider=lambda: None,
                             delay_s=0)
    data, _, _ = await analyst._gather(db, sid, "maya", "forearm_rotation")
    assert data["hr_recovery"] == [{"set": 1, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22}]
    assert data["rotation_sets"] == [{"exercise_id": "forearm_rotation", "set": 1, "reps": 2, "best_deg": 82.0,
                                      "avg_deg": 72.0, "goal_deg": 80.0}]
    assert len(data["tremor_7d"]) == 7 and data["tremor_7d"][0]["mild_min"] == 20
    assert set(data["tremor_7d"][0]) == {"day", "none_min", "slight_min", "mild_min", "strong_min", "unknown_min",
                                         "dyskinesia", "source"}
    payload = await analyst.analyze(sid, "maya", "forearm_rotation")
    assert payload is not None and payload["reps_total"] == 2  # fake mode still works with the bigger context


async def test_tremor_trend_rule(db: Database) -> None:
    cands = [c for c in await InsightAgent(db, use_ai=False).find_candidates() if c.kind == "tremor_trend"]
    assert [c.patient_id for c in cands] == ["t_trem"]
    ev = cands[0].evidence
    assert (ev["this_week_min"], ev["last_week_min"], ev["rise_pct"]) == (420, 280, 50)
    assert ev["days_this_week"] == 7 and ev["days_last_week"] == 7 and ev["simulated_days"] == 14
    assert cands[0].note == ("Mild or strong tremor minutes rose 50% week over week (280 → 420 min). "
                             "Includes simulated watch data.")
    assert cands[0].severity == "high" and cands[0].dedupe_key == f"t_trem:tremor_trend:{iso_week()}"


async def test_tremor_trend_writes_memory_and_notifies(db: Database, dsn: str,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def create(kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                     body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                     dedupe_key: str | None) -> int:
        calls.append({"kind": kind, "severity": severity, "patient_id": patient_id, "title": title,
                      "dedupe_key": dedupe_key})
        return len(calls)

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    result = await InsightAgent(db, use_ai=False).run()
    assert result["errors"] == []
    with psycopg.connect(dsn) as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM memory_events WHERE patient_id = 't_trem'").fetchall()]
    assert kinds == ["tremor_trend"]
    [note] = [c for c in calls if c["patient_id"] == "t_trem"]
    assert note["kind"] == "insight" and note["severity"] == "high"
    assert note["dedupe_key"] == f"insight:t_trem:tremor_trend:{iso_week()}" and "tremor rising" in note["title"]


def test_copilot_prompt_and_guard_know_the_watch_tables() -> None:
    prompt = load_prompt("copilot")
    for table in ("hr_recovery", "rotation_sets", "tremor_daily"):
        assert f"- {table}:" in prompt
        assert table in check_sql(f"SELECT * FROM {table} LIMIT 5")
    assert "simulated" in load_prompt("analysis") and "tremor_7d" in load_prompt("analysis")
