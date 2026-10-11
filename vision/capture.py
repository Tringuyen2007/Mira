"""Webcam capture threads that keep only the latest frame."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

from vision.config import CameraConfig

BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
_MONO_TO_UNIX = time.time() - time.monotonic()


def mono_to_unix(t: float) -> float:
    return t + _MONO_TO_UNIX


class FpsMeter:
    def __init__(self, window_s: float = 1.0):
        self.window_s = window_s
        self._times: deque[float] = deque()

    def tick(self, t: float) -> None:
        self._times.append(t)
        while self._times and t - self._times[0] > self.window_s:
            self._times.popleft()

    @property
    def value(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0


def resolution_warning(cfg: CameraConfig, actual_size) -> str | None:
    if actual_size is None or tuple(actual_size) == (cfg.width, cfg.height):
        return None
    w, h = actual_size
    return f"camera {cfg.name} delivers {w}x{h} but is configured for {cfg.width}x{cfg.height}"


def camera_problem(cfg: CameraConfig, error: str | None, actual_size) -> str | None:
    """Why this camera can't be trusted for calibration or 3D right now, or None if it can."""
    if error:
        return error
    if actual_size is None:
        return f"camera {cfg.name} (index {cfg.index}) has not delivered a frame yet"
    return resolution_warning(cfg, actual_size)


READ_FAIL_S = 1.0  # reads failing for this long is an error; shorter gaps are a webcam warming up


class Camera(threading.Thread):
    def __init__(self, name: str, index: int, width: int = 1280, height: int = 720, fps: int = 30):
        super().__init__(daemon=True, name=f"camera-{name}")
        # threading.Thread owns `name`, so the camera label lives in cam_name
        self.cam_name, self.index, self.width, self.height, self.req_fps = name, index, width, height, fps
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._seq = 0
        self._halt = threading.Event()  # not _stop: threading.Thread uses that name internally
        self.meter = FpsMeter()
        self.opened = False
        self.error: str | None = None
        self.actual_size: tuple[int, int] | None = None
        self._failing_since: float | None = None

    def run(self) -> None:
        cap = cv2.VideoCapture(self.index, BACKEND)
        if not cap.isOpened():
            self.error = f"camera {self.cam_name} (index {self.index}) failed to open"
            return
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.req_fps)
        self.opened = True
        try:
            while not self._halt.is_set():
                ok, frame = cap.read()
                if not self._on_read(ok, frame, time.monotonic()):
                    time.sleep(0.01)
        finally:
            cap.release()
            self.opened = False

    def _on_read(self, ok: bool, frame, t: float) -> bool:
        if not ok:
            if self._failing_since is None:
                self._failing_since = t
            if t - self._failing_since >= READ_FAIL_S:
                self.error = f"camera {self.cam_name}: read failed"
            return False
        self._failing_since = None
        self.error = None
        with self._lock:
            self._frame, self._t = frame, t
            self._seq += 1
        self.meter.tick(t)
        self.actual_size = (frame.shape[1], frame.shape[0])
        return True

    def latest(self) -> tuple[int, float, np.ndarray] | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._seq, self._t, self._frame

    @property
    def fps(self) -> float:
        return self.meter.value

    def stop(self) -> None:
        self._halt.set()
