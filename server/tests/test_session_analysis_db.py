"""session_analyses: upsert is idempotent-on-conflict, patient_sessions and previous_sessions_stats (§5.2)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from server.db import queries, writes
from server.db.pool import Database
from server.tests.dbutil import fresh_database, prepare, ro_dsn

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def _insert_session(dsn: str, session_id: str, *, started_at: datetime, ended_at: datetime | None,
                    exercise_id: str = "squat", simulated: bool = False) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at, simulated) "
            "VALUES (%s, 'maya', %s, %s, %s, %s)",
            (session_id, exercise_id, started_at, ended_at, simulated))


def _insert_reps(dsn: str, session_id: str, reps: list[tuple[float, float, float]]) -> None:
    """reps: list of (quality, rom, asym_avg)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i, (quality, rom, asym_avg) in enumerate(reps, start=1):
            conn.execute(
                "INSERT INTO reps (session_id, rep, set_no, quality, rom, asym_avg) "
                "VALUES (%s, %s, 1, %s, %s, %s)", (session_id, i, quality, rom, asym_avg))


def _run_db(dsn: str, coro_fn: Any) -> Any:
    async def run() -> Any:
        db = Database(dsn, ro_dsn(dsn))
        await db.open()
        try:
            return await coro_fn(db)
        finally:
            await db.close()

    return asyncio.run(run())


HIGHLIGHTS = [{"kind": "rom", "text": "Knee bend improved", "delta": 5}]
FLAGGED = [{"set": 1, "rep": 2, "rule_id": "knee_valgus", "label": "Watch your knee", "severity": "minor",
           "t": 12.5}]
COMPARISON = {"prev_sessions": 2, "rom_delta_deg": 4.0, "asym_delta_pct": -1.5, "good_rep_rate_delta": 0.1}


def _upsert(dsn: str, session_id: str, *, spoken: str = "Nice work today.", note: str = "draft note",
           form_score: int | None = 82, source: str = "ai") -> bool:
    return _run_db(dsn, lambda db: writes.upsert_session_analysis(
        db, session_id, form_score=form_score, reps_total=10, good_reps=8, sets_done=2, spoken=spoken, note=note,
        highlights=HIGHLIGHTS, next_focus="Keep knees aligned", flagged_moments=FLAGGED, comparison=COMPARISON,
        audio_url="/audio/abc.mp3", source=source, model="gemini-test"))


