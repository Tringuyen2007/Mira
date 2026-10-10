"""Against the local Docker TimescaleDB: writer, rollup, time zones, roles, writes, sim → Data Guard → compression."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from server.agents.data_guard import DataGuard
from server.db import queries, writes
from server.db.persist import SessionPersistence
from server.db.pool import Database, DBError
from server.db.writer import FrameWriter
from server.sim.simulate_history import simulate
from server.state import SessionRegistry
from server.tests.dbutil import fresh_database, prepare, ro_dsn

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TZ = ZoneInfo("America/Chicago")


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture(scope="module")
def sim_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        stats = simulate(url, seed=42, days=6, patients=3)
        assert 0 < stats["frames_with_landmarks"] <= stats["frames"]  # story patients: squat landmarks
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    yield database
    await database.close()


def _frame(t: float, session_id: str, knee: float = 80.0, conf: float = 0.95) -> dict:
    frame = json.loads((SAMPLES / "engine_frame.json").read_text())
    frame.update({"t": t, "session_id": session_id, "confidence_avg": conf})
    frame["angles"]["knee_l"] = knee
    return frame


def _write_frames(dsn: str, frames: list[dict], patient_id: str = "maya") -> FrameWriter:
    writer = FrameWriter(dsn, patient_provider=lambda: patient_id, flush_interval_s=0.05)
    writer.start()
    for f in frames:
        writer.submit(f)
    writer.stop(timeout_s=5)
    return writer


def test_writer_copy_round_trip_with_landmarks(dsn: str) -> None:
    sid = str(uuid.uuid4())
    now = time.time()
    writer = _write_frames(dsn, [_frame(now + i / 30, sid) for i in range(90)])
    assert writer.stats()["written_total"] == 90 and writer.stats()["dropped"] == 0
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*), min(array_length(landmarks, 1)), min(confidence)::numeric(4,3), "
                           "min(patient_id), min(knee_l) FROM pose_frames WHERE session_id = %s", (sid,)).fetchone()
    sample = json.loads((SAMPLES / "engine_frame.json").read_text())
    assert row[0] == 90 and row[1] == 99
    assert float(row[2]) == pytest.approx(0.95) and row[3] == "maya" and row[4] == pytest.approx(80.0)
    assert sample["joints"][0][0] == pytest.approx(_landmark0(dsn, sid), abs=1e-4)


def _landmark0(dsn: str, sid: str) -> float:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT landmarks[1] FROM pose_frames WHERE session_id = %s LIMIT 1", (sid,)).fetchone()[0]


def test_real_time_aggregate_shows_a_frame_inserted_now(dsn: str) -> None:
    sid = str(uuid.uuid4())
    _write_frames(dsn, [_frame(time.time(), sid, knee=123.0)])
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT max_knee FROM progress_v WHERE patient_id = 'maya' AND exercise_id = 'squat' "
                           "AND day = time_bucket(INTERVAL '1 day', now(), 'America/Chicago')").fetchone()
        mat = conn.execute("SELECT materialized_only FROM timescaledb_information.continuous_aggregates").fetchone()
    assert mat[0] is False
    assert row is not None and row[0] >= 123.0


def test_nine_pm_houston_frame_lands_on_that_local_day(dsn: str) -> None:
    local_day = (datetime.now(TZ) - timedelta(days=3)).date()
    nine_pm = datetime(local_day.year, local_day.month, local_day.day, 21, 0, tzinfo=TZ)
    sid = str(uuid.uuid4())
    _write_frames(dsn, [_frame(nine_pm.timestamp(), sid, knee=111.0)], patient_id="maya")
    with psycopg.connect(dsn) as conn:
        days = conn.execute("SELECT (day AT TIME ZONE 'America/Chicago')::date FROM progress_v "
                            "WHERE patient_id = 'maya' AND max_knee = 111").fetchall()
    assert nine_pm.astimezone(ZoneInfo("UTC")).date() == local_day + timedelta(days=1)  # 02:00 UTC next day
    assert days == [(local_day,)]


def test_copilot_ro_is_read_only_and_cannot_see_alerts(dsn: str) -> None:
    with psycopg.connect(ro_dsn(dsn), autocommit=True) as conn:
        conn.execute("SELECT * FROM progress_v LIMIT 1")
        conn.execute("SELECT * FROM patients LIMIT 1")
        for stmt in ("SELECT * FROM alerts LIMIT 1", "SELECT * FROM agent_actions LIMIT 1",
                     "SELECT * FROM ai_calls LIMIT 1", "SELECT * FROM pose_frames LIMIT 1"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)
        for stmt in ("INSERT INTO memory_events (patient_id, kind, message, evidence) VALUES ('maya','x','y','{}')",
                     "UPDATE patients SET name = 'x'", "DELETE FROM sessions", "CREATE TABLE evil (x int)"):
            with pytest.raises((psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
                conn.execute(stmt)
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "3s"


async def test_ro_fetch_uses_copilot_ro(db: Database) -> None:
    rows = await db.ro_fetch("SELECT current_user AS who, current_setting('transaction_read_only') AS ro")
    assert rows == [{"who": "copilot_ro", "ro": "on"}]
    with pytest.raises(DBError, match="InsufficientPrivilege"):
        await db.ro_fetch("SELECT * FROM alerts")
    with pytest.raises(DBError, match="QueryCanceled"):
        await db.ro_fetch("SELECT pg_sleep(2)", timeout_s=0.3)


async def test_every_write_function_round_trips(db: Database, dsn: str) -> None:
    sid = str(uuid.uuid4())
    now = time.time()
    assert await writes.insert_session(db, sid, "maya", "squat", now)
    assert await writes.insert_rep(db, sid, 1, set_no=1, ended_at=now, quality=1.0, flags=["knee_cave_l"])
    assert await writes.insert_form_event(db, now, "maya", sid, 1, "knee_cave_l", "warning", 0.5)
    assert await writes.record_session_feedback(db, sid, pain_pre=2, effort=5)
    assert await writes.update_session_ai(db, sid, ai_summary="Nice work", ai_note="note") is None
    assert await writes.update_session_ai(db, sid, ai_summary=None, ai_note="note 2") is None
    assert await writes.set_session_shared(db, sid)
    assert await writes.end_session(db, sid, now + 60, reps_done=10)
    session = await queries.get_session(db, sid)
    assert session is not None
    assert (session["pain_pre"], session["effort"], session["ai_summary"], session["ai_note"], session["shared"],
            session["reps_done"]) == (2, 5, "Nice work", "note 2", True, 10)

    alert_id = str(uuid.uuid4())
    assert await writes.insert_alert_step(db, now, alert_id, "maya", sid, "fall", "detected", "device", {"a": 1})
    assert await writes.insert_agent_action(db, "coach", "adapt", "maya", sid, {"rest_s": 60})
    assert await writes.insert_ai_call(db, "coach", "fake", 12, True, False)
    first = await writes.insert_memory_event(db, "maya", "depth_gain", "Deeper", {"p95": [60, 85]}, "maya:dg:W41")
    assert first is not None
    assert await writes.insert_memory_event(db, "maya", "depth_gain", "Deeper", {}, "maya:dg:W41") is None
    pid = await writes.insert_plan_proposal(db, "maya", "copilot", "Rest days", {"x": 1}, {"exercise_id": "squat"})
    assert pid is not None
    decided = await writes.decide_proposal(db, pid, "approved", "okafor")
    assert decided is not None and decided["status"] == "approved" and decided["decided_by"] == "okafor"
    assert await writes.decide_proposal(db, pid, "dismissed", "okafor") is None
    with pytest.raises(ValueError):
        await writes.decide_proposal(db, pid, "bogus", "okafor")

    v2 = await writes.new_prescription_version(db, "maya", "squat", created_by=f"proposal:{pid}", reps=12)
    assert v2 is not None and v2["version"] == 2 and v2["reps"] == 12 and v2["sets"] == 3
    assert v2["limits"]["max_rest_s"] == 120
    active = [r for r in await queries.prescriptions(db, "maya") if r["exercise_id"] == "squat"]
    assert [r["version"] for r in active] == [2]
    with pytest.raises(ValueError):
        await writes.new_prescription_version(db, "maya", "lunge", created_by="okafor")

    assert await writes.insert_data_check(db, "refresh_ran", "ok", {"x": 1})
    nid = await writes.insert_notification(db, "alert", "urgent", "okafor", "maya", "Fall", None, None, "alerts",
                                           alert_id, f"alert:{alert_id}:detected")
    assert nid is not None
    assert await writes.insert_notification(db, "alert", "urgent", "okafor", "maya", "Fall", None, None, "alerts",
                                            alert_id, f"alert:{alert_id}:detected") is None
    assert await writes.mark_notification_read(db, nid)
    assert await writes.mark_notification_ack(db, nid, "okafor")
    assert await writes.mark_all_notifications_read(db, "okafor") == 0
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM form_events WHERE session_id = %s", (sid,)).fetchone()[0] == 1
        assert conn.execute("SELECT acked_by FROM notifications WHERE id = %s", (nid,)).fetchone()[0] == "okafor"


async def test_session_persistence_profile_reps_pain(db: Database) -> None:
    store = SessionPersistence(db)
    registry = SessionRegistry(store=store)
    store.sessions = registry
    session = await registry.start("maya", "squat")
    assert session.profile.first_name == "Maya" and session.profile.contact_name == "Linh"
    assert session.profile.therapist_name == "Dr. Okafor"
    sid = session.session_id
    await store.on_event({"type": "command", "name": "pain", "payload": {"level": 3}, "session_id": sid})
    for set_no in (1, 2):
        for index in (1, 2):
            await store.on_event({"type": "rep", "index": index, "set": set_no, "quality": "fix_form",
                                  "flags": ["knee_cave_l"], "t": time.time(), "session_id": sid})
    await store.on_event({"type": "command", "name": "pain", "payload": {"level": 5, "effort": 6},
                          "session_id": sid})
    await registry.stop(sid)
    row = await queries.get_session(db, sid)
    reps = await queries.session_reps(db, sid)
    flags = await queries.session_form_events(db, sid)
    assert row is not None and row["ended_at"] is not None and row["reps_done"] == 4
    assert (row["pain_pre"], row["pain_post"], row["effort"]) == (3, 5, 6)
    assert [r["rep"] for r in reps] == [1, 2, 3, 4] and reps[0]["quality"] == 0.5 and reps[2]["set_no"] == 2
    assert [f["rule_id"] for f in flags] == ["knee_cave_l"] * 4
    assert (await registry.start("nobody", "squat")).profile.first_name == "the patient"  # unknown → default


async def test_progress_fallback_has_progress_v_shape(db: Database) -> None:
    source, rows = await queries.progress_series(db, "maya", "squat", 30)
    assert source == "progress_v" and rows
    fallback = await db.fetch(queries.PROGRESS_FALLBACK_SQL, {"pid": "maya", "ex": "squat", "days": 30})
    assert [list(r) for r in fallback] == [list(r) for r in rows]
    assert [r["frames"] for r in fallback] == [r["frames"] for r in rows]


# ---- simulated history → Data Guard → compression --------------------------------------------------------------

async def test_small_sim_check3_compress_and_hyperfunctions(sim_dsn: str) -> None:
    db = Database(sim_dsn)
    await db.open()
    try:
        guard = DataGuard(db)
        check3 = await guard.check_raw_vs_summary(days=10)
        assert check3["status"] == "ok", check3
        assert check3["details"]["mismatches"] == 0 and check3["details"]["days_compared"] >= 3

        results = await guard.compress_finished_chunks(min_age_days=1)
        assert results and all(r["status"] == "ok" and r["details"]["fingerprint_match"] for r in results)
        assert all(r["details"]["rows"] > 0 for r in results)
        stats = await queries.compression(db)
        assert stats["compressed_chunks"] == len(results) and stats["ratio"] and stats["ratio"] > 1
        assert (await guard.check_raw_vs_summary(days=10))["status"] == "ok"  # still equal after compression

        session = await db.fetchrow("SELECT id FROM sessions WHERE patient_id = 'maya' AND exercise_id = 'squat' "
                                    "ORDER BY started_at LIMIT 1")
        assert session is not None
        sid = str(session["id"])
        tw = await queries.session_tw_load(db, sid)
        per_set = await queries.session_tw_load_per_set(db, sid)
        points = await queries.session_knee_lttb(db, sid, 300)
        frames = await queries.session_frames(db, sid)
        assert tw is not None and 45 < tw < 70
        assert [r["set_no"] for r in per_set] == [1, 2, 3]
        assert len(points) == 300 and points == sorted(points, key=lambda p: p["time"])
        assert len(frames) == 1200 and len(frames[0]["joints"]) == 33 and len(frames[0]["joints"][0]) == 3

        results = await guard.run_all()
        by_name = {r["check_name"]: r for r in results}
        assert by_name["raw_vs_summary"]["status"] == "ok"
        assert by_name["missing_summaries"]["status"] == "ok"
        assert by_name["frozen_unchanged"]["status"] == "ok"
        assert by_name["refresh_ran"]["status"] in ("ok", "warn")
        checks = await queries.latest_data_checks(db)
        assert {"raw_vs_summary", "compress_verified", "refresh_ran"} <= {c["check_name"] for c in checks}
    finally:
        await db.close()


async def test_check3_catches_a_wrong_summary(sim_dsn: str) -> None:
    """A raw frame added to an already-materialized day must show up as a mismatch."""
    with psycopg.connect(sim_dsn, autocommit=True) as conn:
        day = conn.execute("SELECT min(day) FROM daily_progress WHERE patient_id = 'p02'").fetchone()[0]
        conn.execute("INSERT INTO pose_frames (time, patient_id, session_id, exercise_id, knee_l, knee_r, "
                     "load_l_pct, confidence) VALUES (%s + INTERVAL '12 hours', 'p02', gen_random_uuid(), 'squat', "
                     "150, 150, 50, 0.9)", (day,))
    db = Database(sim_dsn)
    await db.open()
    try:
        result = await DataGuard(db).check_raw_vs_summary(days=10)
        assert result["status"] == "fail" and result["details"]["mismatches"] == 1
    finally:
        await db.close()
        with psycopg.connect(sim_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM pose_frames WHERE knee_l = 150 AND patient_id = 'p02'")


async def test_safe_drop_refuses_without_summaries() -> None:
    with fresh_database() as url:
        prepare(url)
        old = time.time() - 40 * 86400
        _write_frames(url, [_frame(old + i, str(uuid.uuid4())) for i in range(3)])
        db = Database(url)
        await db.open()
        try:
            result = await DataGuard(db).safe_drop(older_than_days=31)
            assert result["status"] == "fail" and "nothing dropped" in result["details"]["reason"]
            with psycopg.connect(url) as conn:
                assert conn.execute("SELECT count(*) FROM pose_frames").fetchone()[0] == 3
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
            result = await DataGuard(db).safe_drop(older_than_days=31)
            assert result["status"] == "ok" and result["details"]["chunks_dropped"] >= 1
            with psycopg.connect(url) as conn:
                assert conn.execute("SELECT count(*) FROM pose_frames").fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM daily_progress_archive").fetchone()[0] == 1
        finally:
            await db.close()


def test_sim_is_deterministic_for_a_seed() -> None:
    with fresh_database() as a, fresh_database() as b:
        out = []
        for url in (a, b):
            prepare(url)
            stats = simulate(url, seed=7, days=3, patients=2)
            with psycopg.connect(url) as conn:
                out.append((stats["frames"], conn.execute(
                    "SELECT md5(string_agg(md5(f::text), '' ORDER BY time, session_id)) FROM pose_frames f"
                ).fetchone()[0]))
        assert out[0] == out[1]


def test_sim_skips_when_already_loaded(sim_dsn: str) -> None:
    assert simulate(sim_dsn, seed=42, days=6, patients=3) == {"skipped": True}


async def test_concurrent_prescription_versions_are_sequential(db: Database) -> None:
    rows = await asyncio.gather(*[writes.new_prescription_version(db, "maya", "glute_bridge", created_by="okafor",
                                                                  reps=10 + i) for i in range(4)])
    versions = sorted(r["version"] for r in rows if r)
    assert len(versions) == 4 and versions == list(range(versions[0], versions[0] + 4))
