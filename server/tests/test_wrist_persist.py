"""Wrist samples relayed by the phone land in wrist_motion (patient, rotation, quat, accel), ≤ 10 Hz per patient."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def _rows(dsn: str, sid: str) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT patient_id, rotation_deg, quat_w, accel FROM wrist_motion WHERE session_id = %s",
                            (sid,)).fetchall()


def test_wrist_burst_is_persisted_and_throttled(make_client: Callable[..., TestClient], dsn: str) -> None:
    client = make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))
    sid = get_state().sessions.current.session_id
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        for _ in range(10):  # one burst: well under 0.1 s
            ws.send_json({"type": "wrist", "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
                          "rotation_deg": 41.5})
        time.sleep(0.3)
    deadline = time.monotonic() + 5
    while not _rows(dsn, sid) and time.monotonic() < deadline:
        time.sleep(0.1)
    rows = _rows(dsn, sid)
    assert 1 <= len(rows) <= 3  # one burst, throttled to ≤ 10 Hz (≤ 3 even if the burst straddles 0.2 s)
    patient, deg, quat_w, accel = rows[0]
    assert patient == "maya" and deg == pytest.approx(41.5) and quat_w == pytest.approx(0.99)
    assert accel == pytest.approx([0.02, -9.79, 0.15])
