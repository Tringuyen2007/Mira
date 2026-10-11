"""Timed mat presence from trustworthy FrameResult poses.

Call update once per frame in timestamp order. Events are dictionaries with ``type``
and ``t`` (the FrameResult Unix timestamp). The caller owns session state; pass
``in_set=True`` only while a set is running. ``on_mat`` is the current trusted
observation, or None when the observation cannot be trusted.
"""
from __future__ import annotations

import numpy as np

from vision.config import MatConfig
from vision.types import FrameResult

FLOOR_PHASES = {"lying", "bridge", "bird_dog", "floor", "floor_exercise"}


def usable(frame: FrameResult, joints: tuple[int, ...], min_conf: float = 0.5) -> bool:
    """A fresh, finite pose with enough evidence at every requested landmark."""
    if frame.body != "ready" or frame.tracking == "lost" or any(
            flag in frame.flags for flag in ("pose_held", "camera_moved")):
        return False
    j, c = np.asarray(frame.joints), np.asarray(frame.conf)
    return (j.shape == (33, 3) and c.shape == (33,)
            and np.isfinite(j[list(joints)]).all()
            and np.isfinite(c[list(joints)]).all()
            and bool(np.all(c[list(joints)] >= min_conf)))


class PresenceDetector:
    def __init__(self, mat: MatConfig | None = None, margin_m: float = 0.03,
                 arrival_s: float = 1.5, off_mat_s: float = 2.0,
                 facing_deg: float = 35.0, max_gap_s: float = 0.5):
        self.mat = mat or MatConfig()
        self.margin_m, self.arrival_s, self.off_mat_s = margin_m, arrival_s, off_mat_s
        self.facing_deg, self.max_gap_s = facing_deg, max_gap_s
        self.reset()

    def reset(self) -> None:
        self.on_mat: bool | None = None
        self.arrived = False
        self.paused_off_mat = False
        self._arrival_elapsed = self._off_elapsed = 0.0
        self._last_t: float | None = None
        self._last_good = False
        self._last_arrival_match = False
        self._last_off_match = False

    def update(self, frame: FrameResult, in_set: bool = False,
               floor_exercise: bool = False) -> list[dict]:
        t = float(frame.t)
        if self._last_t is not None and t <= self._last_t:
            raise ValueError("frames must have strictly increasing timestamps")
        dt = t - self._last_t if self._last_t is not None and self._last_good and t - self._last_t <= self.max_gap_s + 1e-9 else 0.0
        self._last_t = t
        self._last_good = False
        if not usable(frame, (27, 28, 29, 30, 31, 32)):
            self.on_mat = None
            self._arrival_elapsed = self._off_elapsed = 0.0
            self._last_arrival_match = self._last_off_match = False
            return []
        self._last_good = True
        feet = np.asarray(frame.joints)[[27, 28, 29, 30, 31, 32]]
        xlim = self.mat.width_m / 2 - self.margin_m
        zlim = self.mat.length_m / 2 - self.margin_m
        self.on_mat = bool(xlim > 0 and zlim > 0 and np.all(np.abs(feet[:, 0]) <= xlim)
                           and np.all(np.abs(feet[:, 2]) <= zlim))
        if floor_exercise or frame.phase in FLOOR_PHASES:
            self._arrival_elapsed = self._off_elapsed = 0.0
            self._last_arrival_match = self._last_off_match = False
            return []
        events = []
        # Person-left shoulder minus right shoulder is +x when facing Mira.
        facing = False
        if usable(frame, (11, 12)):
            across = np.asarray(frame.joints)[11] - np.asarray(frame.joints)[12]
            facing = bool(across[0] > 0 and
                          np.degrees(np.arctan2(abs(across[2]), across[0])) <= self.facing_deg)
        if self.on_mat and facing and not self.arrived:
            self._arrival_elapsed += dt if self._last_arrival_match else 0.0
            if self._arrival_elapsed + 1e-9 >= self.arrival_s:
                self.arrived = True
                events.append({"type": "arrived", "t": t})
        else:
            self._arrival_elapsed = 0.0
        if self.on_mat:
            self._off_elapsed = 0.0
            self.paused_off_mat = False
        elif in_set and not self.paused_off_mat:
            self._off_elapsed += dt if self._last_off_match else 0.0
            if self._off_elapsed + 1e-9 >= self.off_mat_s:
                self.paused_off_mat = True
                events.append({"type": "paused", "reason": "off_mat", "t": t})
        else:
            self._off_elapsed = 0.0
        if not in_set:
            self._off_elapsed = 0.0
        self._last_arrival_match = self.on_mat and facing
        self._last_off_match = not self.on_mat and in_set
        return events
