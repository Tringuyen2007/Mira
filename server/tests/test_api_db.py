"""REST endpoints backed by a fresh local TimescaleDB, plus the no-database behaviour (503 / still starts)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn


@pytest.fixture(scope="module")
def api_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], api_dsn: str) -> TestClient:
    return make_client(TIGER_DSN=api_dsn, COPILOT_DSN=ro_dsn(api_dsn))


def _wait(predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_without_database_reads_503_but_sessions_work(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    for path in ("/patients", "/patients/maya", "/patients/maya/progress", "/prescriptions/maya", "/alerts",
                 f"/sessions/{uuid.uuid4()}"):
        r = client.get(path, headers=AUTH)
        assert r.status_code == 503, path
        assert "database unavailable" in r.json()["detail"]
    r = client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["first_name"] == "Maya"
    assert client.post(f"/sessions/{r.json()['session_id']}/stop", headers=AUTH).json()["stopped"] is True
    assert client.get("/calibration/status", headers=AUTH).json()["calibrated"] is False
    assert client.get("/stats", headers=AUTH).json()["db"]["available"] is False


def test_routes_require_token(client: TestClient) -> None:
    for method, path in (("get", "/patients"), ("get", "/alerts"), ("post", "/sessions"),
                         ("get", "/calibration/status"), ("put", "/prescriptions/maya")):
        assert getattr(client, method)(path).status_code == 401, path


def test_live_mock_session_is_persisted_and_readable(client: TestClient, api_dsn: str) -> None:
    sid = get_state().sessions.current.session_id
    assert _wait(lambda: _count(api_dsn, "SELECT count(*) FROM pose_frames WHERE session_id = %s", sid) >= 30)
    assert _count(api_dsn, "SELECT count(*) FROM sessions WHERE id = %s AND NOT simulated", sid) == 1

    r = client.get(f"/sessions/{sid}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["patient_id"] == "maya" and body["tw_load_left_pct"] is not None
    assert body["per_set"] and body["summary"]["ai_note_label"].startswith("AI draft")

    lttb = client.get(f"/sessions/{sid}/frames?downsample=20", headers=AUTH).json()
    assert lttb["mode"] == "lttb" and 0 < len(lttb["points"]) <= 20
    full = client.get(f"/sessions/{sid}/frames?downsample=0", headers=AUTH).json()
    assert full["mode"] == "full" and full["count"] >= 30 and len(full["frames"][0]["joints"]) == 33

    assert client.post(f"/sessions/{sid}/share", headers=AUTH).json() == {"session_id": sid, "shared": True}
    assert client.get(f"/sessions/{uuid.uuid4()}", headers=AUTH).status_code == 404
    assert client.get("/sessions/not-a-uuid", headers=AUTH).status_code == 422

    stats = client.get("/stats", headers=AUTH).json()["db"]
    assert stats["available"] is True and stats["writer"]["written_total"] >= 30
    assert "total_rows" in stats and "compression" in stats


def test_full_rate_frames_are_paged_and_rendered_off_the_event_loop(
        client: TestClient, api_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from server.api import sessions as sessions_api
    from server.db import queries

    sid = get_state().sessions.current.session_id
    assert _wait(lambda: _count(api_dsn, "SELECT count(*) FROM pose_frames WHERE session_id = %s", sid) >= 30)
    t1 = _scalar(api_dsn, "SELECT max(time) FROM pose_frames WHERE session_id = %s", sid)
    total = _count(api_dsn, "SELECT count(*) FROM pose_frames WHERE session_id = %s AND time <= %s", sid, t1)
    monkeypatch.setattr(queries, "MAX_FULL_RATE_FRAMES", 7)
    render_threads: list[bool] = []
    real_render = sessions_api.render_frames

    def render(session_id: str, page: dict) -> bytes:  # type: ignore[type-arg]
        try:
            asyncio.get_running_loop()
            render_threads.append(False)  # on the event loop
        except RuntimeError:
            render_threads.append(True)
        return real_render(session_id, page)

    monkeypatch.setattr(sessions_api, "render_frames", render)
    times: list[str] = []
    params: dict[str, str] = {"downsample": "0", "t1": t1.isoformat()}
    for _ in range(total):
        r = client.get(f"/sessions/{sid}/frames", params=params, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "application/json"
        page = r.json()
        assert page["mode"] == "full" and page["count"] == len(page["frames"]) <= 7
        assert all(len(f["joints"]) == 33 for f in page["frames"] if f["joints"] is not None)
        times += [f["time"] for f in page["frames"]]
        if page["next_t0"] is None:
            break
        params["t0"] = page["next_t0"]
    assert len(times) == total and len(set(times)) == total  # every frame once, no gap, no duplicate
    assert len(render_threads) >= 2 and all(render_threads)


def _scalar(dsn: str, sql: str, *params: object) -> object:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def test_new_session_replaces_current_and_stop_ends_it(client: TestClient, api_dsn: str) -> None:
    r = client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)
    sid = r.json()["session_id"]
    assert client.post(f"/sessions/{sid}/stop", headers=AUTH).status_code == 200
    assert _count(api_dsn, "SELECT count(*) FROM sessions WHERE id = %s AND ended_at IS NOT NULL", sid) == 1
    assert client.post(f"/sessions/{uuid.uuid4()}/stop", headers=AUTH).status_code == 404


def test_patients_progress_and_prescriptions(client: TestClient) -> None:
    patients = client.get("/patients", headers=AUTH).json()["patients"]
    assert any(p["id"] == "maya" for p in patients)
    detail = client.get("/patients/maya", headers=AUTH).json()
    assert detail["patient"]["therapist_name"] == "Dr. Okafor"
    assert {p["exercise_id"] for p in detail["prescriptions"]} == {"squat", "glute_bridge", "single_leg_stance"}
    assert client.get("/patients/nobody", headers=AUTH).status_code == 404
    progress = client.get("/patients/maya/progress?exercise_id=squat", headers=AUTH).json()
    assert progress["source"] == "progress_v"

    before = client.get("/prescriptions/maya", headers=AUTH).json()["prescriptions"]
    squat = next(p for p in before if p["exercise_id"] == "squat")
    r = client.put("/prescriptions/maya", headers=AUTH,
                   json={"exercise_id": "squat", "reps": 12, "created_by": "okafor", "limits": {"max_reps": 14}})
    assert r.status_code == 200
    new = r.json()["prescription"]
    assert new["version"] == squat["version"] + 1 and new["reps"] == 12 and new["sets"] == squat["sets"]
    assert new["created_by"] == "okafor" and new["limits"] == {"max_reps": 14}
    history = client.get("/prescriptions/maya?history=true", headers=AUTH).json()["prescriptions"]
    versions = [(p["version"], p["active"]) for p in history if p["exercise_id"] == "squat"]
    assert versions[0] == (new["version"], True) and all(not active for _, active in versions[1:])
    bad = client.put("/prescriptions/maya", headers=AUTH, json={"exercise_id": "lunge", "created_by": "okafor"})
    assert bad.status_code == 400
    assert client.put("/prescriptions/nobody", headers=AUTH,
                      json={"exercise_id": "squat", "created_by": "okafor"}).status_code == 404


def test_alerts_grouped_and_resolved(client: TestClient, api_dsn: str) -> None:
    alert_id = str(uuid.uuid4())
    with psycopg.connect(api_dsn, autocommit=True) as conn:
        for i, step in enumerate(("detected", "checked_in", "escalated")):
            conn.execute("INSERT INTO alerts (time, alert_id, patient_id, kind, step, channel, payload) "
                         "VALUES (now() - make_interval(secs => %s), %s, 'maya', 'fall', %s, 'device', '{}')",
                         (30 - i, alert_id, step))
    alerts = client.get("/alerts?open=true", headers=AUTH).json()["alerts"]
    mine = next(a for a in alerts if a["alert_id"] == alert_id)
    assert [s["step"] for s in mine["steps"]] == ["detected", "checked_in", "escalated"] and mine["open"]

    r = client.post(f"/alerts/{alert_id}/resolve", headers=AUTH, json={"resolved_by": "okafor"})
    assert r.status_code == 200 and r.json()["step"] == "resolved_by_therapist"
    assert all(a["alert_id"] != alert_id for a in client.get("/alerts?open=true", headers=AUTH).json()["alerts"])
    closed = client.get("/alerts?open=false", headers=AUTH).json()["alerts"]
    assert next(a for a in closed if a["alert_id"] == alert_id)["open"] is False
    assert client.post(f"/alerts/{uuid.uuid4()}/resolve", headers=AUTH).status_code == 404


def _count(dsn: str, query: str, *params: object) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(query, params).fetchone()[0]
