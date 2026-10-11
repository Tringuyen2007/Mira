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


@pytest.mark.parametrize("prefs", [{"a": {"nested": 1}}, {"a": [1, 2]}, {"a": "x" * 65}, {"a": None}])
def test_put_me_rejects_non_scalar_or_long_device_prefs(client: TestClient, phone: dict[str, str],
                                                        prefs: dict[str, object]) -> None:
    assert client.put("/me", headers=phone, json={"device_prefs": prefs}).status_code == 422


def test_put_me_accepts_scalar_device_prefs(client: TestClient, phone: dict[str, str]) -> None:
    prefs = {"captions": True, "volume": 0.5, "size": 2, "voice": "x" * 64}
    r = client.put("/me", headers=phone, json={"device_prefs": prefs})
    assert r.status_code == 200 and r.json()["patient"]["device_prefs"] == prefs


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


def _locked_client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), PAIRING_REUSABLE_UPDATES_PROFILE="0")


def test_demo_phone_is_locked_when_reusable_updates_are_off(make_client: Callable[..., TestClient], dsn: str) -> None:
    """With PAIRING_REUSABLE_UPDATES_PROFILE=0 a demo-code phone can't wipe Maya or rewrite who Retell dials."""
    client = _locked_client(make_client, dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE patients SET emergency_contact_name = 'Eric', emergency_contact_phone = '+15550100111' "
                     "WHERE id = 'maya'")
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    phone = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.delete("/me", headers=phone).status_code == 403
    r = client.put("/me", headers=phone, json={"emergency_contact": {"name": "Mallory", "phone": "+15550199999"},
                                               "profile": {"name": "Not Maya"}, "share_by_default": True})
    assert r.status_code == 200
    me = r.json()["patient"]
    assert me["emergency_contact"]["name"] == "Eric" and me["name"] != "Not Maya"
    assert me["share_by_default"] is True  # other fields still apply
    # an admin-minted (single-use) phone is never locked
    code = client.post("/patients/maya/pairing-code", headers=AUTH).json()["code"]
    real = client.post("/pair", json={"code": code, "profile": PROFILE, "emergency_contact": CONTACT}).json()
    r = client.put("/me", headers={"Authorization": f"Bearer {real['token']}"},
                   json={"emergency_contact": {"name": "Ana", "phone": "+15550100999"}})
    assert r.json()["patient"]["emergency_contact"]["name"] == "Ana"


def test_demo_phone_test_alert_never_calls_when_locked(make_client: Callable[..., TestClient], dsn: str) -> None:
    security.invalidate_token_cache()
    client = make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), PAIRING_REUSABLE_UPDATES_PROFILE="0",
                         CALLS_DISABLED="0", ALLOW_TEST_CALLS="1")
    from server.state import get_state

    calls: list[str] = []

    async def fake_call(phone: str) -> None:
        calls.append(phone)

    get_state().notifier.test_call = fake_call
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE patients SET emergency_contact_phone = '+15550100111' WHERE id = 'maya'")
    token = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT}).json()
    r = client.post("/me/test-alert", headers={"Authorization": f"Bearer {token['token']}"})
    assert r.status_code == 200 and r.json()["called"] is False and r.json()["logged"] is True
    assert calls == []
    # the admin acting for Maya still places the test call
    assert client.post("/me/test-alert?patient_id=maya", headers=AUTH).json()["called"] is True
    assert calls == ["+15550100111"]


def test_demo_phone_unlocked_by_default(client: TestClient, dsn: str) -> None:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    phone = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.put("/me", headers=phone, json={"emergency_contact": {"name": "Zed", "phone": "+15550100888"}})
    assert r.json()["patient"]["emergency_contact"]["name"] == "Zed"
    assert client.delete("/me", headers=phone).status_code == 200
