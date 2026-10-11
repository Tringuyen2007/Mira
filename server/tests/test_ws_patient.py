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


@pytest.fixture(autouse=True)
def _clear_pairing_attempts() -> Iterator[None]:
    from server.api import pair as pair_api

    pair_api._attempts.clear()
    yield
    pair_api._attempts.clear()


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


def test_tagged_command_cannot_steer_another_patients_session(client: TestClient, token: str,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A phone that tags a message with someone else's patient_id/session_id must not reach
    `_on_client_event` at all: the inbound gate (`_patient_owns_current`) only ever checks the *current*
    session's patient, unlike the outbound `_patient_accepts` filter (which deliberately honours those
    tags so a phone still receives its own late `session_summary`)."""
    from server import main

    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200
    bob_sid = r.json()["session_id"]
    seen: list[dict] = []
    monkeypatch.setattr(main, "_on_client_event", lambda msg: seen.append(msg))
    try:
        with client.websocket_connect(f"/ws/events?token={token}") as ws:
            ws.send_json({"type": "command", "name": "stop", "patient_id": "bob", "session_id": bob_sid})
            ws.send_json({"type": "hr", "t": 1.0, "bpm": 90, "patient_id": "bob", "session_id": bob_sid})
            _drain(ws, 0.5)
        assert seen == []
        assert get_state().sessions.current is not None
        assert get_state().sessions.current.session_id == bob_sid
    finally:
        client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)


def test_ws_token_check_db_error_closes_1013_not_1008(client: TestClient, token: str,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB hiccup while checking a patient token must look like "try again later" (1013), not a rejected
    credential (1008): the iOS LiveSocket maps 1008 to .rejected and never retries."""
    from starlette.websockets import WebSocketDisconnect

    from server.db import queries
    from server.db.pool import DBError

    async def boom(*args: object, **kwargs: object) -> None:
        raise DBError("connection lost")

    monkeypatch.setattr(queries, "patient_device", boom)
    security.invalidate_token_cache()
    # The server accepts the handshake, then closes with 1013 ("try again later"): unlike an invalid token
    # (closed before accept, so the handshake itself fails), the disconnect here surfaces on the first receive.
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1013


def test_proposal_for_another_patient_never_reaches_the_phone(client: TestClient, token: str) -> None:
    from server.state import get_state

    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        get_state().hub.emit("proposal_created", {"patient_id": "bob", "proposal": {"id": 1}})
        msgs = _drain(ws, 1.0)
        assert all(m.get("type") != "proposal_created" for m in msgs), msgs


def test_patient_receives_own_session_summary_after_the_session_ends(client: TestClient, token: str,
                                                                      dsn: str) -> None:
    """Maya's phone is still connected when her session is stopped; the analyst's `session_summary` (emitted
    after `stop()` has already cleared `sessions.current`) must still reach her socket. A mock auto-restart may
    start a fresh session for her in the meantime; that is fine, we only care about the stopped session's id."""
    import psycopg

    stopped_sid = get_state().sessions.current.session_id
    with psycopg.connect(dsn, autocommit=True) as conn:  # one rep so the analyst doesn't skip this as a false start
        conn.execute("INSERT INTO reps (session_id, rep, set_no, quality, rom, asym_avg) "
                     "VALUES (%s, 1, 1, 1.0, 80.0, 5.0)", (stopped_sid,))
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        r = client.post(f"/sessions/{stopped_sid}/stop", headers=AUTH)
        assert r.status_code == 200
        msgs = _drain(ws, 15.0)
        summaries = [m for m in msgs if m.get("type") == "session_summary"]
        assert any(m.get("session_id") == stopped_sid for m in summaries), msgs


def test_event_tagged_for_another_patient_with_no_session_is_dropped(client: TestClient, token: str) -> None:
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        get_state().hub.emit("session_summary", {"patient_id": "bob", "session_id": "not-a-real-session"})
        msgs = _drain(ws, 1.0)
        assert all(m.get("type") != "session_summary" for m in msgs), msgs


def test_event_for_another_patients_ended_session_is_dropped(client: TestClient, token: str) -> None:
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200
    bob_sid = r.json()["session_id"]
    r = client.post(f"/sessions/{bob_sid}/stop", headers=AUTH)
    assert r.status_code == 200
    try:
        with client.websocket_connect(f"/ws/events?token={token}") as ws:
            get_state().hub.emit("session_summary", {"session_id": bob_sid})
            msgs = _drain(ws, 1.0)
            assert all(m.get("type") != "session_summary" for m in msgs), msgs
    finally:
        client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)
