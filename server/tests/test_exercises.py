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


def test_watch_exercises() -> None:
    assert exercises.WATCH_EXERCISES == {"forearm_rotation", "shoulder_rotation", "wrist_flexion"}
    assert exercises.display_name("forearm_rotation") == "Forearm turns"
    assert exercises.display_name("shoulder_rotation") == "Shoulder turns"
    assert exercises.display_name("wrist_flexion") == "Wrist bends"
    assert exercises.is_watch_exercise("forearm_rotation") and not exercises.is_watch_exercise("squat")
    assert not exercises.is_watch_exercise(None)
    assert exercises.describe("forearm_rotation")["target_key"] == "rotation_deg"
    assert "watch" not in exercises.describe("forearm_rotation")  # describe() keeps its 5-key shape


def test_rotation_goal() -> None:
    assert exercises.rotation_goal_deg({"rotation_deg": 70}) == 70.0
    assert exercises.rotation_goal_deg({}) == exercises.DEFAULT_ROTATION_GOAL_DEG == 80.0
    assert exercises.rotation_goal_deg(None) == 80.0
    for bad in (True, "80", 5, 500):
        assert exercises.rotation_goal_deg({"rotation_deg": bad}) == 80.0
