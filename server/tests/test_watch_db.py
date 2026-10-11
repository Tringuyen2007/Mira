"""Migration 005 (Apple Watch): new columns and tables, idempotent, copilot_ro grants, and the writes/queries for
hr_recovery, rotation_sets, tremor_daily, the heart-rate limit, tremor monitoring and the watch clock offset."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta

import psycopg
import pytest

from server.agents.sql_guard import check_sql
from server.db import migrate, queries, writes
from server.db.apply_sql import apply_file
from server.db.pool import Database
from server.tests.dbutil import fresh_database, prepare, ro_dsn

WATCH_TABLES = ("hr_recovery", "rotation_sets", "tremor_daily")


def _columns(dsn: str, table: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                            (table,)).fetchall()
    return {r[0] for r in rows}


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


def _session(dsn: str, patient_id: str = "maya", exercise_id: str = "forearm_rotation") -> str:
    sid = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at) VALUES (%s, %s, %s, now())",
                     (sid, patient_id, exercise_id))
    return sid


def _tremor_day(day: date, mild: int = 25) -> dict[str, object]:
    return {"day": day, "none_min": 420, "slight_min": 190, "mild_min": mild, "strong_min": 0, "unknown_min": 60,
            "dyskinesia": "few", "source": "simulated"}


def test_migration_005_adds_columns_and_tables(dsn: str) -> None:
    assert {"patient_id", "accel", "rotation_deg", "quat_w"} <= _columns(dsn, "wrist_motion")
    assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(dsn, "patients")
    assert "watch_clock_offset_ms" in _columns(dsn, "sessions")
    assert {"session_id", "patient_id", "set_index", "peak_bpm", "bpm_60s", "drop_bpm", "time"} <= _columns(
        dsn, "hr_recovery")
    assert {"session_id", "patient_id", "exercise_id", "set_index", "angles", "best_deg", "avg_deg", "goal_deg",
            "time"} <= _columns(dsn, "rotation_sets")
    assert {"patient_id", "day", "none_min", "slight_min", "mild_min", "strong_min", "unknown_min", "dyskinesia",
            "source", "updated_at"} <= _columns(dsn, "tremor_daily")
    with psycopg.connect(dsn) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    assert "007_watch.sql" in names


def test_migration_005_is_idempotent(dsn: str) -> None:
    first = migrate.apply_migrations(dsn)
    assert migrate.apply_migrations(dsn) == first and "007_watch.sql" in first


def test_schema_sql_alone_already_has_the_watch_ddl() -> None:
    with fresh_database() as url:
        apply_file(migrate.DB_DIR / "schema.sql", url)
        assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(url, "patients")
        assert "watch_clock_offset_ms" in _columns(url, "sessions")
        assert {"patient_id", "accel"} <= _columns(url, "wrist_motion")
        for table in WATCH_TABLES:
            assert _columns(url, table), table


def test_copilot_ro_reads_the_watch_tables_and_the_guard_allows_them(dsn: str) -> None:
    with psycopg.connect(ro_dsn(dsn)) as conn:
        for table in WATCH_TABLES:
            conn.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
    for table in WATCH_TABLES:
        assert table in check_sql(f"SELECT * FROM {table}")


async def test_copilot_ro_grant_check_is_clean(db: Database) -> None:
    assert await queries.copilot_ro_grant_problems(db) == []


async def test_hr_recovery_upsert_and_reads(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.upsert_hr_recovery(db, sid, "maya", 1, 126, 104, 22)
    assert await writes.upsert_hr_recovery(db, sid, "maya", 1, 128, 100, 28)  # same set: updated, not duplicated
    assert await writes.upsert_hr_recovery(db, sid, "maya", 2, 130, 110, 20)
    rows = await queries.session_hr_recovery(db, sid)
    assert [(r["set_index"], r["peak_bpm"], r["bpm_60s"], r["drop_bpm"]) for r in rows] == [(1, 128, 100, 28),
                                                                                             (2, 130, 110, 20)]
    recent = await queries.patient_hr_recovery(db, "maya")
    assert str(recent[0]["session_id"]) == sid and {"set_index", "peak_bpm", "time"} <= set(recent[0])


async def test_rotation_set_upsert_and_reads(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.upsert_rotation_set(db, sid, "maya", "forearm_rotation", 1, [62.0, 82.0], 82.0, 72.0, 80.0)
    assert await writes.upsert_rotation_set(db, sid, "maya", "forearm_rotation", 1, [62.0, 84.0], 84.0, 73.0, 80.0)
    [row] = await queries.session_rotation_sets(db, sid)
    assert row["angles"] == pytest.approx([62.0, 84.0]) and row["best_deg"] == pytest.approx(84.0)
    assert row["exercise_id"] == "forearm_rotation" and row["goal_deg"] == pytest.approx(80.0)
    assert str((await queries.patient_rotation_sets(db, "maya"))[0]["session_id"]) == sid


async def test_tremor_days_upsert_and_window(db: Database) -> None:
    today = date.today()
    assert await writes.upsert_tremor_days(db, "maya", [_tremor_day(today), _tremor_day(today - timedelta(days=20))]) == 2
    assert await writes.upsert_tremor_days(db, "maya", [_tremor_day(today, mild=40)]) == 1  # same day: updated
    rows = await queries.patient_tremor_days(db, "maya", 14)
    assert [r["day"] for r in rows] == [today]  # the 20-day-old row is outside the window
    assert rows[0]["mild_min"] == 40 and rows[0]["dyskinesia"] == "few" and rows[0]["source"] == "simulated"


async def test_hr_limit_set_by_and_tremor_flag(db: Database) -> None:
    try:
        assert await writes.set_patient_hr_limit(db, "maya", 120, "okafor") == 120
        assert await writes.set_tremor_monitoring(db, "maya", True) is True
        row = await queries.me_watch_row(db, "maya")
        assert row is not None
        assert row["hr_limit"] == 120 and row["hr_limit_set_by"] == "okafor"
        assert row["hr_limit_set_by_name"] == "Dr. Okafor" and row["therapist_name"] == "Dr. Okafor"
        assert row["tremor_monitoring"] is True
        assert await writes.set_patient_hr_limit(db, "nobody", 120, "okafor") is None
        assert await writes.set_tremor_monitoring(db, "nobody", True) is False
        assert await queries.therapist_name(db, "okafor") == "Dr. Okafor"
        assert await queries.therapist_name(db, "lee") is None
    finally:
        await writes.set_patient_hr_limit(db, "maya", 130, None)
        await writes.set_tremor_monitoring(db, "maya", False)


async def test_watch_clock_offset(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.set_watch_clock_offset(db, sid, -42)
    row = await queries.get_session(db, sid)
    assert row is not None and row["watch_clock_offset_ms"] == -42


async def test_sets_done_today_counts_distinct_sets(dsn: str, db: Database) -> None:
    sid = _session(dsn, exercise_id="wrist_flexion")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for rep, set_no in ((1, 1), (2, 1), (3, 2)):
            conn.execute("INSERT INTO reps (session_id, rep, set_no) VALUES (%s, %s, %s)", (sid, rep, set_no))
    assert (await queries.sets_done_today(db, "maya"))["wrist_flexion"] == 2


async def test_purge_and_export_cover_the_watch_tables(dsn: str, db: Database) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('w_purge', 'Wes Purge', 'okafor')")
    sid = _session(dsn, patient_id="w_purge")
    await writes.upsert_hr_recovery(db, sid, "w_purge", 1, 120, 100, 20)
    await writes.upsert_rotation_set(db, sid, "w_purge", "forearm_rotation", 1, [70.0], 70.0, 70.0, 80.0)
    await writes.upsert_tremor_days(db, "w_purge", [_tremor_day(date.today())])
    export = await queries.patient_export(db, "w_purge")
    assert len(export["hr_recovery"]) == 1 and len(export["rotation_sets"]) == 1 and len(export["tremor_daily"]) == 1
    counts = await writes.purge_patient_data(db, "w_purge")
    assert counts is not None
    assert counts["hr_recovery"] == 1 and counts["rotation_sets"] == 1 and counts["tremor_daily"] == 1
