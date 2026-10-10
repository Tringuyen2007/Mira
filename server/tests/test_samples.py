from __future__ import annotations

import json
from pathlib import Path

from server.hub import POSE_FIELDS

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SERVER_EVENTS = {"arrived", "greeting", "awaiting_gesture", "gesture", "countdown", "cue", "rep", "set_done",
                 "paused", "resumed", "fatigue", "fall", "fall_status", "alert", "haptic", "summary_ready",
                 "proposal_created", "coach_adapt", "device_health"}
CLIENT_EVENTS = {"command", "hr", "wrist", "watch_button", "device_health"}


def _load(name: str) -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def test_all_samples_are_valid_json() -> None:
    files = list(SAMPLES.glob("*.json"))
    assert files
    for path in files:
        json.loads(path.read_text(encoding="utf-8"))


def test_every_ws_event_type_has_a_sample() -> None:
    for kind in SERVER_EVENTS:
        assert _load(f"ws_events_{kind}")["type"] == kind
    for kind in CLIENT_EVENTS:
        assert _load(f"ws_events_client_{kind}")["type"] == kind


def test_pose_and_frame_samples_match_contract() -> None:
    assert set(_load("ws_pose")) == set(POSE_FIELDS)
    frame = _load("engine_frame")
    assert {"type", "seq", "t", "session_id", "exercise_id", "set_no", "joints", "confidence", "angles", "com",
            "load_l_pct", "heel_l_pct", "heel_r_pct", "confidence_avg", "flags"} <= set(frame)
    assert set(_load("ws_live")) >= {"phase", "exercise_id", "rep", "reps_target", "set", "sets_target", "countdown",
                                     "main_angle", "load_l_pct", "load_conf", "hr", "flags", "cue_id", "paused"}
