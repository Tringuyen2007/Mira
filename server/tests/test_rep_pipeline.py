from __future__ import annotations

import pytest

from server.agents.rep_pipeline import CorrectRepPipeline


def _cycle(pipeline: CorrectRepPipeline, *, started_at: float = 0.0, quality: str = "good",
           flags: list[str] | None = None) -> list[dict]:
    events: list[dict] = []
    for index, angle in enumerate([8, 22, 40, 62, 73, 48, 24, 8]):
        events.extend(pipeline.observe(session_id="s1", set_no=1, exercise_id="squat", angle=angle,
                                       timestamp=started_at + index * 0.2, confidence=0.95,
                                       form_quality=quality, flags=flags))
    return events


def test_good_cycle_emits_existing_lcd_rep_event() -> None:
    events = _cycle(CorrectRepPipeline())
    assert events[0]["type"] == "rep"
    assert events[0]["index"] == 1 and events[0]["quality"] == "good"


def test_poor_form_cycle_never_emits_a_rep() -> None:
    events = _cycle(CorrectRepPipeline(), quality="fix_form", flags=["knee_cave_l"])
    assert events[0]["type"] == "rep_attempt"
    assert events[0]["quality"] == "fix_form" and events[0]["flags"] == ["knee_cave_l"]


def test_low_confidence_cycle_never_emits_a_rep() -> None:
    pipeline = CorrectRepPipeline()
    events: list[dict] = []
    for index, angle in enumerate([8, 22, 40, 62, 73, 48, 24, 8]):
        events.extend(pipeline.observe(session_id="s1", set_no=1, exercise_id="squat", angle=angle,
                                       timestamp=index * 0.2, confidence=0.3 if index == 3 else 0.95,
                                       form_quality="good"))
    assert events[0]["type"] == "rep_attempt"
    assert events[0]["flags"] == ["low_pose_confidence"]


def test_good_count_skips_bad_attempt_and_continues() -> None:
    pipeline = CorrectRepPipeline()
    assert _cycle(pipeline, quality="fix_form")[0]["type"] == "rep_attempt"
    events = _cycle(pipeline, started_at=2.0)
    assert events[0]["type"] == "rep" and events[0]["index"] == 1


def test_unknown_exercise_requires_an_explicit_profile() -> None:
    with pytest.raises(ValueError, match="no rep-counting profile"):
        CorrectRepPipeline().observe(session_id="s1", set_no=1, exercise_id="jump", angle=0, timestamp=0,
                                     confidence=1, form_quality="good")
