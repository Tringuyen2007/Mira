"""A patient's sockets only carry their own session; they may send commands, not device_health."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from server import security
from server.state import get_state
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        import psycopg

        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('bob', 'Bob Stone', 'okafor') "
                         "ON CONFLICT DO NOTHING")
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    return r.json()["token"]


def _drain(ws, seconds: float = 1.5) -> list[dict]:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            out.append(ws.receive_json())
        except Exception:  # noqa: BLE001
            break
    return out


def test_patient_receives_own_session_streams(client: TestClient, token: str) -> None:
    assert get_state().sessions.current.patient_id == "maya"
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        msg = ws.receive_json()
        assert "phase" in msg
    with client.websocket_connect(f"/ws/pose?token={token}") as ws:
        assert "joints" in ws.receive_json()


def test_patient_is_silenced_during_another_patients_session(client: TestClient, token: str) -> None:
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200
    try:
        with client.websocket_connect(f"/ws/live?token={token}") as ws:
            msgs = _drain(ws, 1.0)
            assert all(m.get("type") == "ping" for m in msgs), msgs  # nothing of Bob's leaks
            # a command from Maya's phone during Bob's session is ignored
            ws.send_json({"type": "command", "name": "pause"})
    finally:
        client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)


def test_patient_commands_and_forbidden_messages(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from server import main

    seen: list[dict] = []
    monkeypatch.setattr(main, "_on_client_event", lambda msg: seen.append(msg))
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        ws.send_json({"type": "command", "name": "pause"})
        ws.send_json({"type": "hr", "t": 1.0, "bpm": 90})
        ws.send_json({"type": "device_health", "app": "lcd"})
        ws.send_json({"type": "fall", "stage": "confirmed"})
        _drain(ws, 0.5)
    assert [m["type"] for m in seen] == ["command", "hr"]
