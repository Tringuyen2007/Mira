"""Staged COM fall signals; safety escalation belongs to the session/server."""
from __future__ import annotations

import numpy as np

from vision.presence import usable
from vision.types import FrameResult

FLOOR_PHASES = {"lying", "bridge", "bird_dog", "floor", "floor_exercise"}


class FallDetector:
    def __init__(self, drop_speed_m_s: float = -1.2, suspected_height_m: float = 0.5,
                 confirmed_height_m: float = 0.4, still_speed_m_s: float = 0.15,
                 still_s: float = 5.0, max_gap_s: float = 0.5):
        self.drop_speed_m_s = drop_speed_m_s
        self.suspected_height_m = suspected_height_m
        self.confirmed_height_m = confirmed_height_m
        self.still_speed_m_s = still_speed_m_s
        self.still_s = still_s
        self.max_gap_s = max_gap_s
        self.reset()

    def reset(self) -> None:
        self.stage: str | None = None
        self._last_t: float | None = None
        self._last_com: np.ndarray | None = None
        self._still_elapsed = 0.0

    def update(self, frame: FrameResult, floor_exercise: bool = False) -> list[dict]:
        t = float(frame.t)
        if self._last_t is not None and t <= self._last_t:
            raise ValueError("frames must have strictly increasing timestamps")
        prior_t, prior_com = self._last_t, self._last_com
        self._last_t = t
        if floor_exercise or frame.phase in FLOOR_PHASES:
            self.stage, self._last_com, self._still_elapsed = None, None, 0.0
            return []
        if (not usable(frame, (11, 12, 23, 24)) or frame.body != "ready"
                or frame.com is None or not np.isfinite(frame.com).all()):
            self._last_com = None
            self._still_elapsed = 0.0
            return []
        com = np.asarray(frame.com, dtype=float)
        self._last_com = com
        if prior_com is None or prior_t is None or t - prior_t > self.max_gap_s + 1e-9:
            self._still_elapsed = 0.0
            return []
        dt = t - prior_t
        velocity = (com - prior_com) / dt
        if self.stage is None:
            if velocity[1] < self.drop_speed_m_s and com[1] < self.suspected_height_m:
                self.stage = "suspected"
                self._still_elapsed = 0.0
                return [{"type": "fall", "stage": "suspected", "t": t}]
            return []
        if self.stage == "confirmed":
            return []
        if com[1] >= self.suspected_height_m:
            self.stage, self._still_elapsed = None, 0.0
            return []
        if com[1] < self.confirmed_height_m and np.linalg.norm(velocity) < self.still_speed_m_s:
            self._still_elapsed += dt
            if self._still_elapsed + 1e-9 >= self.still_s:
                self.stage = "confirmed"
                return [{"type": "fall", "stage": "confirmed", "t": t}]
        else:
            self._still_elapsed = 0.0
        return []
