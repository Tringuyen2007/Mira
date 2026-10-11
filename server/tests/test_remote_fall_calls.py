"""A remote vision agent's fall (docs/vision-media-contract.md, "Falls and safety events") reaches the Safety
Guardian over /ws/vision and, with calls on and no response in the check-in window, places exactly one Retell call."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import AUTH
from server.tests.test_retell_client import CONTACT_PHONE, FakeCalls

VISION_TOKEN = "vision-token-0123456789abcdef"
CHECKIN_S = 0.3


@pytest.fixture
def remote(make_client: Callable[..., TestClient]) -> tuple[TestClient, FakeCalls, list[str], str]:
    """Remote source, calls on, FakeCalls behind the notifier, a session started and armed, steps collected."""
    client = make_client(MIRA_SOURCE="remote", MIRA_VISION_TOKEN=VISION_TOKEN, CALLS_DISABLED="0",
                         RETELL_API_KEY="key_test_not_real_0123456789", RETELL_FROM_NUMBER="+15550100999",
                         RETELL_AGENT_ID="agent_test", CHECKIN_TIMEOUT_S=str(CHECKIN_S))
    state = get_state()
    calls = FakeCalls({"call_1": [("registered", None), ("ongoing", None), ("ended", {"reason": "user_hangup"}),
                                  ("ended", {"data": {"contact_reached": True, "notes": "ok"}})]})
    assert state.notifier is not None and state.notifier.mode == "live"
    state.notifier._client_obj = SimpleNamespace(call=calls)
    state.notifier.poll_interval_s = 0.05
    steps: list[str] = []
    state.on_alert_step.append(lambda inc, step, payload: steps.append(step))
    started = client.post("/sessions", headers=AUTH).json()
    sid = started["session_id"]
    # The patient's iOS "Start today's session" sends this command; it opens the privacy gate for the agent.
    state.bridge.send_to_engine({"type": "command", "name": "start", "session_id": sid})
    return client, calls, steps, sid


def fall_event(sid: str) -> dict[str, Any]:
    return {"type": "fall", "stage": "confirmed", "session_id": sid, "t": time.time(), "drop_m": 0.9,
            "peak_down_speed_mps": 2.1, "posture": "lying", "on_mat": True}


def wait_for(cond: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.02)


def test_remote_fall_with_no_response_calls_the_emergency_contact(
        remote: tuple[TestClient, FakeCalls, list[str], str]) -> None:
    client, calls, steps, sid = remote
    with client.websocket_connect(f"/ws/vision?token={VISION_TOKEN}") as vision:
        vision.send_json(fall_event(sid))
        wait_for(lambda: "checked_in" in steps)
        vision.send_json({"type": "fall_status", "session_id": sid, "t": time.time(), "posture": "lying",
                          "moving": False, "still_s": 2.0, "on_mat": True})
        wait_for(lambda: "escalated" in steps)
    assert len(calls.created) == 1
    created = calls.created[0]
    assert created["to_number"] == CONTACT_PHONE and created["from_number"] == "+15550100999"
    variables = created["retell_llm_dynamic_variables"]
    assert variables["patient_first_name"] == "Maya" and variables["contact_name"] == "Eric"
    assert "fall" in variables["situation"].lower() or "floor" in variables["situation"].lower()
    assert steps[:3] == ["detected", "checked_in", "escalated"]
    wait_for(lambda: "closed" in steps, timeout_s=5)


def test_remote_fall_answered_in_time_places_no_call(remote: tuple[TestClient, FakeCalls, list[str], str]) -> None:
    client, calls, steps, sid = remote
    with client.websocket_connect(f"/ws/vision?token={VISION_TOKEN}") as vision:
        vision.send_json(fall_event(sid))
        wait_for(lambda: "checked_in" in steps)
        vision.send_json({"type": "gesture", "name": "thumbs_up", "session_id": sid, "t": time.time()})
        wait_for(lambda: "resolved" in steps)
    time.sleep(CHECKIN_S * 2)
    assert calls.created == [] and "escalated" not in steps


def test_duplicate_remote_fall_events_place_one_call(remote: tuple[TestClient, FakeCalls, list[str], str]) -> None:
    client, calls, steps, sid = remote
    with client.websocket_connect(f"/ws/vision?token={VISION_TOKEN}") as vision:
        vision.send_json(fall_event(sid))
        vision.send_json(fall_event(sid))
        wait_for(lambda: "escalated" in steps)
        vision.send_json(fall_event(sid))
        time.sleep(CHECKIN_S * 2)
    assert len(calls.created) == 1 and steps.count("detected") == 1


def test_remote_fall_before_the_session_is_armed_is_dropped(make_client: Callable[..., TestClient]) -> None:
    """Privacy gate: nothing the agent sends counts until the patient started the session from the phone."""
    client = make_client(MIRA_SOURCE="remote", MIRA_VISION_TOKEN=VISION_TOKEN, CHECKIN_TIMEOUT_S=str(CHECKIN_S))
    steps: list[str] = []
    get_state().on_alert_step.append(lambda inc, step, payload: steps.append(step))
    sid = client.post("/sessions", headers=AUTH).json()["session_id"]  # dashboard start: does not arm the agent
    with client.websocket_connect(f"/ws/vision?token={VISION_TOKEN}") as vision:
        vision.send_json(fall_event(sid))
        time.sleep(CHECKIN_S * 2)
    assert steps == []
