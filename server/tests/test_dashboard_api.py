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
NOTE_SESSION_ID = "11111111-1111-1111-1111-111111111111"
OTHER_NOTE_SESSION_ID = "22222222-2222-2222-2222-222222222222"


def test_dashboard_routes_need_the_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    for path in ("/caseload", "/patients/maya/memory", "/patients/maya/session-history"):
        assert client.get(path).status_code == 401


def test_dashboard_routes_503_without_db(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert client.get("/caseload", headers=AUTH).status_code == 503
    assert client.get("/patients/maya/memory", headers=AUTH).status_code == 503
    assert client.get("/patients/maya/session-history", headers=AUTH).status_code == 503


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
    """Beyond `seed.seed` (just Okafor + Maya): a second okafor patient with an open fall alert (George), a
    patient of a different therapist (Priya, to prove the therapist filter), plus one patient per I1-I10
    regression case below."""
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

        # I2: a resolved alert must not count as open (and old alerts must not show up at all).
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('resolved_pat', 'Rosa Diaz', 60, 'right', 'right knee', 'okafor', true)")
        resolved_alert = str(uuid.uuid4())
        conn.execute("INSERT INTO alerts (time, alert_id, patient_id, kind, step, channel, payload) "
                     "VALUES (now() - interval '2 hours', %s, 'resolved_pat', 'fall', 'detected', 'device', '{}')",
                     (resolved_alert,))
        conn.execute("INSERT INTO alerts (time, alert_id, patient_id, kind, step, channel, payload) "
                     "VALUES (now() - interval '1 hour', %s, 'resolved_pat', 'fall', 'resolved_by_therapist', "
                     "'device', '{}')", (resolved_alert,))

        # I10: acknowledged memory must be ignored.
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('acked_pat', 'Ana Cruz', 50, 'left', 'left knee', 'okafor', true)")
        conn.execute("INSERT INTO memory_events (patient_id, kind, message, evidence, acknowledged) "
                     "VALUES ('acked_pat', 'compensation', 'ai text', %s, true)",
                     (json.dumps({"load_from": 50.0, "load_to": 40.0, "side": "left"}),))

        # I10: flagged reps in the last session -> watch, with a deterministic top flag.
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('flags_pat', 'Fay Lopez', 45, 'right', 'right knee', 'okafor', true)")
        flags_session = str(uuid.uuid4())
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (%s, 'flags_pat', 'squat', now() - interval '1 day', now() - interval '1 day' "
                     "+ interval '10 minutes')", (flags_session,))
        for rep, flags in ((1, ["knee_cave_r"]), (2, ["knee_cave_r"]), (3, ["knee_cave_r"]), (4, None)):
            conn.execute("INSERT INTO reps (session_id, rep, flags) VALUES (%s, %s, %s)",
                         (flags_session, rep, flags))

        # I10: pain_post >= 7 in the last finished session -> needs_you.
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('pain_pat', 'Pia Nunez', 55, 'right', 'right knee', 'okafor', true)")
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at, pain_post) "
                     "VALUES (gen_random_uuid(), 'pain_pat', 'squat', now() - interval '2 days', "
                     "now() - interval '2 days' + interval '10 minutes', 3)")
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at, pain_post) "
                     "VALUES (gen_random_uuid(), 'pain_pat', 'squat', now() - interval '1 day', "
                     "now() - interval '1 day' + interval '10 minutes', 8)")

        # I3: no finished session yet, prescribed today at >= 5/week -> missed must be 0, not 7.
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('new_pat', 'Nia Park', 40, 'left', 'left knee', 'okafor', true)")
        conn.execute("INSERT INTO prescriptions (patient_id, exercise_id, version, sets, reps, targets, "
                     "created_by) VALUES ('new_pat', 'squat', 1, 3, 10, %s, 'okafor')",
                     (json.dumps({"sessions_per_week": 5}),))

        # I3: last finished session 3 days ago, prescribed at >= 5/week -> missed == 3.
        conn.execute("INSERT INTO patients (id, name, age, affected_side, condition, therapist_id, simulated) "
                     "VALUES ('missed3_pat', 'Mira Osei', 42, 'left', 'left knee', 'okafor', true)")
        conn.execute("INSERT INTO prescriptions (patient_id, exercise_id, version, sets, reps, targets, "
                     "created_by) VALUES ('missed3_pat', 'squat', 1, 3, 10, %s, 'okafor')",
                     (json.dumps({"sessions_per_week": 5}),))
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (gen_random_uuid(), 'missed3_pat', 'squat', now() - interval '3 days', "
                     "now() - interval '3 days' + interval '10 minutes')")
        # an unfinished session today must not reset the missed count.
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (gen_random_uuid(), 'missed3_pat', 'squat', now(), NULL)")

        # Task 11: a finished session with an AI draft note, for the progress-note approval tests.
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at, ai_note) "
                     "VALUES (%s, 'maya', 'squat', now() - interval '1 day', "
                     "now() - interval '1 day' + interval '10 minutes', 'AI draft note for Maya')",
                     (NOTE_SESSION_ID,))
        # a session belonging to priya (nguyen's patient), to prove the therapist-ownership check.
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (%s, 'priya', 'squat', now() - interval '1 day', "
                     "now() - interval '1 day' + interval '10 minutes')", (OTHER_NOTE_SESSION_ID,))



