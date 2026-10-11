"""Patient tokens on existing routes: own data OK, others 403, full-only routes 403, admin unchanged."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
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
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('bob', 'Bob Stone', 'okafor') "
                         "ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                         "VALUES ('11111111-1111-1111-1111-111111111111', 'bob', 'squat', now() - interval '1 hour', now())")
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_patient_reads_own_data_only(client: TestClient, phone: dict[str, str]) -> None:
    assert client.get("/patients/maya", headers=phone).status_code == 200
    assert client.get("/patients/maya/progress", headers=phone).status_code == 200
    assert client.get("/prescriptions/maya", headers=phone).status_code == 200
    assert client.get("/patients/bob", headers=phone).status_code == 403
    assert client.get("/patients/bob/progress", headers=phone).status_code == 403
    assert client.get("/prescriptions/bob", headers=phone).status_code == 403
    assert client.get("/sessions/11111111-1111-1111-1111-111111111111", headers=phone).status_code == 403
    assert client.get("/patients", headers=phone).status_code == 403  # caseload is the therapist's
    assert client.put("/prescriptions/maya", headers=phone,
                      json={"exercise_id": "squat", "reps": 12, "created_by": "maya"}).status_code == 403
    assert client.get("/alerts", headers=phone).status_code == 401  # full-only router, unchanged


def test_patient_starts_stops_reads_own_session(client: TestClient, phone: dict[str, str]) -> None:
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=phone)
    assert r.status_code == 200 and r.json()["patient_id"] == "maya"  # forced to the caller
    sid = r.json()["session_id"]
    assert client.post("/sessions", json={"exercise_id": "lunge"}, headers=phone).status_code == 422
    assert client.get(f"/sessions/{sid}", headers=phone).status_code == 200
    assert client.get(f"/sessions/{sid}/frames?downsample=10", headers=phone).status_code == 200
    assert client.post(f"/sessions/{sid}/share", headers=phone).json()["shared"] is True
    assert client.post(f"/sessions/{sid}/stop", headers=phone).json()["stopped"] is True
    assert client.post("/sessions/11111111-1111-1111-1111-111111111111/stop", headers=phone).status_code == 403
    assert client.post(f"/sessions/{uuid.uuid4()}/stop", headers=phone).status_code == 404


def test_patient_cannot_stop_another_patients_session(client: TestClient, phone: dict[str, str]) -> None:
    """A patient's phone POSTing /sessions while another patient's session is live must not stop it (409),
    but the patient's own running session is still replaced normally."""
    from server.state import get_state

    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["patient_id"] == "bob"
    try:
        r = client.post("/sessions", json={"exercise_id": "squat"}, headers=phone)
        assert r.status_code == 409
        assert get_state().sessions.current.patient_id == "bob"  # Bob's session is untouched
    finally:
        client.post(f"/sessions/{get_state().sessions.current.session_id}/stop", headers=AUTH)
    r = client.post("/sessions", json={"exercise_id": "squat"}, headers=phone)
    assert r.status_code == 200 and r.json()["patient_id"] == "maya"
    r2 = client.post("/sessions", json={"exercise_id": "squat"}, headers=phone)
    assert r2.status_code == 200 and r2.json()["patient_id"] == "maya"  # own session replaced normally


def test_admin_unchanged(client: TestClient) -> None:
    assert client.get("/patients", headers=AUTH).status_code == 200
    assert client.get("/patients/bob", headers=AUTH).status_code == 200
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "anything"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["patient_id"] == "bob"  # admin is not validated against the plan
    client.post(f"/sessions/{r.json()['session_id']}/stop", headers=AUTH)
