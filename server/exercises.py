"""The exercise catalog (MVP spec §9): one display name per exercise id, shared by the LCD caption, /me and the
phone's plan/progress screens so every surface says "Mini squats" the same way. Exercises flagged `watch` need the
Apple Watch's motion (rotation exercises; their goal is `prescriptions.targets.rotation_deg`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Exercise:
    exercise_id: str
    exercise_name: str
    metric_label: str   # the main metric, as the phone labels its big number
    unit: str
    target_key: str     # the prescriptions.targets key that holds the goal for the main metric
    watch: bool = False  # counted from the Apple Watch's rotation, not the cameras


EXERCISES: dict[str, Exercise] = {e.exercise_id: e for e in (
    Exercise("squat", "Mini squats", "Knee bend", "°", "knee_deg"),
    Exercise("sit_to_stand", "Sit to stand", "Time to stand", "s", "time_s"),
    Exercise("single_leg_stance", "Single-leg stance", "Hold", "s", "hold_s"),
    Exercise("heel_raise", "Heel raises", "Heel height", "cm", "height_cm"),
    Exercise("glute_bridge", "Glute bridge", "Hip lift", "cm", "hold_s"),
    Exercise("bird_dog", "Bird-dog", "Trunk stability", "°", "hold_s"),
    Exercise("arm_raise", "Arm raise", "Shoulder angle", "°", "shoulder_deg"),
    Exercise("forearm_rotation", "Forearm turns", "Turn", "°", "rotation_deg", watch=True),
    Exercise("shoulder_rotation", "Shoulder turns", "Turn", "°", "rotation_deg", watch=True),
    Exercise("wrist_flexion", "Wrist bends", "Bend", "°", "rotation_deg", watch=True),
)}

WATCH_EXERCISES: frozenset[str] = frozenset(e.exercise_id for e in EXERCISES.values() if e.watch)
DEFAULT_ROTATION_GOAL_DEG = 80.0
CATALOG_DEFAULT_SETS = 3   # the MOCK_WRIST demo program's set/rep counts (engine_bridge._SETS/_REPS)
CATALOG_DEFAULT_REPS = 10
_GOAL_RANGE = (10.0, 180.0)


def display_name(exercise_id: str) -> str:
    """Catalog name, or the id humanised ("heel_slide" → "Heel slide"). Never raises."""
    found = EXERCISES.get(exercise_id)
    return found.exercise_name if found else exercise_id.replace("_", " ").capitalize()


def describe(exercise_id: str) -> dict[str, str]:
    """The 5-key description the phone's plan screens use (the `watch` flag is not part of it)."""
    found = EXERCISES.get(exercise_id)
    if found is None:
        found = Exercise(exercise_id, display_name(exercise_id), "Progress", "", "")
    out = asdict(found)
    out.pop("watch", None)
    return out


def is_watch_exercise(exercise_id: str | None) -> bool:
    return exercise_id in WATCH_EXERCISES


def rotation_goal_deg(targets: dict[str, Any] | None) -> float:
    """The prescription's `rotation_deg` goal when it is a plain number in 10..180, else the default (80°)."""
    value = (targets or {}).get("rotation_deg")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and _GOAL_RANGE[0] <= value <= _GOAL_RANGE[1]:
        return float(value)
    return DEFAULT_ROTATION_GOAL_DEG
