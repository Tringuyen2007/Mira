"""GET/PUT/DELETE /me and /me/export with a patient token; a full token may act as the patient via ?patient_id."""

from __future__ import annotations

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
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_get_me(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/me", headers=phone)
    assert r.status_code == 200
    me = r.json()
    assert me["patient"]["id"] == "maya" and me["therapist"]["name"] == "Dr. Okafor"
    assert me["patient"]["next_visit_at"]
    assert client.get("/me").status_code == 401
    # The admin token can read a patient's view by id (dashboard "see what the patient sees").
    assert client.get("/me?patient_id=maya", headers=AUTH).json()["patient"]["id"] == "maya"
    assert client.get("/me", headers=AUTH).status_code == 400  # admin must say which patient


def test_put_me_partial_update(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    r = client.put("/me", headers=phone, json={"emergency_contact": {"name": "Ana", "phone": "+15550100999",
                                                                     "relationship": "daughter"},
                                               "share_by_default": True,
                                               "device_prefs": {"captions": False, "volume": 0.4}})
    assert r.status_code == 200
    me = r.json()
    assert me["patient"]["emergency_contact"]["name"] == "Ana" and me["patient"]["share_by_default"] is True
    assert me["patient"]["device_prefs"] == {"captions": False, "volume": 0.4}
    assert me["patient"]["height_cm"] == 163  # untouched
    r = client.put("/me", headers=phone, json={"profile": {"mass_kg": 68}})
    assert r.status_code == 200 and r.json()["patient"]["mass_kg"] == 68
    assert client.put("/me", headers=phone, json={"device_prefs": {str(i): i for i in range(30)}}).status_code == 422


def test_export_and_delete(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    from server.state import get_state

    sid = get_state().sessions.current.session_id  # the live mock session is Maya's and not simulated
    export = client.get("/me/export", headers=phone)
    assert export.status_code == 200
    doc = export.json()
    assert doc["patient"]["id"] == "maya" and any(s["id"] == sid for s in doc["sessions"])
    assert "pose_frames_count" in doc and "reps" in doc and "alerts" in doc

    with psycopg.connect(dsn) as conn:
        simulated_before = conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND simulated").fetchone()[0]
        conn.execute("INSERT INTO messages (patient_id, sender, body) VALUES ('maya', 'patient', 'hi there')")
    r = client.delete("/me", headers=phone)
    assert r.status_code == 200 and r.json()["deleted"] is True
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND NOT simulated").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM messages WHERE patient_id='maya'").fetchone()[0] == 0
        # labeled demo history is not the patient's own data and survives
        assert conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND simulated").fetchone()[0] == simulated_before
        row = conn.execute("SELECT emergency_contact_name, height_cm FROM patients WHERE id='maya'").fetchone()
        assert row == (None, None)
        assert conn.execute("SELECT count(*) FROM patient_devices WHERE patient_id='maya' AND revoked_at IS NULL").fetchone()[0] == 0
    # the token is gone
    security.invalidate_token_cache()
    assert client.get("/me", headers=phone).status_code == 401
    # the live session was Maya's: it has been stopped
    assert get_state().sessions.current is None or get_state().sessions.current.session_id != sid
