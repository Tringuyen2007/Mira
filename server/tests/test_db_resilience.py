"""Database outages: pools and the frame writer recover by themselves, forever; writes never raise; a session
started during an outage is still saved; copilot_ro failures never mark the app database unavailable.

A small TCP proxy in front of the local Docker TimescaleDB plays the outage (stop = refuse + cut connections)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from server.db import writes
from server.db.persist import SessionPersistence
from server.db.pool import Database, DBError
from server.db.writer import FrameWriter
from server.engine_bridge import LiveState
from server.state import LiveSession, default_profile
from server.tests.dbutil import fresh_database, prepare

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


class TcpProxy:
    """127.0.0.1:<port> → target. stop() refuses new connections and cuts the open ones; start() resumes on
    the same port."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, reuse_address=True)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        for w in list(self._writers):
            w.transport.abort()
        self._writers.clear()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(*self.target)
        except OSError:
            writer.transport.abort()
            return
        self._writers |= {writer, up_writer}

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                dst.transport.abort()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
        self._writers -= {writer, up_writer}


def _via(dsn: str, port: int, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(dsn)
    creds = parts.netloc.rsplit("@", 1)[0] if user is None else f"{user}:{password}"
    return urlunsplit((parts.scheme, f"{creds}@127.0.0.1:{port}", parts.path, parts.query, parts.fragment))


async def _until(cond: Any, timeout_s: float, step_s: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await cond() if asyncio.iscoroutinefunction(cond) else cond():
            return True
        await asyncio.sleep(step_s)
    return False


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
async def proxy(dsn: str) -> AsyncIterator[TcpProxy]:
    parts = urlsplit(dsn)
    p = TcpProxy(parts.hostname or "localhost", parts.port or 5432)
    await p.start()
    yield p
    if p._server is not None:
        await p.stop()


async def test_pool_recovers_after_outage_longer_than_reconnect_timeout(dsn: str, proxy: TcpProxy) -> None:
    # Short reconnect series so psycopg_pool gives up during the outage; the keeper must start new ones.
    db = Database(_via(dsn, proxy.port), reconnect_timeout_s=1.0, keeper_interval_s=0.5)
    await db.open()
    try:
        assert db.available and (await db.fetchrow("SELECT 1 AS ok"))["ok"] == 1
        await proxy.stop()
        with pytest.raises(DBError):
            await db.fetch("SELECT 1")
        assert not db.available
        await asyncio.sleep(4.0)  # several give-ups of the 1 s reconnect series
        assert not db.available
        await proxy.start()
        # Nobody calls the database: the keeper alone must bring it back.
        assert await _until(lambda: db.available, 10.0), "pool did not reconnect by itself"
        assert (await db.fetchrow("SELECT 2 AS ok"))["ok"] == 2
    finally:
        await db.close()


async def test_frame_writer_buffers_through_outage_and_catches_up(dsn: str, proxy: TcpProxy) -> None:
    writer = FrameWriter(_via(dsn, proxy.port), flush_interval_s=0.1, backoff_max_s=1.0)
    sid = str(uuid.uuid4())
    base = json.loads((SAMPLES / "engine_frame.json").read_text())
    t0 = time.time()

    def submit(i: int) -> None:
        writer.submit({**base, "t": t0 + i / 30, "session_id": sid, "patient_id": "maya"})

    writer.start()
    try:
        submit(0)
        assert await _until(lambda: writer.written_total == 1, 5.0)
        await proxy.stop()
        for i in range(1, 61):
            submit(i)
        await asyncio.sleep(3.0)  # outage longer than the backoff cap
        stats = writer.stats()
        assert stats["errors"] > 0 and writer.written_total == 1 and stats["queue_depth"] == 60
        assert writer._thread is not None and writer._thread.is_alive()
        await proxy.start()
        assert await _until(lambda: writer.written_total == 61, 10.0), writer.stats()
    finally:
        await asyncio.to_thread(writer.stop)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM pose_frames WHERE session_id = %s", (sid,)).fetchone()[0] == 61


def test_frame_writer_thread_survives_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = FrameWriter("postgresql://nobody@127.0.0.1:1/x", flush_interval_s=0.05, backoff_max_s=0.1)
    calls = {"n": 0}

    def boom() -> Any:
        calls["n"] += 1
        raise RuntimeError("unexpected")

    monkeypatch.setattr(writer, "_connection", boom)
    writer.start()
    try:
        for i in range(3):
            writer.submit({"t": time.time(), "session_id": "s", "patient_id": "p", "angles": {}, "n": i})
            time.sleep(0.3)
        assert calls["n"] >= 2 and writer._thread is not None and writer._thread.is_alive()
    finally:
        writer.stop()


async def test_session_started_during_outage_is_saved_once_the_db_returns(dsn: str, proxy: TcpProxy) -> None:
    db = Database(_via(dsn, proxy.port), reconnect_timeout_s=1.0, keeper_interval_s=0.3)
    await db.open()
    persistence = SessionPersistence(db)
    session = LiveSession(session_id=str(uuid.uuid4()), patient_id="maya", exercise_id="squat",
                          started_at=time.time(), profile=default_profile("maya"))
    try:
        await proxy.stop()
        await persistence.session_started(session)  # fails: the row is not there
        await proxy.start()
        assert await _until(lambda: db.available, 10.0)
        await persistence.on_event({"type": "rep", "session_id": session.session_id, "t": time.time(), "set": 1,
                                    "index": 1, "quality": "good", "flags": []})
        await persistence.session_stopped(session)
    finally:
        await db.close()
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT ended_at IS NOT NULL, reps_done FROM sessions WHERE id = %s",
                           (session.session_id,)).fetchone()
        reps = conn.execute("SELECT count(*) FROM reps WHERE session_id = %s", (session.session_id,)).fetchone()[0]
    assert row == (True, 1) and reps == 1


async def test_copilot_ro_failure_does_not_mark_the_app_db_unavailable(dsn: str) -> None:
    parts = urlsplit(dsn)
    bad_ro = _via(dsn, parts.port or 5432, "copilot_ro", "wrong-password")
    db = Database(dsn, bad_ro, keeper_interval_s=0.5)
    await db.open()
    try:
        with pytest.raises(DBError):
            await db.ro_fetch("SELECT 1")
        assert db.available
        assert (await db.fetchrow("SELECT 1 AS ok"))["ok"] == 1
    finally:
        await db.close()


async def test_writes_never_raise_on_non_json_payloads(dsn: str) -> None:
    db = Database(dsn)
    await db.open()
    try:
        alert_id = str(uuid.uuid4())
        when = datetime.now(UTC)
        assert await writes.insert_alert_step(db, None, alert_id, "maya", None, "fall", "detected", "ws",
                                              {"at": when, "id": uuid.UUID(alert_id)})
        row = await db.fetchrow("SELECT payload FROM alerts WHERE alert_id = %s", (alert_id,))
        assert row["payload"] == {"at": str(when), "id": alert_id}
        # Any unexpected failure inside a write is logged and returns the default.
        assert await writes.insert_agent_action(db, "x", "y", detail={"bad": object()}) is True  # str() fallback
        assert await writes.insert_ai_call(db, "x", None, "not-a-number", True, False) is False  # type: ignore[arg-type]
    finally:
        await db.close()


def test_main_angle_is_the_deeper_knee_in_flexion() -> None:
    live = LiveState()
    live.on_frame({"angles": {"knee_l": 12.0, "knee_r": 84.5}})
    assert live.main_angle == 84.5


def test_pool_reconnect_delays_stay_capped() -> None:
    """psycopg_pool doubles its delay until `reconnect_timeout`; the default (300 s) lets it reach ~2 minutes
    between attempts. Ours keeps every series short, so the database is retried at least every ~16 s."""
    from psycopg_pool.base import AttemptWithBackoff

    from server.db.pool import RECONNECT_TIMEOUT_S

    db = Database("postgresql://nobody@127.0.0.1:1/x", "postgresql://nobody@127.0.0.1:1/y")
    assert db._pool.reconnect_timeout == RECONNECT_TIMEOUT_S <= 30
    assert db._ro_pool is not None and db._ro_pool.reconnect_timeout == RECONNECT_TIMEOUT_S
    attempt, now, delays = AttemptWithBackoff(timeout=RECONNECT_TIMEOUT_S), 0.0, []
    while not attempt.time_to_give_up(now):
        attempt.update_delay(now)
        delays.append(attempt.delay)
        now += attempt.delay
        if attempt.delay == 0:
            break
    assert max(delays) <= 16.5
