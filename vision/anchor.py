"""Foot anchoring: pin a planted foot so MediaPipe's wobble doesn't make it jitter.

A foot (ankle, heel, toe) is planted when its heel and toe are near the floor and it has barely
moved for a moment; it is then held at its average position over that moment (no vertical snap, so
a floor error stays visible), drifting slowly toward small real shifts (a soft anchor: jitter is removed,
weight shifts still show). It is released by a real step or lift (the raw foot moves away from the anchor, or the
heel leaves the floor, for a couple of frames) and blends back to the live position so it never jumps.
The rest of the body is untouched: a squat with planted feet keeps the feet locked while hips drop.
"""
from __future__ import annotations

from collections import deque

import numpy as np

FEET = {"l": (27, 29, 31), "r": (28, 30, 32)}  # ankle, heel, toe (foot index)
CONTACT_HEIGHT_M = 0.12  # heel and toe below this: touching the floor
STILL_SPEED_MPS = 0.15  # slower than this over STILL_S: not moving
STILL_S = 0.2
RELEASE_M = 0.03  # raw foot this far from the anchor ...
RELEASE_FRAMES = 2  # ... for this many frames in a row: a real step
BLEND_S = 0.1
FOLLOW_PER_S = 2.0  # a planted foot drifts toward the detections at this rate (soft anchor: ~0.5 s to settle)


class _Foot:
    def __init__(self):
        self.history: deque[tuple[float, np.ndarray]] = deque()
        self.anchor: np.ndarray | None = None  # held output: the average position while planting
        self.anchor_raw: np.ndarray | None = None  # what the detections said when it planted
        self.away = 0
        self.blend: tuple[float, np.ndarray] | None = None  # (release time, held - live offset) fading out
        self.last_t: float | None = None

    def reset(self):
        self.__init__()


class FootAnchor:
    def __init__(self):
        self._feet = {side: _Foot() for side in FEET}

    def reset(self) -> None:
        for f in self._feet.values():
            f.reset()

    def update(self, t: float, joints: np.ndarray) -> tuple[np.ndarray, dict[str, bool]]:
        """Returns (joints with planted feet held, side -> locked)."""
        out = np.array(joints, float, copy=True)
        locked = {}
        for side, idx in FEET.items():
            foot = self._feet[side]
            pts = out[list(idx)]
            if not np.isfinite(pts).all():
                foot.reset()
                locked[side] = False
                continue
            locked[side] = self._step(foot, t, pts)
            if foot.anchor is not None:
                out[list(idx)] = foot.anchor
            elif foot.blend is not None:
                t0, offset = foot.blend
                a = (t - t0) / BLEND_S
                if a >= 1:
                    foot.blend = None
                else:  # follow the live foot, with the gap from the held spot fading out
                    out[list(idx)] = pts + (1 - a) * offset
        return out, locked

    @staticmethod
    def _step(foot: _Foot, t: float, pts: np.ndarray) -> bool:
        on_floor = pts[1, 1] <= CONTACT_HEIGHT_M and pts[2, 1] <= CONTACT_HEIGHT_M
        dt = (t - foot.last_t) if foot.last_t is not None else 0.0
        foot.last_t = t
        if foot.anchor is not None:
            moved = float(np.max(np.linalg.norm(pts - foot.anchor_raw, axis=1))) > RELEASE_M
            foot.away = foot.away + 1 if (moved or not on_floor) else 0
            if foot.away == 0:  # soft anchor: drift toward small real shifts; jumps (junk or a step) aren't followed
                foot.anchor_raw = foot.anchor_raw + (1 - np.exp(-FOLLOW_PER_S * max(dt, 0.0))) * (pts - foot.anchor_raw)
                foot.anchor = foot.anchor_raw.copy()
            if foot.away < RELEASE_FRAMES:
                return True
            foot.blend = (t, foot.anchor - pts)
            foot.anchor = foot.anchor_raw = None
            foot.away = 0
            foot.history.clear()
            return False
        foot.history.append((t, pts.copy()))
        while len(foot.history) > 1 and t - foot.history[1][0] >= STILL_S:
            foot.history.popleft()
        t_first, first = foot.history[0]
        span = t - t_first
        if not on_floor or span < STILL_S:
            return False
        window = np.stack([p for _, p in foot.history])
        speed = float(np.max(np.linalg.norm(window.mean(axis=0) - first, axis=1))) / span
        if speed >= STILL_SPEED_MPS:
            return False
        foot.anchor_raw = window.mean(axis=0)
        foot.anchor = foot.anchor_raw.copy()
        foot.blend = None
        return True
