from __future__ import annotations

from server import exercises


def test_known_and_unknown_names() -> None:
    assert exercises.display_name("squat") == "Mini squats"  # what the LCD showed before the catalog existed
    assert exercises.display_name("glute_bridge") == "Glute bridge"
    assert exercises.display_name("single_leg_stance") == "Single-leg stance"
    assert exercises.display_name("heel_slide") == "Heel slide"  # unknown ids are humanised, never KeyError


def test_describe_shape() -> None:
    d = exercises.describe("squat")
    assert d == {"exercise_id": "squat", "exercise_name": "Mini squats", "metric_label": "Knee bend",
                 "unit": "°", "target_key": "knee_deg"}
    assert exercises.describe("mystery")["metric_label"] == "Progress"


def test_live_state_uses_the_catalog() -> None:
    from server.engine_bridge import LiveState

    live = LiveState()
    live.on_session_start("Maya", "glute_bridge")
    assert live.exercise_name == exercises.display_name("glute_bridge")
