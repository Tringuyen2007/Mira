from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from server.agents import incident_snapshot as snap
from server.agents.safety_guardian import Incident
from server.engine_bridge import lying_frame_body, squat_frame_body
from server.notify.templates import CALL_VARIABLES, LIMITS_SENTENCE, render_opening
from server.state import PatientProfile, default_profile

# 3:42 PM in America/Chicago on a demo day.
FALL_T = datetime(2026, 10, 8, 15, 42, 5, tzinfo=ZoneInfo("America/Chicago")).timestamp()
FALL = {"type": "fall", "stage": "confirmed", "drop_m": 0.9, "peak_down_speed_mps": 2.1, "posture": "unknown",
        "on_mat": True, "exercise_id": "squat", "set": 2, "rep": 7, "t": FALL_T}


def lying_frames(seconds: float, *, start: float = FALL_T, moving_after: float | None = None) -> list[dict[str, Any]]:
    """A recorded fall: 2 s of squats, then lying still on the mat (optionally moving later)."""
    rng = random.Random(3)
    frames = [{"t": start - 2 + i / 30, **squat_frame_body(0.5, 0.45, 0.0, rng)} for i in range(60)]
    for i in range(int(seconds * 30)):
        t = start + i / 30
        moving = moving_after is not None and t - start >= moving_after
        frames.append({"t": t, **lying_frame_body(moving, rng)})
    return frames


def incident(profile: PatientProfile | None = None, **kw: Any) -> Incident:
    defaults: dict[str, Any] = {"alert_id": "a-1", "kind": "fall", "session_id": "s-1",
                                "profile": profile or default_profile("maya"), "started_t": FALL_T,
                                "trigger": dict(FALL), "checkins": 1, "hr_before": 112, "hr_now": 118}
    return Incident(**{**defaults, **kw})


def with_snapshot(inc: Incident, frames: list[dict[str, Any]]) -> Incident:
    inc.snapshot = snap.build_snapshot(inc, frames, inc.profile)
    return inc


def test_recorded_fall_gives_the_expected_sentence() -> None:
    inc = with_snapshot(incident(), lying_frames(35))
    assert snap.situation_text(inc.snapshot) == (
        "At 3:42 PM, during set 2 of her squats, Mira's cameras saw Maya drop quickly to the floor, about 0.9 "
        "meters in under a second. She appears to be lying down on the mat and has barely moved for 35 seconds. "
        "Mira asked her once to give a thumbs up or raise her hand, and she has not responded. "
        "Her heart rate was 112 just before and is 118 now.")
    assert snap.situation_short(inc.snapshot) == "Mira saw Maya fall at 3:42 PM and she hasn't responded."
    assert snap.status_text(inc.snapshot) == "Still lying down on the mat, not moving. No response yet."


def test_missing_posture_is_estimated_from_the_ring_buffer() -> None:
    s = with_snapshot(incident(), lying_frames(10)).snapshot
    assert s["posture"] == "lying" and s["posture_estimated"] is True
    assert "appears to be lying down" in snap.situation_text(s)


def test_engine_posture_is_used_and_not_labelled_estimated() -> None:
    inc = incident(last_status={"posture": "sitting", "moving": False, "still_s": 12.0, "on_mat": True})
    s = with_snapshot(inc, lying_frames(12)).snapshot
    assert s["posture"] == "sitting" and s["posture_estimated"] is False and s["still_s"] == 12
    assert "She is now sitting up on the mat and has barely moved for 12 seconds." in snap.situation_text(s)


def test_no_frames_and_unknown_posture_says_mira_cant_tell() -> None:
    s = with_snapshot(incident(), []).snapshot
    assert s["posture"] is None and s["posture_estimated"] is False
    text = snap.situation_text(s)
    assert "Mira can't tell her position now." in text
    assert snap.status_text(s) == "No response yet."


def test_no_heart_rate_says_not_available() -> None:
    s = with_snapshot(incident(hr_before=None, hr_now=None), lying_frames(5)).snapshot
    text = snap.situation_text(s)
    assert text.endswith("Her heart rate is not available.")
    assert "None" not in text