@pytest.fixture
def dash_client(make_client: Callable[..., TestClient], dash_dsn: str) -> TestClient:
    return make_client(TIGER_DSN=dash_dsn, COPILOT_DSN=ro_dsn(dash_dsn))


def test_caseload_filters_by_therapist_and_sorts_by_severity(dash_client: TestClient, dash_dsn: str) -> None:
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    ids = {p["id"] for p in body["patients"]}
    assert {"maya", "george"} <= ids
    assert "priya" not in ids
    for p in body["patients"]:
        assert "bucket" in p and p["bucket"] in ("needs_you", "watch", "on_track")
    assert [p["severity"] for p in body["patients"]] == sorted((p["severity"] for p in body["patients"]),
                                                                reverse=True)
    george = next(p for p in body["patients"] if p["id"] == "george")
    assert george["bucket"] == "needs_you"
    assert george["severity"] == body["patients"][0]["severity"]  # alert severity is the top tier


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


def _patient(body: dict, patient_id: str) -> dict:
    return next(p for p in body["patients"] if p["id"] == patient_id)


def test_resolved_alert_is_not_counted_as_open(dash_client: TestClient) -> None:
    """I2: an alert with a `resolved_by_therapist` step must not put the patient in needs_you."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "resolved_pat")
    assert p["bucket"] != "needs_you"
    assert p["kind"] != "alert"


def test_acknowledged_memory_is_ignored(dash_client: TestClient) -> None:
    """I10: an acknowledged compensation memory event must not drive triage."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "acked_pat")
    assert p["kind"] != "compensation"
    assert p["bucket"] == "on_track"


def test_flagged_reps_in_last_session_go_to_watch(dash_client: TestClient) -> None:
    """I10: 3 of 4 reps flagged `knee_cave_r` in the last session -> watch, with the deterministic top flag."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "flags_pat")
    assert p["bucket"] == "watch" and p["kind"] == "flags"
    assert p["reason"] == "Knee drifting inward on 3 of 4 reps"


def test_pain_eight_in_last_session_is_needs_you(dash_client: TestClient) -> None:
    """I10: pain_post >= 7 in the (chronologically) last finished session -> needs_you."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "pain_pat")
    assert p["bucket"] == "needs_you" and p["kind"] == "pain"
    assert p["reason"] == "Reported pain 8 out of 10 after the last session"


