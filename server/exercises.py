"""The exercise catalog (MVP spec §9): one display name per exercise id, shared by the LCD caption, /me and the
phone's plan/progress screens so every surface says "Mini squats" the same way."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Exercise:
    exercise_id: str
    exercise_name: str
    metric_label: str   # the main metric, as the phone labels its big number
    unit: str
    target_key: str     # the prescriptions.targets key that holds the goal for the main metric


EXERCISES: dict[str, Exercise] = {e.exercise_id: e for e in (
    Exercise("squat", "Mini squats", "Knee bend", "°", "knee_deg"),
    Exercise("sit_to_stand", "Sit to stand", "Time to stand", "s", "time_s"),
    Exercise("single_leg_stance", "Single-leg stance", "Hold", "s", "hold_s"),
    Exercise("heel_raise", "Heel raises", "Heel height", "cm", "height_cm"),
    Exercise("glute_bridge", "Glute bridge", "Hip lift", "cm", "hold_s"),
    Exercise("bird_dog", "Bird-dog", "Trunk stability", "°", "hold_s"),
    Exercise("arm_raise", "Arm raise", "Shoulder angle", "°", "shoulder_deg"),
)}


def display_name(exercise_id: str) -> str:
    """Catalog name, or the id humanised ("heel_slide" → "Heel slide"). Never raises."""
    found = EXERCISES.get(exercise_id)
    return found.exercise_name if found else exercise_id.replace("_", " ").capitalize()


def describe(exercise_id: str) -> dict[str, str]:
    found = EXERCISES.get(exercise_id)
    if found is None:
        found = Exercise(exercise_id, display_name(exercise_id), "Progress", "", "")
    return asdict(found)
