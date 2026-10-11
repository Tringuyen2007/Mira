"""Camera-moved alarm: compare live marker detections with the ones saved at calibration.

Statuses: "ok", "moved" (markers shifted > threshold on `confirm` checks in a row), "blind" (no saved
marker seen for `blind_s`, e.g. someone standing over them), "unknown" (never calibrated with markers).
"""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig


class MoveMonitor:
    def __init__(self, cams: dict[str, CameraConfig], detector=None, interval_s: float = 1.0,
                 threshold_px: float = 3.0, confirm: int = 3, blind_s: float = 10.0):
        self.detector = detector
        self.interval_s, self.threshold_px, self.confirm, self.blind_s = interval_s, threshold_px, confirm, blind_s
        self._ref = {n: c.marker_obs for n, c in cams.items() if c.marker_obs}
        self._state = {n: "ok" if n in self._ref else "unknown" for n in cams}
        self._shift: dict[str, float | None] = {n: None for n in cams}
        self._last_check: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        self._above = {n: 0 for n in cams}
        self._below = {n: 0 for n in cams}

    def update(self, name: str, image, t: float) -> str:
        """Detect markers in `image` at most once per interval and update the camera's status."""
        if name not in self._ref or t - self._last_check.get(name, -np.inf) < self.interval_s:
            return self.status(name)
        self._last_check[name] = t
        return self.observe(name, self.detector.detect(image), t)

    def observe(self, name: str, detections: dict[int, np.ndarray], t: float) -> str:
        ref = self._ref.get(name)
        if ref is None:
            return self.status(name)
        self._last_seen.setdefault(name, t)
        common = sorted(set(ref) & set(detections))
        if not common:
            if t - self._last_seen[name] >= self.blind_s and self._state[name] == "ok":
                self._state[name] = "blind"
            return self._state[name]
        self._last_seen[name] = t
        shift = float(np.median([np.median(np.linalg.norm(detections[i] - ref[i], axis=1)) for i in common]))
        self._shift[name] = shift
        if shift > self.threshold_px:
            self._above[name] += 1
            self._below[name] = 0
            if self._above[name] >= self.confirm:
                self._state[name] = "moved"
        else:
            self._below[name] += 1
            self._above[name] = 0
            if self._state[name] == "blind" or (self._state[name] == "moved" and self._below[name] >= self.confirm):
                self._state[name] = "ok"
        return self._state[name]

    def status(self, name: str) -> str:
        return self._state.get(name, "unknown")

    def shift_px(self, name: str) -> float | None:
        return self._shift.get(name)

    def message(self, name: str) -> str | None:
        """Banner text for the live tools, or None when there is nothing to say."""
        s = self.status(name)
        if s == "moved":
            return f"Camera {name} has moved ({self.shift_px(name):.0f} px): re-run calib_extrinsics"
        if s == "blind":
            return f"can't check camera {name} (markers hidden)"
        return None
