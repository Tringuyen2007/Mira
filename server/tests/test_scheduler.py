"""Scheduler: runs on its timers, survives exceptions, cancels cleanly, notifies Data Guard failures, and keeps
event-loop lag low while Data Guard + Insight run against a real local database. Also GET /health/data and
POST /insight/run."""

from __future__ import annotations

import asyncio
import sys
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.agents import data_guard
from server.agents.data_guard import DataGuard
from server.agents.scheduler import Scheduler
from server.db import writes
from server.db.pool import Database
from server.engine_bridge import LagMonitor
from server.sim.simulate_history import simulate
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

WRITES_PY = str(Path(writes.__file__).resolve())
FAST = {"guard_delay_s": 0, "guard_interval_s": 0.05, "insight_delay_s": 0, "insight_interval_s": 0.05}


class FakeDB:
    def __init__(self, available: bool = True, has_ro: bool = True) -> None:
        self.available = available
        self.has_ro = has_ro

    async def ro_fetch(self, sql: str, params: Any = None, **_: Any) -> list[dict[str, Any]]:
        assert sql.startswith("SELECT DISTINCT therapist_id FROM patients")
        return [{"therapist_id": "okafor"}]


class FakeGuard:
    def __init__(self, results: list[dict[str, Any]] | None = None, fail_first: int = 0, hang: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self.hang = hang
        self.results = results or [{"check_name": "raw_vs_summary", "status": "ok", "details": {}}]

    async def run_all(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(3600)
        if self.calls <= self.fail_first:
            raise RuntimeError("boom")
        return self.results


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def create(kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                     body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                     dedupe_key: str | None) -> int:  # F7's exact signature: a wrong keyword raises
        calls.append(dict(kind=kind, severity=severity, therapist_id=therapist_id, patient_id=patient_id,
                          title=title, body=body, link=link, source_table=source_table, source_id=source_id,
                          dedupe_key=dedupe_key))
        return len(calls)

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    return calls


def _counter() -> tuple[list[int], Callable[[], Any]]:
    calls: list[int] = []

    async def job() -> dict[str, Any]:
        calls.append(1)
        return {"written": 0}

    return calls, job


async def test_jobs_run_on_their_timers_and_stop() -> None:
    guard = FakeGuard()
    insight_calls, insight = _counter()
    sched = Scheduler(FakeDB(), guard=guard, insight=insight, guard_enabled=True, insight_enabled=True, **FAST)
    sched.start()
    await asyncio.sleep(0.3)
    await sched.stop()
    assert guard.calls >= 3 and len(insight_calls) >= 3
    stats = sched.stats()
    assert stats["data_guard"]["runs"] == guard.calls and stats["data_guard"]["failures"] == 0
    assert stats["data_guard"]["last_result"] == {"raw_vs_summary": "ok"}
    assert stats["insight"]["last_result"] == {"written": 0}
    calls_after_stop = guard.calls
    await asyncio.sleep(0.15)
    assert guard.calls == calls_after_stop


async def test_first_run_waits_for_the_delay() -> None:
    guard = FakeGuard()
    sched = Scheduler(FakeDB(), guard=guard, insight=_counter()[1], guard_enabled=True, insight_enabled=False,
                      guard_delay_s=0.3, guard_interval_s=10)
    sched.start()
    await asyncio.sleep(0.1)
    assert guard.calls == 0 and sched.stats()["data_guard"]["next_run_at"] > time.time()
    await asyncio.sleep(0.35)
    assert guard.calls == 1
    await sched.stop()


async def test_disabled_jobs_never_run() -> None:
    guard = FakeGuard()
    insight_calls, insight = _counter()
    sched = Scheduler(FakeDB(), guard=guard, insight=insight, guard_enabled=False, insight_enabled=False, **FAST)
    sched.start()
    await asyncio.sleep(0.15)
    await sched.stop()
    assert guard.calls == 0 and insight_calls == []
    assert await sched.run_insight_now() == {"skipped": "INSIGHT_ENABLED is off"}


async def test_loop_survives_exceptions() -> None:
    guard = FakeGuard(fail_first=2)
    sched = Scheduler(FakeDB(), guard=guard, insight=_counter()[1], guard_enabled=True, insight_enabled=False, **FAST)
    sched.start()
    await asyncio.sleep(0.3)
    await sched.stop()
    stats = sched.stats()["data_guard"]
    assert stats["failures"] == 2 and stats["runs"] >= 1 and stats["last_error"] is None


async def test_stop_cancels_a_run_in_progress() -> None:
    guard = FakeGuard(hang=True)
    sched = Scheduler(FakeDB(), guard=guard, insight=_counter()[1], guard_enabled=True, insight_enabled=False, **FAST)
    sched.start()
    await asyncio.sleep(0.05)
    assert sched.stats()["data_guard"]["running"]
    started = time.perf_counter()
    await sched.stop()
    assert time.perf_counter() - started < 0.5
    assert not sched._tasks and not sched.stats()["data_guard"]["running"]
    await sched.stop()  # idempotent


async def test_data_guard_skipped_while_database_unavailable() -> None:
    guard = FakeGuard()
    sched = Scheduler(FakeDB(available=False), guard=guard, guard_enabled=True, insight_enabled=False)
    assert await sched.run_data_guard() is None
    assert guard.calls == 0 and sched.jobs["data_guard"].skipped == 1


async def test_data_guard_skipped_without_copilot_ro() -> None:
    guard = FakeGuard()
    sched = Scheduler(FakeDB(has_ro=False), guard=guard, guard_enabled=True, insight_enabled=False)
    assert await sched.run_data_guard() is None
    assert guard.calls == 0 and sched.jobs["data_guard"].skipped == 1


async def test_data_guard_failures_become_normal_notifications(notified: list[dict[str, Any]]) -> None:
    guard = FakeGuard(results=[
        {"check_name": "refresh_ran", "status": "ok", "details": {}},
        {"check_name": "raw_vs_summary", "status": "fail", "details": {"mismatches": 1}},
        {"check_name": "frozen_unchanged", "status": "error", "details": {"error": "timeout"}},
    ])
    sched = Scheduler(FakeDB(), guard=guard, guard_enabled=True, insight_enabled=False)
    await sched.run_data_guard()
    assert [(n["kind"], n["severity"], n["source_id"]) for n in notified] == [
        ("data_guard", "normal", "raw_vs_summary"), ("data_guard", "normal", "frozen_unchanged")]
    first = notified[0]
    assert first["therapist_id"] == "okafor" and first["patient_id"] is None
    assert first["title"] == "Data Guard: raw_vs_summary fail" and '"mismatches": 1' in first["body"]
    assert first["dedupe_key"].startswith("data_guard:raw_vs_summary:") and first["dedupe_key"].endswith(":okafor")


async def test_on_demand_insight_waits_for_a_scheduled_run() -> None:
    order: list[str] = []
    gate = asyncio.Event()

    async def insight() -> dict[str, Any]:
        order.append("start")
        await gate.wait()
        order.append("end")
        return {"written": 1}

    sched = Scheduler(FakeDB(), guard=FakeGuard(), insight=insight, guard_enabled=False, insight_enabled=True,
                      insight_delay_s=0, insight_interval_s=60)
    sched.start()
    await asyncio.sleep(0.02)
    on_demand = asyncio.create_task(sched.run_insight_now())
    await asyncio.sleep(0.02)
    assert order == ["start"]  # the on-demand pass waits for the scheduled one
    gate.set()
    assert await on_demand == {"written": 1}
    assert order == ["start", "end", "start", "end"]
    await sched.stop()


# ---- real database: Data Guard + Insight on the loop, lag measured ---------------------------------------------

@pytest.fixture(scope="module")
def sim_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        simulate(url, seed=42, days=21, patients=5)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
        yield url


async def test_event_loop_lag_while_guard_and_insight_run(sim_dsn: str, notified: list[dict[str, Any]]) -> None:
    db = Database(sim_dsn, ro_dsn(sim_dsn))
    await db.open()
    monitor = LagMonitor(interval_s=0.02, window=10_000)
    lag_task = asyncio.create_task(monitor.run())
    sched = Scheduler(db, guard_enabled=True, insight_enabled=True, guard_delay_s=0, guard_interval_s=0.2,
                      insight_delay_s=0, insight_interval_s=0.2)
    try:
        sched.start()
        await asyncio.sleep(3.0)  # back-to-back runs every 0.2 s while the monitor samples every 20 ms
        on_demand = await sched.run_insight_now()
    finally:
        await sched.stop()
        lag_task.cancel()
        await asyncio.gather(lag_task, return_exceptions=True)
    stats = sched.stats()
    lag = monitor.stats()
    print(f"\nevent loop lag ms: {lag}; data_guard runs {stats['data_guard']['runs']} "
          f"(last {stats['data_guard']['last_duration_ms']} ms), insight runs {stats['insight']['runs']} "
          f"(last {stats['insight']['last_duration_ms']} ms)")
    try:
        assert stats["data_guard"]["runs"] >= 3 and stats["insight"]["runs"] >= 3
        assert stats["data_guard"]["failures"] == 0 and stats["insight"]["failures"] == 0
        assert lag["samples"] >= 50 and lag["p95"] is not None and lag["p95"] < 50
        assert on_demand is not None and on_demand["new"] == 0  # everything was written by the first pass
        rows = await db.fetch("SELECT kind, evidence->>'note_source' AS source FROM memory_events "
                              "WHERE patient_id = 'maya'")
        assert {"compensation", "depth_gain"} <= {r["kind"] for r in rows}
        assert {r["source"] for r in rows} == {"ai"}
        checks = {r["check_name"]: r["status"] for r in await db.fetch(
            "SELECT DISTINCT ON (check_name) check_name, status FROM data_checks ORDER BY check_name, time DESC")}
        assert checks["raw_vs_summary"] == "ok" and checks["missing_summaries"] == "ok"
        assert any(n["kind"] == "insight" and n["patient_id"] == "maya" for n in notified)
    finally:
        await db.close()


def _called_from_writes() -> bool:
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename == WRITES_PY:
            return True
        frame = frame.f_back  # type: ignore[assignment]
    return False


def _trap_app_pool(db: Database, monkeypatch: pytest.MonkeyPatch, written: list[str]) -> None:
    """The app pool fails every statement that doesn't come from a fixed writes.py function."""
    real = {name: getattr(db, name) for name in ("fetch", "fetchrow", "execute")}

    def guarded(name: str) -> Any:
        async def call(sql: Any, *args: Any, **kwargs: Any) -> Any:
            if not _called_from_writes():
                raise AssertionError(f"app pool used outside writes.py: {str(sql)[:80]}")
            written.append(str(sql))
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


async def test_background_jobs_read_only_through_copilot_ro(sim_dsn: str, monkeypatch: pytest.MonkeyPatch,
                                                            notified: list[dict[str, Any]]) -> None:
    """A full scheduled pass of both jobs (incl. a Data Guard failure notification): every read is copilot_ro;
    the app pool runs only writes.py (archive insert, data_checks, memory_events)."""
    db = Database(sim_dsn, ro_dsn(sim_dsn))
    await db.open()
    written: list[str] = []
    _trap_app_pool(db, monkeypatch, written)
    sched = Scheduler(db, guard_enabled=True, insight_enabled=True)
    real_check = sched.guard.check_frozen_unchanged

    async def failing_check4() -> dict[str, Any]:  # forces the failure-notification path
        result = await real_check()
        return {**result, "status": "fail"}

    monkeypatch.setattr(sched.guard, "check_frozen_unchanged", failing_check4)
    try:
        with pytest.raises(AssertionError):  # the trap works
            await db.fetch("SELECT 1")
        results = await sched.run_data_guard()
        insight_result = await sched.run_insight_now()
    finally:
        await db.close()
    assert results is not None and {r["check_name"]: r["status"] for r in results} == {
        "refresh_ran": results[0]["status"], "missing_summaries": "ok", "raw_vs_summary": "ok",
        "frozen_unchanged": "fail"}
    assert results[0]["status"] in ("ok", "warn")
    assert insight_result is not None and not insight_result["errors"]
    assert any(n["kind"] == "data_guard" and n["therapist_id"] == "okafor" for n in notified)
    assert {" ".join(w.split()[:3]) for w in written} <= {"INSERT INTO daily_progress_archive",
                                                         "INSERT INTO data_checks", "INSERT INTO memory_events"}
    assert sum(w.startswith("INSERT INTO data_checks") for w in written) == 4


async def test_refresh_check_failure_details_come_from_job_stats(sim_dsn: str, monkeypatch: pytest.MonkeyPatch
                                                               ) -> None:
    """copilot_ro can't see job_errors (owner-only rows), so a failing check 1 reports job_stats fields."""
    with psycopg.connect(sim_dsn, autocommit=True) as conn:
        job_id = conn.execute("SELECT job_id FROM timescaledb_information.jobs "
                              "WHERE proc_name = 'policy_refresh_continuous_aggregate'").fetchone()[0]
        conn.execute(f"CALL run_job({job_id})")
    monkeypatch.setattr(data_guard, "REFRESH_MAX_AGE_MIN", -1)  # the last success now counts as stale
    db = Database(sim_dsn, ro_dsn(sim_dsn))
    await db.open()
    try:
        result = await DataGuard(db).check_refresh_ran()
    finally:
        await db.close()
    details = result["details"]
    assert result["status"] == "fail" and details["last_run_status"] == "Success"
    assert details["last_run_started"] and details["job_status"] and "job_errors" in details["errors_in"]


# ---- HTTP: /health/data and /insight/run ----------------------------------------------------------------------

def test_health_data_without_database(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    body = client.get("/health/data", headers=AUTH).json()
    assert body["status"] == "unavailable" and body["checks"] == [] and "no database" in body["reason"]
    assert client.get("/health/data").status_code == 401
    assert client.post("/insight/run", headers=AUTH).status_code == 503


@pytest.fixture(scope="module")
def api_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def test_health_data_reports_latest_check_per_name(make_client: Callable[..., TestClient], api_dsn: str) -> None:
    client = make_client(TIGER_DSN=api_dsn, COPILOT_DSN=ro_dsn(api_dsn), INSIGHT_ENABLED="1")
    body = client.get("/health/data", headers=AUTH).json()
    assert body["status"] == "unavailable" and body["reason"] == "the Data Guard has not run yet"
    assert body["scheduler"]["enabled"] is False  # DATA_GUARD_ENABLED=0 in the test baseline

    async def insert(rows: list[tuple[str, str, dict[str, Any], float]]) -> None:
        db = Database(api_dsn)
        await db.open()
        for name, status, details, ago in rows:
            await writes.insert_data_check(db, name, status, details, time=time.time() - ago)
        await db.close()

    asyncio.run(insert([("raw_vs_summary", "fail", {"mismatches": 2}, 900),
                        ("raw_vs_summary", "ok", {"mismatches": 0}, 10),
                        ("refresh_ran", "ok", {}, 10)]))
    body = client.get("/health/data", headers=AUTH).json()
    assert body["status"] == "ok" and body["copilot_ro_grants"] == {"ok": True, "problems": []}
    assert {c["check_name"]: c["status"] for c in body["checks"]} == {"raw_vs_summary": "ok", "refresh_ran": "ok"}
    assert body["checks"][0]["details"] == {"mismatches": 0} and body["last_run"]

    asyncio.run(insert([("frozen_unchanged", "fail", {"changed_or_missing": 1}, 0)]))
    assert client.get("/health/data", headers=AUTH).json()["status"] == "failing"

    run = client.post("/insight/run", headers=AUTH)
    assert run.status_code == 200 and run.json()["errors"] == [] and run.json()["candidates"] == 0
    assert client.get("/stats", headers=AUTH).json()["scheduler"]["insight"]["runs"] == 1


def test_stale_copilot_ro_grants_are_named_at_startup_and_in_health_data(
        make_client: Callable[..., TestClient]) -> None:
    """C1: a database whose roles.sql predates F5 (no pose_frames grant, contact phone readable)."""
    import json as _json

    import psycopg

    from server.config import cfg

    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("REVOKE SELECT ON pose_frames FROM copilot_ro")
            conn.execute("GRANT SELECT (emergency_contact_phone) ON patients TO copilot_ro")
        client = make_client(TIGER_DSN=url, COPILOT_DSN=ro_dsn(url))
        body = client.get("/health/data", headers=AUTH).json()
        assert body["status"] == "failing"
        assert body["copilot_ro_grants"]["ok"] is False
        assert body["copilot_ro_grants"]["problems"] == [
            "copilot_ro is missing SELECT on pose_frames",
            "copilot_ro can read patients.emergency_contact_phone (pre-F5 grants)"]
        assert "make db-setup" in body["copilot_ro_grants"]["fix"] and "pose_frames" in body["reason"]
        client.__exit__(None, None, None)  # flush the log
        lines = [_json.loads(x) for x in (Path(cfg.LOG_DIR) / "server.log").read_text().splitlines()]
        [warning] = [x for x in lines if x["msg"] == "copilot_ro grants are stale"]
        assert warning["level"] == "WARNING" and "copilot_ro is missing SELECT on pose_frames" in warning["problems"]