def test_upsert_is_idempotent_and_overwrites_with_latest_values(dsn: str) -> None:
    session_id = str(uuid.uuid4())
    _insert_session(dsn, session_id, started_at=NOW, ended_at=NOW + timedelta(minutes=10))
    _insert_reps(dsn, session_id, [(1.0, 90.0, 2.0)] * 3)

    assert _upsert(dsn, session_id, spoken="First pass.", form_score=70) is True
    assert _upsert(dsn, session_id, spoken="Second pass, final.", form_score=91) is True

    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT spoken, form_score FROM session_analyses WHERE session_id = %s",
                            (session_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("Second pass, final.", 91)

    analysis = _run_db(dsn, lambda db: queries.session_analysis(db, session_id))
    assert analysis is not None
    assert analysis["spoken"] == "Second pass, final." and analysis["form_score"] == 91
    assert analysis["highlights"] == HIGHLIGHTS and analysis["comparison"] == COMPARISON
    assert analysis["source"] == "ai" and analysis["model"] == "gemini-test"


def test_session_analysis_returns_none_when_missing(dsn: str) -> None:
    assert _run_db(dsn, lambda db: queries.session_analysis(db, str(uuid.uuid4()))) is None


def test_patient_sessions_returns_ended_non_simulated_with_analysis(dsn: str) -> None:
    ended_id = str(uuid.uuid4())
    _insert_session(dsn, ended_id, started_at=NOW, ended_at=NOW + timedelta(minutes=10))
    _insert_reps(dsn, ended_id, [(1.0, 90.0, 2.0)] * 3)
    long_spoken = "x" * 200
    _upsert(dsn, ended_id, spoken=long_spoken, form_score=77)

    unfinished_id = str(uuid.uuid4())
    _insert_session(dsn, unfinished_id, started_at=NOW, ended_at=None)

    simulated_id = str(uuid.uuid4())
    _insert_session(dsn, simulated_id, started_at=NOW, ended_at=NOW + timedelta(minutes=10), simulated=True)

    rows = _run_db(dsn, lambda db: queries.patient_sessions(db, "maya", limit=50))
    ids = {str(r["id"]) for r in rows}
    assert ended_id in ids
    assert unfinished_id not in ids
    assert simulated_id not in ids

    row = next(r for r in rows if str(r["id"]) == ended_id)
    assert row["form_score"] == 77 and row["good_reps"] == 8 and row["reps_total"] == 10
    assert row["spoken_preview"] == long_spoken[:140] and len(row["spoken_preview"]) == 140


def test_patient_sessions_newest_first_with_before_cursor(dsn: str) -> None:
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    _insert_session(dsn, older_id, started_at=NOW - timedelta(days=5), ended_at=NOW - timedelta(days=5, minutes=-10))
    _insert_session(dsn, newer_id, started_at=NOW - timedelta(days=1), ended_at=NOW - timedelta(days=1, minutes=-10))

    rows = _run_db(dsn, lambda db: queries.patient_sessions(db, "maya", limit=50))
    order = [str(r["id"]) for r in rows]
    assert order.index(newer_id) < order.index(older_id)

    before_rows = _run_db(dsn, lambda db: queries.patient_sessions(
        db, "maya", limit=50, before=NOW - timedelta(days=1)))
    before_ids = {str(r["id"]) for r in before_rows}
    assert newer_id not in before_ids
    assert older_id in before_ids


def test_previous_sessions_stats_averages_older_sessions_and_ignores_current(dsn: str) -> None:
    exercise_id = f"stats_squat_{uuid.uuid4().hex[:8]}"

    older1 = str(uuid.uuid4())
    _insert_session(dsn, older1, started_at=NOW - timedelta(hours=3),
                    ended_at=NOW - timedelta(hours=3) + timedelta(minutes=10), exercise_id=exercise_id)
    _insert_reps(dsn, older1, [(1.0, 80.0, 2.0), (1.0, 85.0, 3.0), (0.0, 90.0, 4.0)])  # good_rate = 2/3

    older2 = str(uuid.uuid4())
    _insert_session(dsn, older2, started_at=NOW - timedelta(hours=2),
                    ended_at=NOW - timedelta(hours=2) + timedelta(minutes=10), exercise_id=exercise_id)
    _insert_reps(dsn, older2, [(1.0, 100.0, 1.0), (1.0, 100.0, 1.0)])  # good_rate = 1.0
    assert _upsert(dsn, older2, form_score=65) is True

    current = str(uuid.uuid4())
    _insert_session(dsn, current, started_at=NOW, ended_at=NOW + timedelta(minutes=10), exercise_id=exercise_id)
    _insert_reps(dsn, current, [(1.0, 999.0, 999.0)])  # must never be counted

    stats = _run_db(dsn, lambda db: queries.previous_sessions_stats(db, "maya", exercise_id, current, limit=5))
    assert stats["count"] == 2
    assert stats["avg_good_rep_rate"] == pytest.approx((2 / 3 + 1.0) / 2)
    assert stats["avg_rom_deg"] == pytest.approx((80.0 + 85.0 + 90.0 + 100.0 + 100.0) / 5)
    assert stats["avg_asym_pct"] == pytest.approx((2.0 + 3.0 + 4.0 + 1.0 + 1.0) / 5)
    assert stats["last_form_score"] == 65


def test_previous_sessions_stats_is_all_none_with_no_history(dsn: str) -> None:
    exercise_id = f"stats_lonely_{uuid.uuid4().hex[:8]}"
    current = str(uuid.uuid4())
    _insert_session(dsn, current, started_at=NOW, ended_at=NOW + timedelta(minutes=10), exercise_id=exercise_id)

    stats = _run_db(dsn, lambda db: queries.previous_sessions_stats(db, "maya", exercise_id, current, limit=5))
    assert stats == {"count": 0, "avg_good_rep_rate": None, "avg_rom_deg": None, "avg_asym_pct": None,
                     "last_form_score": None}