def test_moving_after_the_fall_is_reported() -> None:
    s = with_snapshot(incident(), lying_frames(20, moving_after=15)).snapshot
    assert s["moving"] is True
    assert "lying down on the mat and is moving." in snap.situation_text(s)


@pytest.mark.parametrize("com_y, lean, posture", [(0.12, 88, "lying"), (0.3, 10, "sitting"),
                                                  (0.55, 15, "kneeling"), (0.95, 5, "standing")])
def test_estimate_posture(com_y: float, lean: float, posture: str) -> None:
    assert snap.estimate_posture(com_y, lean) == posture


@pytest.mark.parametrize("kind, extra, first", [
    ("pain", {"pain_level": 8}, "Maya reported severe pain of 8 out of 10 on her phone, and Mira paused the session."),
    ("hr", {}, "Maya's heart rate stayed above her limit of 130 for more than 5 seconds, and Mira paused the session."),
])
def test_pain_and_hr_situations_have_3_to_4_sentences(kind: str, extra: dict[str, Any], first: str) -> None:
    trigger = {"type": "command", "t": FALL_T, "exercise_id": "squat", "set": 1}
    s = with_snapshot(incident(kind=kind, trigger=trigger, **extra), []).snapshot
    text = snap.situation_text(s)
    assert first in text
    assert 3 <= text.count(". ") + 1 <= 4


def test_call_variables_are_all_non_empty_strings_even_with_missing_profile_data() -> None:
    profile = replace(default_profile("maya"), phone=None, contact_name=None, therapist_name=None,
                      therapist_phone=None, condition=None, weeks_since_surgery=None)
    inc = with_snapshot(incident(profile, hr_before=None, hr_now=None), [])
    variables = snap.call_variables(inc, None)
    assert set(variables) == set(CALL_VARIABLES)
    for key, value in variables.items():
        assert isinstance(value, str) and value and "None" not in value, key
    assert variables["contact_name"] == "there"
    assert variables["patient_phone"] == "the number you have for her"
    assert variables["limits"] == LIMITS_SENTENCE


def test_rendered_opening_and_voicemail_contain_no_braces() -> None:
    inc = with_snapshot(incident(), lying_frames(35))
    rendered = render_opening(snap.call_variables(inc, "Linh"))
    assert rendered["opening"].startswith("Hi Linh, this is Mira, the home exercise assistant for Maya. "
                                          "This is an urgent safety call. At 3:42 PM,")
    assert rendered["voicemail"] == ("Hi Linh, this is Mira, the home exercise assistant for Maya. Urgent: Mira saw "
                                     "Maya fall at 3:42 PM and she hasn't responded. Please call Maya at the number "
                                     "you have for her. If you can't reach her, call 911.")
    for text in rendered.values():
        assert "{{" not in text and "}}" not in text


def test_render_opening_refuses_a_missing_variable() -> None:
    with pytest.raises(ValueError, match="unrendered"):
        render_opening({"contact_name": "Linh"})


def test_pronouns_come_from_the_profile() -> None:
    profile = replace(default_profile("maya"), first_name="Sam", pronoun_subj="they", pronoun_obj="them",
                      pronoun_poss="their")
    inc = with_snapshot(incident(profile), lying_frames(20))
    text = snap.situation_text(inc.snapshot)
    assert "during set 2 of their squats" in text and "They appear to be lying down" in text
    assert "Mira asked them once to give a thumbs up or raise their hand, and they have not responded." in text
    assert snap.situation_short(inc.snapshot).endswith("and they haven't responded.")
    assert "whether they are conscious" in snap.call_variables(inc, "Lee")["limits"]


def test_context_text_from_cached_profile() -> None:
    profile = replace(default_profile("maya"), pain_today=3, falls_30d=1)
    assert snap.context_text(incident(profile)) == (
        "Recovering from a right knee replacement 3 weeks ago. She rated her pain 3 out of 10 today. "
        "1 other fall in the last month.")
    assert snap.context_text(incident()) == "Recovering from a right knee replacement 3 weeks ago. No falls in the last month."


def test_status_after_a_response() -> None:
    inc = incident(responses=["thumbs_up"], last_status={"posture": "sitting", "on_mat": True})
    assert snap.status_text(with_snapshot(inc, []).snapshot) == "Responded. Sitting up on the mat."
