"""GET /caseload and GET /patients/{id}/memory (dashboard spec 2026-10-11, Task 8)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_dashboard_routes_need_the_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    for path in ("/caseload", "/patients/maya/memory"):
        assert client.get(path).status_code == 401


def test_dashboard_routes_503_without_db(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert client.get("/caseload", headers=AUTH).status_code == 503
    assert client.get("/patients/maya/memory", headers=AUTH).status_code == 503


def test_caseload_sample_shape() -> None:
    body = json.loads((SAMPLES / "rest_get_caseload.json").read_text())
    assert set(body) == {"therapist_id", "generated_at", "today", "counts", "patients"}
    assert set(body["counts"]) == {"all", "needs_you", "watch", "on_track"}
    p = body["patients"][0]
    for key in ("id", "name", "bucket", "severity", "reason", "evidence", "week", "last_session_at", "live_now"):
        assert key in p
    assert [x["severity"] for x in body["patients"]] == sorted((x["severity"] for x in body["patients"]),
                                                                reverse=True)


def test_patient_memory_sample_shape() -> None:
    body = json.loads((SAMPLES / "rest_get_patient_memory.json").read_text())
    assert set(body) == {"patient_id", "days", "events"}
    e = body["events"][0]
    for key in ("id", "time", "kind", "message", "evidence", "acknowledged"):
        assert key in e


# ---- DB-backed -------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dash_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        _seed_extra(url)
        yield url


def _seed_extra(dsn: str) -> None:
    """Beyond `seed.seed` (just Okafor + Maya): a second okafor patient with an open fall alert (George) and a
    patient of a different therapist (Priya, to prove the therapist filter)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('george', 'George Alvarez', 71, 'left', 'left hip replacement', 'okafor', true)")
        conn.execute("INSERT INTO therapists (id, name) VALUES ('nguyen', 'Dr. Nguyen')")
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('priya', 'Priya Shah', 54, 'right', 'right ACL reconstruction', 'nguyen', true)")
        conn.execute("INSERT INTO alerts (time, alert_id, patient_id, kind, step, channel, payload) "
                     "VALUES (now() - interval '2 minutes', %s, 'george', 'fall', 'detected', 'device', '{}')",
                     (str(uuid.uuid4()),))
        conn.execute("INSERT INTO memory_events (patient_id, kind, message, evidence) "
                     "VALUES ('maya', 'compensation', 'Bending deeper, but taking weight off her right leg', "
                     "%s)", (json.dumps({"load_from": 47.0, "load_to": 36.0, "side": "right"}),))
        conn.execute("INSERT INTO memory_events (patient_id, kind, message, evidence, time) "
                     "VALUES ('maya', 'depth_gain', 'Reached the 110 knee bend goal', %s, "
                     "now() - interval '40 days')", (json.dumps({"gain_deg": 6}),))


@pytest.fixture
def dash_client(make_client: Callable[..., TestClient], dash_dsn: str) -> TestClient:
    return make_client(TIGER_DSN=dash_dsn, COPILOT_DSN=ro_dsn(dash_dsn))


def test_caseload_filters_by_therapist_and_sorts_by_severity(dash_client: TestClient, dash_dsn: str) -> None:
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    ids = {p["id"] for p in body["patients"]}
    assert ids == {"maya", "george"}
    assert "priya" not in ids
    for p in body["patients"]:
        assert "bucket" in p and p["bucket"] in ("needs_you", "watch", "on_track")
    assert [p["severity"] for p in body["patients"]] == sorted((p["severity"] for p in body["patients"]),
                                                                reverse=True)
    george = next(p for p in body["patients"] if p["id"] == "george")
    assert george is body["patients"][0]
    assert george["bucket"] == "needs_you"


def test_caseload_requires_patient_id_pattern(dash_client: TestClient) -> None:
    assert dash_client.get("/caseload?therapist_id=" + "x" * 65, headers=AUTH).status_code == 422
    assert dash_client.get("/caseload?therapist_id=bad id!", headers=AUTH).status_code == 422


def test_patient_memory_newest_first_and_days_bounds(dash_client: TestClient) -> None:
    body = dash_client.get("/patients/maya/memory?days=21", headers=AUTH).json()
    assert body["patient_id"] == "maya" and body["days"] == 21
    times = [e["time"] for e in body["events"]]
    assert times == sorted(times, reverse=True)
    assert any(e["kind"] == "compensation" for e in body["events"])
    assert not any(e["kind"] == "depth_gain" for e in body["events"])  # 40 days ago, outside the 21-day window

    assert dash_client.get("/patients/maya/memory?days=0", headers=AUTH).status_code == 422
    assert dash_client.get("/patients/maya/memory?days=91", headers=AUTH).status_code == 422
    assert dash_client.get("/patients/nobody/memory", headers=AUTH).status_code == 404
