"""Vision-to-event boundary for correctness-gated rep counting.

The vision layer supplies one primary angle and a deterministic form verdict for
each frame.  This module emits a normal Mira ``rep`` event only for a completed
cycle whose every observable frame was good.  A failed attempt is represented
as ``rep_attempt`` for analytics/feedback and never changes the LCD count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from server.agents.rep_counter import PROFILES, RepCounter

FormQuality = Literal["good", "fix_form"]


@dataclass
class _Cycle:
    counter: RepCounter
    displayed_reps: int = 0
    good: bool = True
    flags: set[str] = field(default_factory=set)


class CorrectRepPipeline:
    """Stateful, per-session/set gate between vision output and the existing LCD event contract.

    Call :meth:`observe` once per pose frame.  The caller owns landmark-to-angle
    and form-rule evaluation; this class owns temporal counting and makes it
    impossible for a poor-form completion to emit ``type='rep'``.
    """

    def __init__(self) -> None:
        self._cycles: dict[tuple[str, int | None, str], _Cycle] = {}

    def reset_session(self, session_id: str) -> None:
        """Forget counters for a finished or restarted session."""
        self._cycles = {key: value for key, value in self._cycles.items() if key[0] != session_id}

    def observe(self, *, session_id: str, set_no: int | None, exercise_id: str, angle: float | None,
                timestamp: float, confidence: float, form_quality: FormQuality, flags: list[str] | None = None
                ) -> list[dict[str, Any]]:
        """Return zero or one event for this frame.

        ``form_quality`` must be ``good`` only when all exercise-specific form
        rules pass.  Low-confidence frames also make an in-progress cycle
        ineligible, preventing an unverified rep from reaching the display.
        """
        if exercise_id not in PROFILES:
            raise ValueError(f"no rep-counting profile for {exercise_id!r}")
        if form_quality not in ("good", "fix_form"):
            raise ValueError(f"invalid form quality: {form_quality!r}")
        key = (session_id, set_no, exercise_id)
        cycle = self._cycles.setdefault(key, _Cycle(RepCounter(PROFILES[exercise_id])))
        was_in_cycle = cycle.counter.in_cycle
        completed = cycle.counter.observe(angle, timestamp, confidence)
        is_in_cycle = cycle.counter.in_cycle
        if was_in_cycle:
            if form_quality != "good" or confidence < cycle.counter.min_confidence:
                cycle.good = False
                cycle.flags.update(flags or [])
                if confidence < cycle.counter.min_confidence:
                    cycle.flags.add("low_pose_confidence")
        if completed is None:
            return []

        event_base = {
            "session_id": session_id, "set": set_no, "exercise_id": exercise_id,
            "started_at": completed.started_at, "ended_at": completed.ended_at,
            "peak_angle": completed.peak_angle, "flags": sorted(cycle.flags),
        }
        good, attempt_flags = cycle.good, sorted(cycle.flags)
        cycle.good, cycle.flags = True, set()
        if not good:
            return [{"type": "rep_attempt", "quality": "fix_form", "physical_index": completed.index,
                     "reason": "form_or_pose_confidence", **event_base, "flags": attempt_flags}]
        cycle.displayed_reps += 1
        return [{"type": "rep", "index": cycle.displayed_reps, "quality": "good", **event_base}]
