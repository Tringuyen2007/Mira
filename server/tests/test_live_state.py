from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from server.engine_bridge import INCIDENT_CLOSED, LiveState
from server.hub import Hub
from server.tests.conftest import TOKEN


def test_caption_follows_greeting_cue_and_summary() -> None:
    live = LiveState()
    live.on_event({"type": "greeting", "text": "Hi Maya!", "t": 1.0})
    assert live.payload()["caption"] == {"text": "Hi Maya!", "source": "greeting", "t": 1.0}
    live.on_event({"type": "cue", "id": "knees_out", "text": "Knees out.", "t": 2.0})
    assert live.payload()["caption"]["text"] == "Knees out."
    live.on_event({"type": "summary_ready", "spoken": "Set done.", "t": 3.0})
    assert live.payload()["caption"] == {"text": "Set done.", "source": "summary_ready", "t": 3.0}
    live.on_event({"type": "cue", "id": "x", "text": "", "t": 4.0})
    assert live.payload()["caption"]["text"] == "Set done.", "empty text never blanks the caption"


def test_session_start_sets_name_and_exercise_and_clears_caption() -> None:
    live = LiveState()
    live.on_event({"type": "cue", "text": "old", "t": 1.0})
    live.on_session_start("Maya", "squat")
    p = live.payload()
    assert (p["first_name"], p["exercise_name"], p["caption"]) == ("Maya", "Mini squats", None)
    live.on_session_start("Sam", "lunge")
    assert live.payload()["exercise_name"] == "Lunge", "unknown ids fall back to a readable name"


def test_session_start_resets_phase_and_counters() -> None:
    live = LiveState()
    live.on_event({"type": "countdown", "n": 3, "set": 1})
    live.on_event({"type": "rep", "index": 4})
    live.on_event({"type": "paused", "reason": "user"})
    live.on_event({"type": "stopped"})
    live.on_event({"type": "set_done", "reps_target": 10, "sets_target": 3})
    live.on_event({"type": "hr", "bpm": 92})
    live.on_frame({"exercise_id": "squat", "set_no": 2, "angles": {"knee_l": 80.0}, "load_l_pct": 55.0,
                   "confidence_avg": 0.9, "flags": ["knee_valgus"]})
    live.on_session_start("Maya", "squat")
    p = live.payload()
    assert (p["phase"], p["rep"], p["paused"], p["countdown"], p["cue_id"]) == ("idle", 0, False, None, None)
    assert (p["set"], p["reps_target"], p["sets_target"], p["hr"]) == (None, None, None, None)
    assert (p["load_l_pct"], p["load_conf"], p["main_angle"], p["flags"]) == (None, None, None, [])


def test_incident_flag() -> None:
    live = LiveState()
    live.on_event({"type": "cue", "id": "check_in", "text": "Are you okay?"})
    assert live.payload()["incident"] is True
    live.on_event({"type": "alert", "step": "resolved"})
    assert live.payload()["incident"] is False
    live.on_event({"type": "fall", "stage": "confirmed"})
    assert live.payload()["incident"] is True
    live.on_event({"type": INCIDENT_CLOSED})
    assert live.payload()["incident"] is False


def test_alert_steps_after_the_close_leave_the_incident_closed() -> None:
    live = LiveState()
    live.on_agent_event({"type": "alert", "step": "detected"})
    assert live.payload()["incident"] is True
    live.on_agent_event({"type": "alert", "step": "closed"})
    live.on_agent_event({"type": "alert", "step": "webhook_call_analyzed"})
    assert live.payload()["incident"] is False


def test_escalation_keeps_the_incident_open() -> None:
    live = LiveState()
    live.on_agent_event({"type": "alert", "step": "detected"})
    live.on_agent_event({"type": "alert", "step": "escalated"})
    assert live.payload()["incident"] is True


def test_only_a_confirmed_fall_opens_an_incident() -> None:
    live = LiveState()
    live.on_agent_event({"type": "fall", "stage": "suspected"})
    live.on_agent_event({"type": "fall"})
    assert live.payload()["incident"] is False
    live.on_agent_event({"type": "fall", "stage": "confirmed"})
    assert live.payload()["incident"] is True


def test_existing_fields_unchanged() -> None:
    live = LiveState()
    live.on_event({"type": "countdown", "n": 3, "set": 1, "reps_target": 10, "sets_target": 3})
    p = live.payload()
    assert (p["phase"], p["countdown"], p["rep"], p["reps_target"]) == ("countdown", 3, 0, 10)


def test_agent_events_only_touch_the_new_fields() -> None:
    live = LiveState()
    live.on_event({"type": "countdown", "n": 2, "set": 1})
    # The Coach can emit a focus cue during the countdown; it must not flip the phase to active.
    live.on_agent_event({"type": "cue", "id": "focus_depth", "text": "Sit back a little."})
    p = live.payload()
    assert (p["phase"], p["countdown"], p["cue_id"]) == ("countdown", 2, None)
    assert p["caption"]["text"] == "Sit back a little."


def test_hub_emit_observers_see_agent_events() -> None:
    hub = Hub()
    seen: list[dict[str, Any]] = []
    hub.emit_observers.append(seen.append)
    hub.emit("greeting", {"text": "Hi"})
    assert seen == [{"type": "greeting", "text": "Hi"}]


def test_live_stream_carries_first_name(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    with client.websocket_connect(f"/ws/live?token={TOKEN}") as ws:
        msg = ws.receive_json()
        assert msg["first_name"] == "Maya" and msg["exercise_name"] == "Mini squats"
