"""Apple Watch REST: GET /me/watch, POST /me/tremor, PATCH /patients/{id}/limits (therapist-page contract plus a live
push and a live-session refresh, extended per the tri-watch overrides), PATCH /patients/{id}/watch-features and
GET /patients/{id}/watch."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.state import get_state
from server.tests.conftest import AUTH, TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE
from server.tests.test_ws_safety import read_until


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


def _sql(dsn: str, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(autouse=True)
def _reset_maya(dsn: str) -> Iterator[None]:
    _sql(dsn, "UPDATE patients SET hr_limit = 130, hr_limit_set_by = NULL, tremor_monitoring = false, "
              "device_prefs = '{}' WHERE id = 'maya'")
    _sql(dsn, "DELETE FROM tremor_daily")
    yield


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _day(day: date, **overrides: Any) -> dict[str, Any]:
    body = {"day": day.isoformat(), "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0,
            "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}
    return {**body, **overrides}


# ---- GET /me/watch ---------------------------------------------------------------------------------------------

def test_get_me_watch_defaults(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/me/watch", headers=phone)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"first_name", "hr_limit", "hr_limit_set_by_name", "therapist_name", "tremor_monitoring",
                         "prefs", "plan"}
    assert body["first_name"] == "Maya" and body["hr_limit"] == 130 and body["hr_limit_set_by_name"] is None
    assert body["therapist_name"] == "Dr. Okafor" and body["tremor_monitoring"] is False
    assert body["prefs"] == {"watch_taps": "normal", "watch_arm": "right", "watch_double_tap": True,
                             "watch_captions": True}
    squat = next(p for p in body["plan"] if p["exercise_id"] == "squat")
    assert squat["name"] == "Mini squats" and squat["watch"] is False and "goal_deg" not in squat
    assert isinstance(squat["done_sets"], int) and squat["sets"] == 3 and squat["reps"] == 10
    assert client.get("/me/watch").status_code == 401
    assert client.get("/me/watch", headers=AUTH).status_code == 400
    assert client.get("/me/watch?patient_id=maya", headers=AUTH).json()["first_name"] == "Maya"


def test_get_me_watch_prefs_and_watch_plan_items(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    prefs = {"watch_taps": "strong", "watch_arm": "left", "watch_double_tap": False, "watch_captions": False}
    assert client.put("/me", headers=phone, json={"device_prefs": {**prefs, "captions": True}}).status_code == 200
    put = client.put("/prescriptions/maya", headers=AUTH, json={
        "exercise_id": "forearm_rotation", "created_by": "okafor", "sets": 3, "reps": 10,
        "targets": {"rotation_deg": 75}})
    assert put.status_code == 200
    sid = str(uuid.uuid4())
    _sql(dsn, "INSERT INTO sessions (id, patient_id, exercise_id, started_at) "
              "VALUES (%s, 'maya', 'forearm_rotation', now())", sid)
    for rep, set_no in ((1, 1), (2, 1), (3, 2)):
        _sql(dsn, "INSERT INTO reps (session_id, rep, set_no, quality) VALUES (%s, %s, %s, 1.0)", sid, rep, set_no)
    body = client.get("/me/watch", headers=phone).json()
    assert body["prefs"] == prefs
    turns = next(p for p in body["plan"] if p["exercise_id"] == "forearm_rotation")
    assert turns == {"exercise_id": "forearm_rotation", "name": "Forearm turns", "sets": 3, "reps": 10,
                     "done_sets": 2, "watch": True, "goal_deg": 75.0}


# ---- PATCH /patients/{id}/limits --------------------------------------------------------------------------------

def test_patch_limits_contract_live_refresh_and_push(client: TestClient, dsn: str) -> None:
    state = get_state()
    current = state.sessions.current
    assert current is not None and current.patient_id == "maya"  # the mock boot session
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        r = client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 120})
        assert r.status_code == 200 and r.json() == {"patient_id": "maya", "hr_limit": 120}
        event = read_until(ws, lambda m: m.get("type") == "limits_changed")
    assert event["patient_id"] == "maya" and event["hr_limit"] == 120 and event["set_by_name"] == "Dr. Okafor"
    assert current.profile.hr_limit == 120  # Guardian and Coach read this object on every hr event
    assert state.guardian._profile(current.session_id).hr_limit == 120
    assert _sql(dsn, "SELECT hr_limit, hr_limit_set_by FROM patients WHERE id = 'maya'") == [(120, "okafor")]
    [(detail,)] = _sql(dsn, "SELECT detail FROM agent_actions WHERE action = 'hr_limit_set' ORDER BY time DESC "
                            "LIMIT 1")
    assert detail == {"from": 130, "to": 120, "by": "okafor"}


def test_patch_limits_errors(client: TestClient, phone: dict[str, str]) -> None:
    ok = {"therapist_id": "okafor", "hr_limit": 120}
    assert client.patch("/patients/nobody/limits", headers=AUTH, json=ok).status_code == 404
    assert client.patch("/patients/maya/limits", headers=AUTH,
                        json={"therapist_id": "lee", "hr_limit": 120}).status_code == 403
    for bad in (39, 221):
        assert client.patch("/patients/maya/limits", headers=AUTH,
                            json={"therapist_id": "okafor", "hr_limit": bad}).status_code == 422
    assert client.patch("/patients/maya/limits", json=ok).status_code == 401
    assert client.patch("/patients/maya/limits", headers=phone, json=ok).status_code == 401  # full token only


def test_me_watch_shows_who_set_the_limit(client: TestClient, phone: dict[str, str]) -> None:
    client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 118})
    body = client.get("/me/watch", headers=phone).json()
    assert body["hr_limit"] == 118 and body["hr_limit_set_by_name"] == "Dr. Okafor"


def test_patient_socket_gets_its_own_limits_changed(client: TestClient, phone: dict[str, str]) -> None:
    token = phone["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 125})
        assert read_until(ws, lambda m: m.get("type") == "limits_changed")["hr_limit"] == 125


# ---- watch features + tremor --------------------------------------------------------------------------------------

def test_tremor_upload_needs_monitoring_on(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    today = date.today()
    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today)]})
    assert r.status_code == 403 and r.json() == {"detail": "tremor monitoring off"}

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        r = client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
        assert r.status_code == 200 and r.json() == {"patient_id": "maya", "tremor_monitoring": True}
        event = read_until(ws, lambda m: m.get("type") == "watch_features_changed")
    assert event["patient_id"] == "maya" and event["tremor_monitoring"] is True
    assert client.get("/me/watch", headers=phone).json()["tremor_monitoring"] is True

    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today), _day(today - timedelta(days=1))]})
    assert r.status_code == 200 and r.json() == {"stored": 2}
    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today, mild_min=40, dyskinesia="some")]})
    assert r.status_code == 200 and r.json() == {"stored": 1}
    assert _sql(dsn, "SELECT mild_min, dyskinesia FROM tremor_daily WHERE patient_id = 'maya' AND day = %s",
                today) == [(40, "some")]


@pytest.mark.parametrize("days", [
    [],
    [_day(date.today() - timedelta(days=i)) for i in range(15)],
    [_day(date.today(), dyskinesia="lots")],
    [_day(date.today(), source="guess")],
    [_day(date.today(), mild_min=-1)],
    [_day(date.today(), mild_min=1441)],
])
def test_tremor_upload_validation(client: TestClient, phone: dict[str, str], days: list[dict[str, Any]]) -> None:
    client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
    assert client.post("/me/tremor", headers=phone, json={"days": days}).status_code == 422


def test_tremor_upload_is_rate_limited(client: TestClient, phone: dict[str, str]) -> None:
    client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
    codes = [client.post("/me/tremor", headers=phone, json={"days": [_day(date.today())]}).status_code
             for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_watch_features_errors(client: TestClient, phone: dict[str, str]) -> None:
    assert client.patch("/patients/nobody/watch-features", headers=AUTH,
                        json={"tremor_monitoring": True}).status_code == 404
    assert client.patch("/patients/maya/watch-features", headers=AUTH, json={}).status_code == 422
    assert client.patch("/patients/maya/watch-features", headers=phone,
                        json={"tremor_monitoring": True}).status_code == 401


# ---- GET /patients/{id}/watch -----------------------------------------------------------------------------------

def test_get_patient_watch(client: TestClient, dsn: str) -> None:
    sid = str(uuid.uuid4())
    _sql(dsn, "INSERT INTO sessions (id, patient_id, exercise_id, started_at) "
              "VALUES (%s, 'maya', 'forearm_rotation', now())", sid)
    _sql(dsn, "INSERT INTO hr_recovery (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm) "
              "VALUES (%s, 'maya', 2, 126, 104, 22)", sid)
    _sql(dsn, "INSERT INTO rotation_sets (session_id, patient_id, exercise_id, set_index, angles, best_deg, avg_deg, "
              "goal_deg) VALUES (%s, 'maya', 'forearm_rotation', 1, '{62,82}', 82, 72, 80)", sid)
    _sql(dsn, "INSERT INTO tremor_daily (patient_id, day, mild_min, source) VALUES ('maya', current_date, 25, "
              "'simulated')")
    r = client.get("/patients/maya/watch", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"patient_id", "hr_recovery", "rotation_sets", "tremor"}
    rec = next(x for x in body["hr_recovery"] if x["session_id"] == sid)
    assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (2, 126, 104, 22) and rec["time"]
    rot = next(x for x in body["rotation_sets"] if x["session_id"] == sid)
    assert rot["angles"] == [62.0, 82.0] and rot["best_deg"] == 82.0 and rot["goal_deg"] == 80.0
    assert body["tremor"][0]["mild_min"] == 25 and body["tremor"][0]["source"] == "simulated"
    assert client.get("/patients/nobody/watch", headers=AUTH).status_code == 404
    assert client.get("/patients/maya/watch").status_code == 401
