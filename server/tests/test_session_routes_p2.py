"""Plan 2 Task 4: session-end REST (analysis on GET, checkin, patient session list) and the LCD allow-list."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.devices import DEVICE_EVENT_TYPES
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE


def read_until(ws: Any, pred: Callable[[dict[str, Any]], bool], limit: int = 400) -> dict[str, Any]:
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message not received")


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
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('bob', 'Bob Stone', 'okafor') "
                         "ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                         "VALUES ('22222222-2222-2222-2222-222222222222', 'bob', 'squat', "
                         "now() - interval '1 hour', now())")
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), CHECKIN_TIMEOUT_S="60")


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _finished_session(client: TestClient, phone: dict[str, str]) -> str:
    """Start a fresh mock session, wait for at least one completed rep, then stop it."""
    r = client.post("/sessions", json={"exercise_id": "squat"}, headers=phone)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    with client.websocket_connect(f"/ws/events?token={phone['Authorization'].split()[1]}") as ws:
        read_until(ws, lambda m: m.get("type") == "rep")
    assert client.post(f"/sessions/{sid}/stop", headers=phone).json()["stopped"] is True
    return sid


def _wait_for_analysis(client: TestClient, sid: str, headers: dict[str, str],
                       timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = client.get(f"/sessions/{sid}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        if body.get("analysis") is not None:
            return body
        time.sleep(0.5)
    raise AssertionError(f"analysis never arrived for {sid}: {body}")


def test_stopped_mock_session_gets_an_analysis(client: TestClient, phone: dict[str, str]) -> None:
    sid = _finished_session(client, phone)
    body = _wait_for_analysis(client, sid, phone)
    assert body["session"]["exercise_id"] == "squat"
    assert body["exercise_name"] == "Mini squats" or isinstance(body["exercise_name"], str)
    assert body["analysis"]["form_score"] is not None or body["analysis"]["reps_total"] is not None
    assert body["analysis"]["note"].startswith("AI draft")


def test_checkin_persists_pain_effort_note(client: TestClient, phone: dict[str, str]) -> None:
    sid = _finished_session(client, phone)
    r = client.post(f"/sessions/{sid}/checkin", headers=phone,
                    json={"pain_post": 3, "effort": 6, "note": "felt okay"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"session_id": sid, "pain_post": 3, "effort": 6, "note_saved": True}
    detail = client.get(f"/sessions/{sid}", headers=phone).json()
    assert detail["session"]["pain_post"] == 3 and detail["session"]["effort"] == 6
    assert detail["session"]["note"] == "felt okay"


def test_checkin_high_pain_notifies_the_therapist(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    """Post-session pain >= 7 routes to the therapist as a notification, not through the live Guardian (the
    session has already ended, so there is no live call left to escalate into). Posting the same check-in
    twice must still leave exactly one row (dedupe_key = pain_post:{sid})."""
    from psycopg.rows import dict_row

    sid = _finished_session(client, phone)
    for _ in range(2):
        r = client.post(f"/sessions/{sid}/checkin", headers=phone, json={"pain_post": 8, "note": "sharp pain"})
        assert r.status_code == 200
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE kind = 'pain' AND source_id = %s", (sid,)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["severity"] == "high" and row["therapist_id"] == "okafor"
    assert row["title"] == "Maya reported pain 8/10 after a session"
    assert row["body"] == "sharp pain"
    assert row["link"] == f"/sessions/{sid}" and row["source_table"] == "sessions"
    assert row["dedupe_key"] == f"pain_post:{sid}"


def test_checkin_rejects_someone_elses_session(client: TestClient, phone: dict[str, str]) -> None:
    r = client.post("/sessions/22222222-2222-2222-2222-222222222222/checkin", headers=phone,
                    json={"pain_post": 5})
    assert r.status_code == 403


def test_patient_sessions_list_includes_form_score(client: TestClient, phone: dict[str, str]) -> None:
    sid = _finished_session(client, phone)
    _wait_for_analysis(client, sid, phone)
    r = client.get("/patients/maya/sessions", headers=phone)
    assert r.status_code == 200
    body = r.json()
    assert body["patient_id"] == "maya"
    row = next(s for s in body["sessions"] if s["id"] == sid)
    assert "form_score" in row


def test_patient_sessions_rejects_other_patients(client: TestClient, phone: dict[str, str]) -> None:
    assert client.get("/patients/bob/sessions", headers=phone).status_code == 403


def test_admin_can_read_any_patients_sessions(client: TestClient) -> None:
    assert client.get("/patients/bob/sessions", headers=AUTH).status_code == 200


def test_session_summary_is_in_the_device_allow_list() -> None:
    assert "session_summary" in DEVICE_EVENT_TYPES
