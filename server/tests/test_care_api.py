"""GET/POST /me/care, /me/messages, /patients/{id}/messages, /patients/{id}/milestones, /me/test-alert."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

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
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _drain(ws, seconds: float = 1.5) -> list[dict]:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            out.append(ws.receive_json())
        except Exception:  # noqa: BLE001
            break
    return out


def test_me_care_starts_with_therapist_next_visit_and_no_notes(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/me/care", headers=phone)
    assert r.status_code == 200, r.text
    care = r.json()
    assert care["therapist"]["name"] == "Dr. Okafor" and care["therapist"]["clinic"] == "Lakeside Rehab"
    assert care["next_visit_at"]
    assert care["notes"] == [] and care["unread_messages"] == 0
    assert isinstance(care["highlights"], list)
    assert client.get("/me/care").status_code == 401
    assert client.get("/me/care?patient_id=maya", headers=AUTH).json()["therapist"]["name"] == "Dr. Okafor"
    assert client.get("/me/care", headers=AUTH).status_code == 400


def test_admin_note_and_message_show_up_and_unread_clears_on_read(client: TestClient, phone: dict[str, str]) -> None:
    r = client.post("/patients/maya/messages", json={"body": "Great squats this week", "kind": "note"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["message"]["sender"] == "therapist" and r.json()["message"]["kind"] == "note"

    r = client.get("/me/care", headers=phone)
    assert len(r.json()["notes"]) == 1 and r.json()["notes"][0]["body"] == "Great squats this week"

    r = client.post("/patients/maya/messages", json={"body": "How's the knee feeling?"}, headers=AUTH)
    assert r.status_code == 200
    mid = r.json()["message"]["id"]
    assert r.json()["message"]["kind"] == "message" and r.json()["message"]["sender"] == "therapist"

    assert client.get("/me/care", headers=phone).json()["unread_messages"] == 1
    r = client.get("/me/messages", headers=phone)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert any(m["id"] == mid and m["sender"] == "therapist" for m in msgs)
    assert client.get("/me/care", headers=phone).json()["unread_messages"] == 0

    r = client.get(f"/me/messages?since_id={mid}", headers=phone)
    assert r.json()["messages"] == []

    assert client.post("/patients/maya/messages", json={"body": "x", "kind": "bogus"},
                       headers=AUTH).status_code == 422
    assert client.post("/patients/maya/messages", json={"body": "x"}).status_code == 401


def test_patient_message_notifies_therapist_and_emits_live(client: TestClient, phone: dict[str, str]) -> None:
    token = phone["Authorization"].split(" ", 1)[1]
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        r = client.post("/me/messages", json={"body": "My knee hurts today"}, headers=phone)
        assert r.status_code == 200, r.text
        body = r.json()["message"]
        assert body["sender"] == "patient" and body["body"] == "My knee hurts today"
        msgs = _drain(ws, 2.0)
        events = [m for m in msgs if m.get("type") == "message"]
        assert any(e["message"]["id"] == body["id"] and e["patient_id"] == "maya" for e in events), msgs

    notifs = client.get("/notifications?therapist_id=okafor", headers=AUTH).json()["notifications"]
    assert any(n["kind"] == "message" and n["source_id"] == str(body["id"]) for n in notifs)
    assert client.post("/me/messages", json={"body": ""}, headers=phone).status_code == 422
    assert client.post("/me/messages", json={"body": "hi"}).status_code == 401


def test_milestones_route_is_owner_or_full_only(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/patients/maya/milestones", headers=phone)
    assert r.status_code == 200
    assert r.json() == {"patient_id": "maya", "milestones": []}
    assert client.get("/patients/bob/milestones", headers=phone).status_code == 403
    assert client.get("/patients/bob/milestones", headers=AUTH).status_code == 200
    assert client.get("/patients/maya/milestones").status_code == 401


def test_test_alert_logs_without_calling_when_calls_are_disabled(client: TestClient, phone: dict[str, str]) -> None:
    r = client.post("/me/test-alert", headers=phone)
    assert r.status_code == 200, r.text
    assert r.json() == {"logged": True, "called": False,
                        "note": "Test alert logged. Calls are disabled on this server."}
    notifs = client.get("/notifications?therapist_id=okafor", headers=AUTH).json()["notifications"]
    assert any(n["kind"] == "test" for n in notifs)
