"""Pose-signal rep counter used by the vision service before it emits a ``rep`` event.

This is deliberately deterministic rather than an LLM decision.  The Coach may
use its evaluation records, but never decides whether a patient completed a
rep.  The counter needs one scalar flexion signal per frame: the maximum knee
flexion for squats, the front-knee flexion for lunges, and hip flexion for a
straight-leg raise.  Angles follow Mira's 0° = straight convention.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepProfile:
    exercise_id: str
    signal: str
    ready_at_or_below: float
    peak_at_or_above: float
    min_cycle_s: float


PROFILES: dict[str, RepProfile] = {
    "squat": RepProfile("squat", "max(knee_l, knee_r)", 25.0, 55.0, 0.60),
    "lunge": RepProfile("lunge", "front_knee_flexion", 25.0, 60.0, 0.70),
    "straight_leg_raise": RepProfile("straight_leg_raise", "raised_hip_flexion", 20.0, 55.0, 0.70),
}


@dataclass(frozen=True)
class CountedRep:
    index: int
    exercise_id: str
    started_at: float
    ended_at: float
    peak_angle: float


class RepCounter:
    """Hysteretic low → peak → low rep counter.

    A cycle begins only after a stable extended/resting position, which avoids
    counting a person who enters the camera already at the bottom of a motion.
    Low-confidence frames are ignored by the caller-facing API.
    """

    def __init__(self, profile: RepProfile, *, min_confidence: float = 0.60) -> None:
        self.profile = profile
        self.min_confidence = min_confidence
        self.count = 0
        self._state = "await_ready"
        self._started_at: float | None = None
        self._peak = 0.0

    @property
    def in_cycle(self) -> bool:
        """Whether a movement cycle is under way (used by the form-quality gate)."""
        return self._state != "await_ready"

    def observe(self, angle: float | None, timestamp: float, confidence: float = 1.0) -> CountedRep | None:
        """Accept one pose-derived signal sample and return a completed rep once, if any."""
        if angle is None or confidence < self.min_confidence:
            return None
        if self._state == "await_ready":
            if angle <= self.profile.ready_at_or_below:
                self._state = "await_peak"
                self._started_at = timestamp
                self._peak = angle
            return None
        if self._state == "await_peak":
            self._peak = max(self._peak, angle)
            if angle >= self.profile.peak_at_or_above:
                self._state = "await_return"
            return None

        # A full range of motion is present; count only after returning home.
        self._peak = max(self._peak, angle)
        if angle > self.profile.ready_at_or_below:
            return None
        started_at = self._started_at if self._started_at is not None else timestamp
        if timestamp - started_at < self.profile.min_cycle_s:
            return None
        self.count += 1
        completed = CountedRep(self.count, self.profile.exercise_id, started_at, timestamp, self._peak)
        # Require a fresh resting sample before beginning the next cycle.  This
        # keeps the return frame from also becoming the start of another rep.
        self._state = "await_ready"
        self._started_at = None
        self._peak = 0.0
        return completed
