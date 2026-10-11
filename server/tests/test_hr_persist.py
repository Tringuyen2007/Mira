"""The mock engine's `hr` events (~1/s) are persisted into `heart_rate`, throttled to at most one row per
second per session."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn


@pytest.fixture(scope="module")
def hr_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], hr_dsn: str) -> TestClient:
    return make_client(TIGER_DSN=hr_dsn, COPILOT_DSN=ro_dsn(hr_dsn))


def _count(dsn: str, sql: str, *params: object) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchone()[0]


def _wait(predicate: Callable[[], bool], timeout_s: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_mock_session_persists_hr_at_most_once_per_second(client: TestClient, hr_dsn: str) -> None:
    sid = get_state().sessions.current.session_id
    assert _wait(lambda: _count(hr_dsn, "SELECT count(*) FROM heart_rate WHERE session_id = %s", sid) >= 2)
    time.sleep(1.0)
    rows = _count(hr_dsn, "SELECT count(*) FROM heart_rate WHERE session_id = %s", sid)
    elapsed = _count(hr_dsn, "SELECT ceil(extract(epoch from (max(time) - min(time))) + 1)::int "
                            "FROM heart_rate WHERE session_id = %s", sid)
    assert rows >= 2
    assert rows <= elapsed + 1, "at most ~1 heart_rate row per second per session"

    r = client.get(f"/sessions/{sid}", headers=AUTH)
    assert r.status_code == 200


async def test_session_stopped_forgets_the_hr_throttle() -> None:
    from server.db.persist import SessionPersistence
    from server.state import LiveSession, default_profile

    persistence = SessionPersistence(None)  # type: ignore[arg-type]  # writes are no-ops without a database
    session = LiveSession(session_id="hr-throttle", patient_id="maya", exercise_id="squat",
                          started_at=time.time(), profile=default_profile("maya"))
    await persistence.session_started(session)
    await persistence.on_event({"type": "hr", "session_id": session.session_id, "t": time.time(), "bpm": 90})
    assert session.session_id in persistence._last_hr_at
    await persistence.session_stopped(session)
    assert session.session_id not in persistence._last_hr_at
