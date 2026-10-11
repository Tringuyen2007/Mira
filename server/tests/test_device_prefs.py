"""device_prefs flows from PUT /me to /ws/live (Plan 3 §6.5): the LCD kiosk honours captions & volume."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.tests.conftest import AUTH, TOKEN
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
    from server import security

    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def read_until(ws: Any, pred: Callable[[dict[str, Any]], bool], limit: int = 400) -> dict[str, Any]:
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message not seen")


def test_put_me_updates_the_live_session_device_prefs(client: TestClient, phone: dict[str, str]) -> None:
    """Maya's mock session is already live (boot session). PUT /me while it is current updates
    bridge.live.device_prefs, so the very next /ws/live message carries the new prefs."""
    with client.websocket_connect(f"/ws/live?token={TOKEN}") as ws:
        ws.receive_json()  # the message already queued when we connected
        r = client.put("/me", headers=phone, json={"device_prefs": {"captions": False, "volume": 0.4}})
        assert r.status_code == 200
        assert r.json()["patient"]["device_prefs"] == {"captions": False, "volume": 0.4}
        msg = read_until(ws, lambda m: m.get("device_prefs") == {"captions": False, "volume": 0.4})
        assert msg["device_prefs"]["captions"] is False
        assert msg["device_prefs"]["volume"] == 0.4


def test_new_session_start_carries_the_stored_device_prefs(client: TestClient, phone: dict[str, str]) -> None:
    """A fresh session load (e.g. the phone restarting the exercise) re-reads the profile from the database,
    so device_prefs saved by an earlier PUT /me are there from the first /ws/live message of that session."""
    r = client.put("/me", headers=phone, json={"device_prefs": {"captions": False, "volume": 0.6}})
    assert r.status_code == 200
    r = client.post("/sessions", headers=phone, json={"exercise_id": "squat"})
    assert r.status_code == 200
    with client.websocket_connect(f"/ws/live?token={TOKEN}") as ws:
        msg = ws.receive_json()
        assert msg["device_prefs"] == {"captions": False, "volume": 0.6}


def test_put_me_does_not_touch_live_for_a_different_patients_session(client: TestClient,
                                                                      phone: dict[str, str]) -> None:
    """An admin editing another patient's prefs (via ?patient_id=) must not touch Maya's live session, since
    the live mock session belongs to Maya."""
    from server.state import get_state

    before = dict(get_state().bridge.live.device_prefs)
    r = client.put("/me", headers=AUTH, json={"device_prefs": {"captions": False, "volume": 0.1}},
                   params={"patient_id": "someone-else"})
    # someone-else doesn't exist in this fresh database; the write fails, but either way the live session
    # (Maya's) must be untouched.
    assert r.status_code in (404, 503, 200)
    assert get_state().bridge.live.device_prefs == before
