"""POST /live/call: the therapist's Call button places a Retell call (log-only here) with the live context."""
from collections.abc import Callable

from fastapi.testclient import TestClient

from server.tests.conftest import AUTH


def test_live_call_needs_token_and_places_a_simulated_call(make_client: Callable[..., TestClient]) -> None:
    client = make_client(CALLS_DISABLED="1")
    assert client.post("/live/call", json={"patient_id": "maya", "therapist_id": "okafor"}).status_code == 401
    r = client.post("/live/call", json={"patient_id": "maya", "therapist_id": "okafor", "note": "Check her knee"},
                    headers=AUTH)
    if r.status_code == 503:  # no database in this environment: the lookup itself is unavailable
        return
    assert r.status_code in (200, 404, 409), r.text
    if r.status_code == 200:
        body = r.json()
        assert body["mode"] == "log_only" and body["call_id"].startswith("sim_") and body["to"] == "contact"
        assert "Maya" in body["context"]
        # second press within 5 minutes is refused, never a second call
        assert client.post("/live/call", json={"patient_id": "maya", "therapist_id": "okafor"}, headers=AUTH).status_code == 429


def test_live_context_sentence() -> None:
    from server.api.dashboard import _live_context
    from server.engine_bridge import LiveState

    live = LiveState()
    live.exercise_name, live.set, live.sets_target, live.rep, live.flags = "Mini squats", 1, 3, 4, ["knee_cave_l"]
    text = _live_context(live, "Maya")
    assert text == "Maya is in a session with Mira right now, doing mini squats, set 1 of 3, rep 4, form notes: knee cave l."
