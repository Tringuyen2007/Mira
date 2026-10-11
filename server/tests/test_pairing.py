"""POST /pair: therapist code → patient token; demo code reusable; errors; admin code minting and revocation."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.db import writes
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

PROFILE = {"name": "Maya Ellis", "height_cm": 163, "mass_kg": 70, "affected_side": "right",
           "condition": "Knee replacement"}
CONTACT = {"name": "Eric", "phone": "+15550100111", "relationship": "son"}


@pytest.fixture(autouse=True)
def _clear_pairing_attempts() -> Iterator[None]:
    """The per-IP limiter is a module-global dict: without this, attempts recorded by one test (successes now
    count too, item 4a) would bleed into the next and spuriously trip its rate limit."""
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


def test_rate_limit_counts_successful_pairs_too(client: TestClient) -> None:
    """A reusable demo code must not let one IP pair unlimited phones: successes count against the limit too."""
    from server.api import pair as pair_api

    for _ in range(pair_api.MAX_ATTEMPTS_PER_MIN):
        assert _pair(client).status_code == 200
    assert _pair(client).status_code == 429


def test_reusable_code_profile_update_can_be_disabled(make_client: Callable[..., TestClient], dsn: str) -> None:
    """With PAIRING_REUSABLE_UPDATES_PROFILE=0, a reusable code still pairs the phone but no longer rewrites
    Maya's profile/emergency contact for every stranger who pairs with the public demo code."""
    security.invalidate_token_cache()
    client = make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), PAIRING_REUSABLE_UPDATES_PROFILE="0")
    other_profile = {"name": "Someone Else", "height_cm": 180, "mass_kg": 90, "affected_side": "left",
                     "condition": "Hip pain"}
    other_contact = {"name": "Nobody", "phone": "+15550199999", "relationship": "friend"}
    r = client.post("/pair", json={"code": "K7P4M2", "device_name": "iPhone", "profile": other_profile,
                                   "emergency_contact": other_contact, "alert_therapist": True})
    assert r.status_code == 200 and r.json()["patient_id"] == "maya"
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT name, emergency_contact_name FROM patients WHERE id = 'maya'").fetchone()
        assert row[0] != "Someone Else"
        assert row[1] != "Nobody"


def test_excess_devices_are_capped_at_ten(client: TestClient, dsn: str) -> None:
    from server.api import pair as pair_api

    tokens = []
    for _ in range(pair_api.MAX_ACTIVE_DEVICES_PER_PATIENT):
        pair_api._attempts.clear()  # this test pairs more than MAX_ATTEMPTS_PER_MIN times from one IP
        tokens.append(_pair(client).json()["token"])
    pair_api._attempts.clear()
    eleventh = _pair(client).json()["token"]
    with psycopg.connect(dsn) as conn:
        active = conn.execute(
            "SELECT count(*) FROM patient_devices WHERE patient_id = 'maya' AND revoked_at IS NULL").fetchone()[0]
        assert active == pair_api.MAX_ACTIVE_DEVICES_PER_PATIENT
        oldest_revoked = conn.execute("SELECT revoked_at IS NOT NULL FROM patient_devices WHERE token_hash = %s",
                                      (security.token_hash(tokens[0]),)).fetchone()[0]
        assert oldest_revoked is True
    import asyncio

    assert asyncio.run(security.resolve_token(eleventh)) is not None


def test_claim_pairing_code_is_atomic_against_concurrent_claims(dsn: str) -> None:
    """Two concurrent /pair calls for the same single-use code must not both succeed (TOCTOU race)."""
    import asyncio

    from server.db.pool import Database

    async def run() -> tuple[dict | None, dict | None]:
        db = Database(dsn, ro_dsn(dsn))
        await db.open()
        try:
            await writes.upsert_pairing_code(db, "RACE01", "maya", "okafor", reusable=False)
            return await asyncio.gather(writes.claim_pairing_code(db, "RACE01"),
                                        writes.claim_pairing_code(db, "RACE01"))
        finally:
            await db.close()

    first, second = asyncio.run(run())
    assert (first is None) != (second is None)  # exactly one succeeds


def test_demo_phone_principal_is_marked_demo(client: TestClient) -> None:
    """A phone paired with a reusable code is a demo phone; one paired with an admin-minted code is not."""
    import asyncio

    demo = asyncio.run(security.resolve_token(_pair(client).json()["token"]))
    assert demo is not None and demo.demo is True
    code = client.post("/patients/maya/pairing-code", headers=AUTH).json()["code"]
    real = asyncio.run(security.resolve_token(_pair(client, code=code).json()["token"]))
    assert real is not None and real.demo is False


def test_reusable_pairs_never_evict_a_single_use_device(client: TestClient, dsn: str) -> None:
    """The demo code pairing 12 phones must evict only older demo phones, never the admin-minted device."""
    from server.api import pair as pair_api

    code = client.post("/patients/maya/pairing-code", headers=AUTH).json()["code"]
    real = _pair(client, code=code).json()["token"]
    for _ in range(12):
        pair_api._attempts.clear()
        assert _pair(client).status_code == 200
    import asyncio

    security.invalidate_token_cache()
    assert asyncio.run(security.resolve_token(real)) is not None
    with psycopg.connect(dsn) as conn:
        demo_active = conn.execute("SELECT count(*) FROM patient_devices WHERE patient_id = 'maya' "
                                   "AND revoked_at IS NULL AND via_reusable").fetchone()[0]
    assert demo_active == pair_api.MAX_ACTIVE_DEVICES_PER_PATIENT
