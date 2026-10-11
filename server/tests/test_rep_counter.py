from __future__ import annotations

import pytest

from server.agents.rep_counter import PROFILES, RepCounter


@pytest.mark.parametrize("exercise_id, signal", [
    ("squat", [8, 22, 40, 61, 72, 48, 24, 10]),
    ("lunge", [10, 24, 45, 64, 76, 52, 25, 8]),
    ("straight_leg_raise", [5, 18, 30, 56, 70, 43, 19, 5]),
])
def test_counts_one_complete_cycle(exercise_id: str, signal: list[float]) -> None:
    counter = RepCounter(PROFILES[exercise_id])
    reps = [counter.observe(value, index * 0.2) for index, value in enumerate(signal)]
    completed = [rep for rep in reps if rep]
    assert len(completed) == 1
    assert completed[0].index == 1 and completed[0].exercise_id == exercise_id


def test_does_not_count_partial_or_low_confidence_motion() -> None:
    counter = RepCounter(PROFILES["squat"])
    for index, value in enumerate([10, 35, 59, 40, 26]):
        assert counter.observe(value, index * 0.2) is None
    for index, value in enumerate([10, 35, 60, 10]):
        assert counter.observe(value, 2 + index * 0.2, confidence=0.1) is None
    assert counter.count == 0