def test_missed_is_zero_with_no_finished_session_yet(dash_client: TestClient) -> None:
    """I3: a patient prescribed today at >= 5/week with no finished session yet must show missed == 0, not 7."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "new_pat")
    assert p["kind"] != "missed"
    assert p["bucket"] == "on_track"


def test_missed_counts_only_finished_sessions(dash_client: TestClient) -> None:
    """I3: the last FINISHED session was 3 days ago -> missed == 3 (needs_you); an unfinished session
    started today must not reset the count back toward 0."""
    body = dash_client.get("/caseload?therapist_id=okafor", headers=AUTH).json()
    p = _patient(body, "missed3_pat")
    assert p["bucket"] == "needs_you" and p["kind"] == "missed"
    assert p["reason"] == "Missed 3 sessions in a row"


async def test_sessions_done_today_counts_only_finished_sessions() -> None:
    """Minor 8: a session started today but not yet ended must not count as "done". Uses its own fresh,
    isolated database (not `dash_dsn`, which is shared and polluted by other tests' mock live sessions)
    and calls the query function directly, with no app/client lifecycle involved."""
    from server.db import dashboard_queries
    from server.db.pool import Database

    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                         "VALUES (gen_random_uuid(), 'maya', 'squat', now(), now() + interval '10 minutes')")
            conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                         "VALUES (gen_random_uuid(), 'maya', 'squat', now(), NULL)")  # unfinished
        db = Database(url)
        await db.open()
        try:
            assert await dashboard_queries.sessions_done_today(db, ["maya"]) == 1
        finally:
            await db.close()


def test_live_now_scoped_to_therapist_caseload(make_client: Callable[..., TestClient]) -> None:
    """I1: `_live_now` must only return the current session when its patient is among the queried
    therapist's caseload `rows`; otherwise it must be None, never another therapist's patient. Drives the
    mock engine's phase directly (set by app startup for MOCK_PATIENT_ID "maya") instead of waiting on
    mock-frame timing, and needs no database."""
    from server.api.dashboard import _live_now
    from server.state import get_state

    make_client()  # starts the app lifespan: a mock session for "maya" is now state.sessions.current
    state = get_state()
    state.bridge.live.phase = "active"
    assert state.sessions.current is not None and state.sessions.current.patient_id == "maya"

    assert _live_now([{"id": "maya", "name": "Maya Ellis"}]) is not None
    assert _live_now([{"id": "george", "name": "George Alvarez"}]) is None
    assert _live_now([]) is None


# ---- progress-note approval (Task 11) --------------------------------------------------------------------

def test_progress_note_routes_need_the_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                       json={"therapist_id": "okafor", "text": "ok"}).status_code == 401
    assert client.get(f"/sessions/{NOTE_SESSION_ID}/progress-note").status_code == 401


def test_progress_note_routes_503_without_db(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                       json={"therapist_id": "okafor", "text": "ok"}, headers=AUTH).status_code == 503
    assert client.get(f"/sessions/{NOTE_SESSION_ID}/progress-note", headers=AUTH).status_code == 503


def test_progress_note_sample_shape() -> None:
    post = json.loads((SAMPLES / "rest_post_progress_note.json").read_text())
    assert set(post) == {"ok", "session_id", "approved_by", "approved_at"}
    get = json.loads((SAMPLES / "rest_get_progress_note.json").read_text())
    assert set(get) == {"approved", "text", "approved_by", "approved_at", "edited"}


def test_progress_note_rejects_empty_or_oversized_text(dash_client: TestClient) -> None:
    for text in ("", "   "):
        r = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                             json={"therapist_id": "okafor", "text": text}, headers=AUTH)
        assert r.status_code == 422
    r = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                         json={"therapist_id": "okafor", "text": "x" * 4001}, headers=AUTH)
    assert r.status_code == 422
    r = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                         json={"therapist_id": "bad id!", "text": "ok"}, headers=AUTH)
    assert r.status_code == 422


def test_progress_note_404_for_unknown_session(dash_client: TestClient) -> None:
    unknown = str(uuid.uuid4())
    assert dash_client.post(f"/sessions/{unknown}/progress-note",
                            json={"therapist_id": "okafor", "text": "ok"}, headers=AUTH).status_code == 404
    assert dash_client.get(f"/sessions/{unknown}/progress-note", headers=AUTH).status_code == 404


def test_progress_note_403_for_another_therapist(dash_client: TestClient) -> None:
    """OTHER_NOTE_SESSION_ID belongs to priya, who is nguyen's patient, not okafor's."""
    r = dash_client.post(f"/sessions/{OTHER_NOTE_SESSION_ID}/progress-note",
                         json={"therapist_id": "okafor", "text": "ok"}, headers=AUTH)
    assert r.status_code == 403


def test_progress_note_approve_then_read_back_and_edited_flag(dash_client: TestClient) -> None:
    get_before = dash_client.get(f"/sessions/{NOTE_SESSION_ID}/progress-note", headers=AUTH).json()
    assert get_before == {"approved": False, "text": None, "approved_by": None, "approved_at": None,
                          "edited": False}

    post = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                            json={"therapist_id": "okafor", "text": "Edited note for Maya"}, headers=AUTH)
    assert post.status_code == 200
    body = post.json()
    assert body["ok"] is True
    assert body["session_id"] == NOTE_SESSION_ID
    assert body["approved_by"] == "okafor"
    assert body["approved_at"]

    get1 = dash_client.get(f"/sessions/{NOTE_SESSION_ID}/progress-note", headers=AUTH).json()
    assert get1["approved"] is True
    assert get1["text"] == "Edited note for Maya"
    assert get1["approved_by"] == "okafor"
    assert get1["edited"] is True  # differs from the session's ai_note

    # A second approval (this time matching the AI draft verbatim) must overwrite what GET returns.
    post2 = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                             json={"therapist_id": "okafor", "text": "AI draft note for Maya"}, headers=AUTH)
    assert post2.status_code == 200

    get2 = dash_client.get(f"/sessions/{NOTE_SESSION_ID}/progress-note", headers=AUTH).json()
    assert get2["text"] == "AI draft note for Maya"


# ---- PATCH /patients/{id}/limits (heart-rate limit, Task R3) --------------------------------------------

def test_limits_route_needs_the_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    r = client.patch("/patients/maya/limits", json={"therapist_id": "okafor", "hr_limit": 140})
    assert r.status_code == 401


def test_limits_route_503_without_db(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    r = client.patch("/patients/maya/limits", json={"therapist_id": "okafor", "hr_limit": 140}, headers=AUTH)
    assert r.status_code == 503


def test_limits_rejects_out_of_range_hr_limit(dash_client: TestClient) -> None:
    for bad in (39, 221):
        r = dash_client.patch("/patients/maya/limits", json={"therapist_id": "okafor", "hr_limit": bad},
                              headers=AUTH)
        assert r.status_code == 422


def test_limits_sample_shape() -> None:
    body = json.loads((SAMPLES / "rest_patch_patient_limits.json").read_text())
    assert set(body) == {"patient_id", "hr_limit"}


def test_limits_404_for_unknown_patient(dash_client: TestClient) -> None:
    r = dash_client.patch("/patients/nobody/limits", json={"therapist_id": "okafor", "hr_limit": 140},
                          headers=AUTH)
    assert r.status_code == 404


def test_limits_403_for_another_therapist(dash_client: TestClient) -> None:
    """priya belongs to nguyen, not okafor."""
    r = dash_client.patch("/patients/priya/limits", json={"therapist_id": "okafor", "hr_limit": 140},
                          headers=AUTH)
    assert r.status_code == 403


def test_limits_persisted_and_visible_in_get_patient_and_logs_agent_action(dash_client: TestClient,
                                                                           dash_dsn: str) -> None:
    before = dash_client.get("/patients/maya", headers=AUTH).json()["patient"]["hr_limit"]
    r = dash_client.patch("/patients/maya/limits", json={"therapist_id": "okafor", "hr_limit": 140}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"patient_id": "maya", "hr_limit": 140}

    after = dash_client.get("/patients/maya", headers=AUTH).json()["patient"]["hr_limit"]
    assert after == 140
    assert after != before

    with psycopg.connect(dash_dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT detail FROM agent_actions WHERE agent = 'therapist' AND action = 'hr_limit_set' "
            "AND patient_id = 'maya' ORDER BY time DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == {"from": before, "to": 140, "by": "okafor"}


# ---- session history (final review, time-lapse pickers) ---------------------------------------------------

def test_session_history_sample_shape() -> None:
    body = json.loads((SAMPLES / "rest_get_patient_session_history.json").read_text())
    assert set(body) == {"patient_id", "exercise_id", "sessions"}
    s = body["sessions"][0]
    assert set(s) == {"id", "started_at", "exercise_id", "reps_done"}
    times = [x["started_at"] for x in body["sessions"]]
    assert times == sorted(times, reverse=True)


def test_session_history_finished_only_newest_first_and_bounded(dash_client: TestClient) -> None:
    body = dash_client.get("/patients/missed3_pat/session-history", headers=AUTH).json()
    # missed3_pat has one finished and one unfinished session: only the finished one comes back.
    assert body["patient_id"] == "missed3_pat" and body["exercise_id"] is None
    assert len(body["sessions"]) == 1
    assert set(body["sessions"][0]) == {"id", "started_at", "exercise_id", "reps_done"}

    pain = dash_client.get("/patients/pain_pat/session-history?exercise_id=squat", headers=AUTH).json()["sessions"]
    assert len(pain) == 2 and pain[0]["started_at"] > pain[1]["started_at"]
    assert dash_client.get("/patients/pain_pat/session-history?exercise_id=lunge",
                           headers=AUTH).json()["sessions"] == []
    assert len(dash_client.get("/patients/pain_pat/session-history?limit=1", headers=AUTH).json()["sessions"]) == 1


def test_session_history_validates_input(dash_client: TestClient) -> None:
    assert dash_client.get("/patients/nobody/session-history", headers=AUTH).status_code == 404
    assert dash_client.get("/patients/bad%20id/session-history", headers=AUTH).status_code == 422
    assert dash_client.get("/patients/maya/session-history?limit=0", headers=AUTH).status_code == 422
    assert dash_client.get("/patients/maya/session-history?limit=201", headers=AUTH).status_code == 422
    assert dash_client.get("/patients/maya/session-history?exercise_id=a;b", headers=AUTH).status_code == 422


def test_memory_route_drops_insight_investigation(dash_client: TestClient, dash_dsn: str) -> None:
    with psycopg.connect(dash_dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO memory_events (patient_id, kind, message, evidence) VALUES ('george', 'pain', "
                     "'x', %s)", (json.dumps({"max_pain_recent": 5, "investigation": {"steps": [1, 2, 3]}}),))
    events = dash_client.get("/patients/george/memory", headers=AUTH).json()["events"]
    assert events and all("investigation" not in e["evidence"] for e in events)
    assert any(e["evidence"].get("max_pain_recent") == 5 for e in events)


def test_progress_note_rejects_huge_raw_text(dash_client: TestClient) -> None:
    r = dash_client.post(f"/sessions/{NOTE_SESSION_ID}/progress-note",
                         json={"therapist_id": "okafor", "text": " " * 9000}, headers=AUTH)
    assert r.status_code == 422


def test_live_now_while_camera_streams_but_engine_idle(make_client: Callable[..., TestClient]) -> None:
    """A remote camera whose one-camera body is still measuring sends frames before `arrived`: the session must
    already count as live (room + video on the dashboard), and stop counting once frames stop."""
    import time as _time

    from server.api.dashboard import _live_now
    from server.engine_bridge import STREAMING_WINDOW_S
    from server.state import get_state

    make_client()
    state = get_state()
    live = state.bridge.live
    live.phase = "idle"
    live.last_frame_at = None
    assert _live_now([{"id": "maya", "name": "Maya Ellis"}]) is None
    live.on_frame({"type": "frame", "exercise_id": "squat", "flags": ["measuring"]})
    assert live.streaming and live.payload()["streaming"] is True
    assert _live_now([{"id": "maya", "name": "Maya Ellis"}]) is not None
    live.last_frame_at = _time.monotonic() - STREAMING_WINDOW_S - 1
    assert _live_now([{"id": "maya", "name": "Maya Ellis"}]) is None
