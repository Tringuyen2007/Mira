"""MediaPipe Pose Landmarker per camera."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from vision.roi import crop, uncrop
from vision.types import CameraPeople, PoseResult

MODEL_DIR = Path(__file__).resolve().parent / "models"
NUM_POSES = 3  # people per camera; the PersonSelector keeps the one at the mat


def model_path(kind: str = "full") -> Path:
    p = MODEL_DIR / f"pose_landmarker_{kind}.task"
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing: run python -m vision.models.download")
    return p


class PoseEstimator:
    """VIDEO-mode landmarker for one camera (timestamps must increase)."""

    def __init__(self, kind: str = "full", num_poses: int = NUM_POSES):
        options = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path(kind))),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=num_poses,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
        self._last_ms = -1

    def detect(self, frame_bgr: np.ndarray, t_mono: float, cam: str, roi=None) -> list[PoseResult]:
        """Everyone found (up to num_poses), in full-frame pixels. Only `roi` of the frame is searched."""
        ts = max(int(t_mono * 1000), self._last_ms + 1)
        self._last_ms = ts
        sub, offset = crop(frame_bgr, roi)
        rgb = cv2.cvtColor(np.ascontiguousarray(sub), cv2.COLOR_BGR2RGB)
        res = self._landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        h, w = sub.shape[:2]
        people = []
        for k, lms in enumerate(res.pose_landmarks or []):
            lm2d = uncrop(np.array([[p.x * w, p.y * h] for p in lms]), offset)
            vis = np.array([p.visibility for p in lms], dtype=float)
            wl = res.pose_world_landmarks
            world = np.array([[p.x, p.y, p.z] for p in wl[k]]) if wl and k < len(wl) else None
            people.append(PoseResult(cam=cam, t=t_mono, lm2d=lm2d, vis=vis, world=world))
        return people

    def close(self) -> None:
        self._landmarker.close()


class PoseWorker(threading.Thread):
    """Runs pose on each new frame from one Camera and puts CameraPeople on a shared queue."""

    def __init__(self, camera, out: queue.Queue, model: str = "full", roi=None, num_poses: int = NUM_POSES):
        super().__init__(daemon=True, name=f"pose-{camera.cam_name}")
        self.camera, self.out, self.model, self.roi, self.num_poses = camera, out, model, roi, num_poses
        self._halt = threading.Event()  # not _stop: threading.Thread uses that name internally
        self.latest: tuple[np.ndarray, CameraPeople] | None = None
        self.ms = 0.0
        self.error: str | None = None

    def run(self) -> None:
        try:
            est = PoseEstimator(self.model, self.num_poses)
        except Exception as e:  # surfaced in the viewer HUD
            self.error = f"pose {self.camera.cam_name}: {e}"
            return
        last_seq = -1
        try:
            while not self._halt.is_set():
                item = self.camera.latest()
                if item is None or item[0] == last_seq:
                    time.sleep(0.002)
                    continue
                last_seq, t, frame = item
                t0 = time.perf_counter()
                r = CameraPeople(self.camera.cam_name, t, est.detect(frame, t, self.camera.cam_name, self.roi))
                dt = (time.perf_counter() - t0) * 1000
                self.ms = dt if self.ms == 0 else 0.9 * self.ms + 0.1 * dt
                self.latest = (frame, r)
                self.out.put(r)
        finally:
            est.close()

    def stop(self) -> None:
        self._halt.set()
