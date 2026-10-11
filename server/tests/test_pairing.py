"""POST /pair: therapist code → patient token; demo code reusable; errors; admin code minting and revocation."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

PROFILE = {"name": "Maya Ellis", "height_cm": 163, "mass_kg": 70, "affected_side": "right",
           "condition": "Knee replacement"}
CONTACT = {"name": "Eric", "phone": "+15550100111", "relationship": "son"}


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


def _pair(client: TestClient, code: str = "K7P4M2", **extra: object) -> object:
    body = {"code": code, "device_name": "iPhone", "profile": PROFILE, "emergency_contact": CONTACT,
            "alert_therapist": True, **extra}
    return client.post("/pair", json=body)


def test_pair_with_demo_code_returns_token_and_me(client: TestClient, dsn: str) -> None:
    r = _pair(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patient_id"] == "maya" and body["token"].startswith("pt_")
    me = body["me"]
    assert me["patient"]["first_name"] == "Maya" and me["patient"]["emergency_contact"]["name"] == "Eric"
    assert me["patient"]["emergency_contact"]["relationship"] == "son"
    assert me["therapist"]["name"] == "Dr. Okafor" and me["therapist"]["clinic"] == "Lakeside Rehab"
    assert {p["exercise_id"] for p in me["prescriptions"]} == {"squat", "glute_bridge", "single_leg_stance"}
    assert next(p for p in me["prescriptions"] if p["exercise_id"] == "squat")["exercise_name"] == "Mini squats"
    assert "online" in me["lcd"] and me["calibration"]["calibrated"] is False
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT patient_id, device_name FROM patient_devices WHERE token_hash = %s",
                           (security.token_hash(body["token"]),)).fetchone()
        assert row == ("maya", "iPhone")
        assert conn.execute("SELECT count(*) FROM patient_devices WHERE patient_id = 'maya'").fetchone()[0] >= 1
        # the demo code stays usable
        assert conn.execute("SELECT used_at FROM pairing_codes WHERE code = 'K7P4M2'").fetchone()[0] is None
    # the token works on a patient route (Task 5 adds /me; here: the token resolves)
    import asyncio

    assert asyncio.run(security.resolve_token(body["token"])) == security.Principal("patient", "maya")


def test_pair_is_case_insensitive_and_second_phone_gets_its_own_token(client: TestClient) -> None:
    a = _pair(client, code="k7p4m2").json()["token"]
    b = _pair(client).json()["token"]
    assert a != b


def test_bad_codes(client: TestClient, dsn: str) -> None:
    assert _pair(client, code="ZZZZZZ").status_code == 404
    assert _pair(client, code="ABC").status_code == 422
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO pairing_codes (code, patient_id, therapist_id, expires_at) "
                     "VALUES ('EXPIRD', 'maya', 'okafor', now() - interval '1 minute') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO pairing_codes (code, patient_id, therapist_id, used_at) "
                     "VALUES ('USEDUP', 'maya', 'okafor', now()) ON CONFLICT DO NOTHING")
    assert _pair(client, code="EXPIRD").status_code == 410
    assert _pair(client, code="USEDUP").status_code == 410


def test_pair_needs_no_token_but_is_rate_limited(client: TestClient) -> None:
    from server.api import pair as pair_api

    pair_api._attempts.clear()
    for _ in range(pair_api.MAX_ATTEMPTS_PER_MIN):
        assert _pair(client, code="ZZZZZZ").status_code == 404
    assert _pair(client, code="ZZZZZZ").status_code == 429
    pair_api._attempts.clear()


def test_admin_mints_single_use_code_and_revokes_devices(client: TestClient, dsn: str) -> None:
    r = client.post("/patients/maya/pairing-code", headers=AUTH)
    assert r.status_code == 200
    code = r.json()["code"]
    assert len(code) == 6 and r.json()["expires_at"]
    assert client.post("/patients/nobody/pairing-code", headers=AUTH).status_code == 404
    assert client.post("/patients/maya/pairing-code").status_code == 401
    first = _pair(client, code=code)
    assert first.status_code == 200
    assert _pair(client, code=code).status_code == 410  # single use
    token = first.json()["token"]
    r = client.delete("/patients/maya/devices", headers=AUTH)
    assert r.status_code == 200 and r.json()["revoked"] >= 1
    import asyncio

    assert asyncio.run(security.resolve_token(token)) is None


def test_pair_without_database_is_503(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert _pair(client).status_code == 503
