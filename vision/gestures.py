"""Held raised-hand and crossed-arms gestures from fresh mat-frame poses."""
from __future__ import annotations

import numpy as np

from vision.presence import usable
from vision.types import FrameResult

FLOOR_PHASES = {"lying", "bridge", "bird_dog", "floor", "floor_exercise"}


class GestureDetector:
    def __init__(self, hold_s: float = 1.0, max_gap_s: float = 0.5):
        self.hold_s, self.max_gap_s = hold_s, max_gap_s
        self.reset()

    def reset(self) -> None:
        self._elapsed = {"raised_hand": 0.0, "crossed_arms": 0.0}
        self._active = {"raised_hand": False, "crossed_arms": False}
        self._last_t: float | None = None
        self._last_valid = False
        self._last_match = {"raised_hand": False, "crossed_arms": False}

    def update(self, frame: FrameResult, mid_rep: bool = False,
               floor_exercise: bool = False) -> list[dict]:
        t = float(frame.t)
        if self._last_t is not None and t <= self._last_t:
            raise ValueError("frames must have strictly increasing timestamps")
        dt = t - self._last_t if self._last_t is not None and self._last_valid and t - self._last_t <= self.max_gap_s + 1e-9 else 0.0
        self._last_t = t
        self._last_valid = False
        required = (0, 11, 12, 13, 14, 15, 16, 23, 24)
        if (floor_exercise or frame.phase in FLOOR_PHASES or
                not usable(frame, required) or frame.body != "ready"):
            self._elapsed = dict.fromkeys(self._elapsed, 0.0)
            return []
        self._last_valid = True
        J = np.asarray(frame.joints)
        head_y = max(J[0, 1], J[7, 1] if np.isfinite(J[7]).all() else J[0, 1],
                     J[8, 1] if np.isfinite(J[8]).all() else J[0, 1])
        raised = bool(max(J[15, 1], J[16, 1]) > head_y + 0.10)
        centre = float((J[11, 0] + J[12, 0]) / 2)
        chest_top = float(min(J[11, 1], J[12, 1]))
        chest_bottom = float(max(J[23, 1], J[24, 1]))
        # Wrist on the other side of the torso, and the two forearm segments
        # reverse order in the frontal (x, y) projection.
        crossed = bool(J[15, 0] < centre - 0.02 and J[16, 0] > centre + 0.02
                       and chest_bottom <= J[15, 1] <= chest_top
                       and chest_bottom <= J[16, 1] <= chest_top
                       and J[13, 0] > J[14, 0])
        matches = {"raised_hand": raised and not mid_rep, "crossed_arms": crossed}
        events = []
        for name, match in matches.items():
            if match:
                self._elapsed[name] += dt if self._last_match[name] else 0.0
                if self._elapsed[name] + 1e-9 >= self.hold_s and not self._active[name]:
                    self._active[name] = True
                    events.append({"type": "paused", "reason": "stop_gesture", "t": t} if name == "crossed_arms"
                                  else {"type": "gesture", "name": name, "t": t})
            else:
                self._elapsed[name] = 0.0
                self._active[name] = False
            self._last_match[name] = match
        return events
