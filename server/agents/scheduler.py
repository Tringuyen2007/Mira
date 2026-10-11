"""Background scheduler (TRI.md §5.10, §7.4): Data Guard and Insight on timers, started in the lifespan.

Data Guard `run_all()` every DATA_GUARD_INTERVAL_S, first DATA_GUARD_DELAY_S after startup (5 min: normally just
after a 15-min refresh-policy run). Insight every INSIGHT_INTERVAL_S when INSIGHT_ENABLED, plus `run_insight_now()`
on demand. All DB work is on the async pools, so nothing here blocks the event loop. Both jobs read only through
copilot_ro; the Data Guard is skipped without COPILOT_DSN. A failing run is logged and
counted and the loop carries on; `stop()` cancels cleanly. Data Guard failures become non-urgent dashboard
notifications (kind data_guard, severity normal) through F7 when it is present.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from server.agents.data_guard import DataGuard
from server.agents.insight import notify, run_once
from server.config import cfg
from server.db.pool import Database, DBError
from server.db.queries import TZ
from server.logging_setup import get_logger

log = get_logger("scheduler")

FAILED_STATUSES = ("fail", "error")
NOTIFY_BODY_MAX = 300
# copilot_ro cannot read `therapists`; every therapist with a patient gets the notification.
THERAPISTS_SQL = "SELECT DISTINCT therapist_id FROM patients WHERE therapist_id IS NOT NULL ORDER BY 1"

Job = Callable[[], Awaitable[Any]]


@dataclass
class JobStats:
    enabled: bool = False
    runs: int = 0
    failures: int = 0
    skipped: int = 0
    running: bool = False
    last_started: float | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None
    last_result: Any = None
    next_run_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Scheduler:
    def __init__(self, db: Database, *, guard: Any | None = None, insight: Job | None = None,
                 guard_enabled: bool | None = None, guard_delay_s: float | None = None,
                 guard_interval_s: float | None = None, insight_enabled: bool | None = None,
                 insight_delay_s: float | None = None, insight_interval_s: float | None = None) -> None:
        self.db = db
        self.guard = guard if guard is not None else DataGuard(db)
        self._insight: Job = insight if insight is not None else (lambda: run_once(db))
        self.guard_enabled = cfg.DATA_GUARD_ENABLED if guard_enabled is None else guard_enabled
        self.guard_delay_s = cfg.DATA_GUARD_DELAY_S if guard_delay_s is None else guard_delay_s
        self.guard_interval_s = cfg.DATA_GUARD_INTERVAL_S if guard_interval_s is None else guard_interval_s
        self.insight_enabled = cfg.INSIGHT_ENABLED if insight_enabled is None else insight_enabled
        self.insight_delay_s = cfg.INSIGHT_DELAY_S if insight_delay_s is None else insight_delay_s
        self.insight_interval_s = cfg.INSIGHT_INTERVAL_S if insight_interval_s is None else insight_interval_s
        self.jobs = {"data_guard": JobStats(enabled=self.guard_enabled),
                     "insight": JobStats(enabled=self.insight_enabled)}
        self._insight_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    # ---- lifecycle -------------------------------------------------------------------------------------

    def start(self) -> None:
        if self.guard_enabled:
            self._tasks.append(asyncio.create_task(
                self._loop("data_guard", self.guard_delay_s, self.guard_interval_s, self.run_data_guard),
                name="scheduler-data-guard"))
        if self.insight_enabled:
            self._tasks.append(asyncio.create_task(
                self._loop("insight", self.insight_delay_s, self.insight_interval_s, self._insight_locked),
                name="scheduler-insight"))
        log.info("scheduler started", extra={"data_guard": self.guard_enabled, "insight": self.insight_enabled,
                                             "data_guard_delay_s": self.guard_delay_s,
                                             "insight_delay_s": self.insight_delay_s})

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        log.info("scheduler stopped")

    def stats(self) -> dict[str, Any]:
        return {name: job.as_dict() for name, job in self.jobs.items()}

    # ---- jobs ------------------------------------------------------------------------------------------

    async def run_insight_now(self) -> dict[str, Any] | None:
        """On-demand Insight pass (waits for a scheduled pass in progress, then runs). None if it failed."""
        if not self.insight_enabled:
            return {"skipped": "INSIGHT_ENABLED is off"}
        return await self._run("insight", self._insight_locked)

    async def _insight_locked(self) -> Any:
        async with self._insight_lock:
            return await self._insight()

    async def run_data_guard(self) -> list[dict[str, Any]] | None:
        if not self.db.has_ro:
            self.jobs["data_guard"].skipped += 1
            log.warning("data guard skipped: COPILOT_DSN is not configured (it reads only through copilot_ro)")
            return None
        if not self.db.available:
            self.jobs["data_guard"].skipped += 1
            log.info("data guard skipped: database unavailable")
            return None
        results: list[dict[str, Any]] = await self.guard.run_all()
        failed = [r for r in results if r["status"] in FAILED_STATUSES]
        if failed:
            await self._notify_guard_failures(failed)
        return results

    async def _notify_guard_failures(self, failed: list[dict[str, Any]]) -> None:
        try:
            therapists = [r["therapist_id"] for r in await self.db.ro_fetch(THERAPISTS_SQL)]
        except DBError as exc:
            log.warning("data guard failure not notified: therapists unavailable", extra={"error": str(exc)})
            return
        today = datetime.now(ZoneInfo(TZ)).date().isoformat()
        for result in failed:
            log.warning("data guard check failed", extra={"check_name": result["check_name"],
                                                         "status": result["status"]})
            body = json.dumps(result["details"], default=str)
            for therapist_id in therapists:
                await notify(kind="data_guard", severity="normal", therapist_id=therapist_id, patient_id=None,
                             title=f"Data Guard: {result['check_name']} {result['status']}",
                             body=body[:NOTIFY_BODY_MAX], link="/health/data", source_table="data_checks",
                             source_id=result["check_name"],
                             dedupe_key=f"data_guard:{result['check_name']}:{today}:{therapist_id}")

    # ---- loop machinery --------------------------------------------------------------------------------

    async def _run(self, name: str, fn: Job) -> Any:
        """One run of a job: never raises (except cancellation); outcome goes to stats and the log."""
        job = self.jobs[name]
        job.running, job.last_started = True, time.time()
        started = time.perf_counter()
        try:
            result = await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken run must never stop the schedule
            job.failures += 1
            job.last_error = f"{type(exc).__name__}: {exc}"
            log.exception(f"{name} run failed", extra={"error": job.last_error})
            return None
        else:
            job.runs += 1
            job.last_error = None
            job.last_result = _summary(result)
            return result
        finally:
            job.running = False
            job.last_duration_ms = int((time.perf_counter() - started) * 1000)

    async def _loop(self, name: str, delay_s: float, interval_s: float, fn: Job) -> None:
        loop = asyncio.get_running_loop()
        self.jobs[name].next_run_at = time.time() + delay_s
        await asyncio.sleep(delay_s)
        while True:
            started = loop.time()
            await self._run(name, fn)
            wait = max(0.0, interval_s - (loop.time() - started))
            self.jobs[name].next_run_at = time.time() + wait
            await asyncio.sleep(wait)


def _summary(result: Any) -> Any:
    """Small /stats-friendly view of a job result."""
    if isinstance(result, list):
        return {r.get("check_name"): r.get("status") for r in result if isinstance(r, dict)}
    return result
