from __future__ import annotations

import json
from pathlib import Path

from server.hub import POSE_FIELDS

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SERVER_EVENTS = {"arrived", "greeting", "awaiting_gesture", "gesture", "countdown", "cue", "rep", "set_done",
                 "paused", "resumed", "fatigue", "fall", "fall_status", "alert", "haptic", "summary_ready",
                 "proposal_created", "coach_adapt", "device_health", "device_status", "session_summary", "message",
                 "breather", "hr_recovery", "rotation_rep", "rotation_set", "sync_progress", "limits_changed",
                 "watch_features_changed", "say"}
CLIENT_EVENTS = {"command", "hr", "wrist", "watch_button", "device_health", "watch_sync"}
REST_SAMPLES_REQUIRED_KEYS = {
    "rest_get_me.json": {"patient", "therapist", "prescriptions", "lcd", "calibration"},
    "rest_post_pair.json": {"patient_id", "token", "me"},
    "rest_get_session.json": {"session", "exercise_name", "analysis", "reps", "flags", "summary"},
    "rest_post_session_checkin.json": {"session_id", "pain_post", "effort", "note_saved"},
    "rest_get_patient_sessions.json": {"patient_id", "sessions"},
    "rest_get_me_care.json": {"therapist", "next_visit_at", "hr_limit", "emergency_contact", "alert_therapist",
                              "notes", "highlights", "unread_messages"},
    "rest_get_me_messages.json": {"messages"},
    "rest_post_me_messages.json": {"message"},
    "rest_get_patient_milestones.json": {"patient_id", "milestones"},
    "rest_get_me_watch.json": {"first_name", "hr_limit", "hr_limit_set_by_name", "therapist_name",
                               "tremor_monitoring", "prefs", "plan"},
    "rest_post_me_tremor_request.json": {"days"},
    "rest_post_me_tremor.json": {"stored"},
    "rest_patch_patient_limits_request.json": {"therapist_id", "hr_limit"},
    "rest_patch_patient_limits.json": {"patient_id", "hr_limit"},
    "rest_patch_patient_watch_features_request.json": {"tremor_monitoring"},
    "rest_patch_patient_watch_features.json": {"patient_id", "tremor_monitoring"},
    "rest_get_patient_watch.json": {"patient_id", "hr_recovery", "rotation_sets", "tremor"},
}


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


def test_rest_samples_have_required_keys() -> None:
    for name, keys in REST_SAMPLES_REQUIRED_KEYS.items():
        assert keys <= set(_load(name.removesuffix(".json")))


def test_pose_and_frame_samples_match_contract() -> None:
    assert set(_load("ws_pose")) == set(POSE_FIELDS)
    frame = _load("engine_frame")
    assert {"type", "seq", "t", "session_id", "exercise_id", "set_no", "joints", "confidence", "angles", "com",
            "load_l_pct", "heel_l_pct", "heel_r_pct", "confidence_avg", "flags"} <= set(frame)
    assert set(_load("ws_live")) >= {"phase", "exercise_id", "rep", "reps_target", "set", "sets_target", "countdown",
                                     "main_angle", "load_l_pct", "load_conf", "hr", "flags", "cue_id", "paused",
                                     "caption", "first_name", "exercise_name", "incident"}


def test_watch_samples_match_the_contract() -> None:
    assert _load("ws_events_client_watch_button_resume") == {"type": "watch_button", "name": "resume"}
    assert "rotation_deg" in _load("ws_events_client_wrist")
    assert _load("ws_events_haptic")["pattern"] == "countdown_tick"
    assert _load("ws_events_breather")["state"] == "start"
    assert set(_load("ws_events_rotation_rep")) >= {"exercise_id", "set_index", "rep", "reps_target", "angle_deg",
                                                    "goal_deg", "reached"}
    assert set(_load("ws_events_rotation_set")) >= {"exercise_id", "set_index", "angles", "best_deg", "avg_deg",
                                                    "goal_deg"}
    assert set(_load("ws_events_hr_recovery")) >= {"set_index", "peak_bpm", "bpm_60s", "drop_bpm"}
    assert set(_load("ws_events_sync_progress")) >= {"progress", "locked"}
    assert set(_load("ws_events_limits_changed")) >= {"patient_id", "hr_limit", "set_by_name"}
    assert set(_load("ws_events_watch_features_changed")) >= {"patient_id", "tremor_monitoring"}
