"""Live cameras -> paired PoseResults."""
from __future__ import annotations

import queue

import numpy as np

from vision.capture import Camera, resolution_warning
from vision.config import CameraConfig
from vision.pairing import Pair, Pairer, SkewStats
from vision.pose import PoseWorker, model_path
from vision.types import PoseResult


class LiveSource:
    def __init__(self, cams: dict[str, CameraConfig], model: str = "full"):
        if not cams:
            raise SystemExit("No cameras configured: run python -m vision.cams first")
        model_path(model)  # fail fast if the model is missing
        self.cams = cams
        self._queue: queue.Queue = queue.Queue()
        self.cameras = {n: Camera(n, c.index, c.width, c.height) for n, c in cams.items()}
        self.workers = {n: PoseWorker(cam, self._queue, model) for n, cam in self.cameras.items()}
        names = sorted(cams)
        self.pairer = Pairer(anchor=names[0], other=names[1] if len(names) > 1 else None)
        self.skew = SkewStats()

    def start(self) -> "LiveSource":
        for c in self.cameras.values():
            c.start()
        for w in self.workers.values():
            w.start()
        return self

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()
        for c in self.cameras.values():
            c.stop()
        for t in [*self.workers.values(), *self.cameras.values()]:
            if t.is_alive():
                t.join(timeout=1.0)

    def __enter__(self) -> "LiveSource":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def poll(self, timeout: float = 0.01) -> list[Pair]:
        out: list[Pair] = []
        try:
            r = self._queue.get(timeout=timeout)
        except queue.Empty:
            return out
        while True:
            for pair in self.pairer.add(r):
                self.skew.add(pair)
                out.append(pair)
            try:
                r = self._queue.get_nowait()
            except queue.Empty:
                return out

    def latest_frames(self) -> dict[str, tuple[np.ndarray, PoseResult] | None]:
        return {n: w.latest for n, w in self.workers.items()}

    def stats(self) -> dict[str, dict]:
        return {
            n: {
                "fps": cam.fps,
                "pose_ms": self.workers[n].ms,
                "error": cam.error or self.workers[n].error,
                "warning": resolution_warning(self.cams[n], cam.actual_size),
            }
            for n, cam in self.cameras.items()
        }
