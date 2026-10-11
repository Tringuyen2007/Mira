"""Per-joint fallbacks after triangulation and the `tracking` label."""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import uncalibrated_world_to_mat, world_to_mat
from vision.triangulate import Triangulation
from vision.types import LEFT_HIP, N_JOINTS, RIGHT_HIP, PoseResult

HELD_CONF = 0.3  # held joints: below the usual 0.5 gate so consumers can treat them as unreliable
FALLBACK_MIN_VIS = 0.5
ANCHOR_HOLD_S = 2.0
FOOT_JOINTS = [27, 28, 29, 30, 31, 32]


def _hip_mid(points: np.ndarray) -> np.ndarray | None:
    h = (points[LEFT_HIP] + points[RIGHT_HIP]) / 2
    return h if np.isfinite(h).all() else None


class Cleaner:
    def __init__(self, cams: dict[str, CameraConfig], hold_s: float = 0.3):
        self.cams = cams
        self.hold_s = hold_s
        self._last = np.full((N_JOINTS, 3), np.nan)
        self._last_t = np.full(N_JOINTS, -np.inf)
        self._hip: np.ndarray | None = None
        self._hip_t = -np.inf

    def update(self, t: float, tri: Triangulation | None, poses: dict[str, PoseResult | None]):
        seen = {n: p for n, p in poses.items() if p is not None and p.detected}
        if tri is not None and len(seen) >= 2:
            joints, conf = self._measure_3d(tri, seen)
            tracking = "3d"
            hip = _hip_mid(joints)
            if hip is not None:
                self._hip, self._hip_t = hip, t
        elif seen:
            name = max(seen, key=lambda n: float(np.mean(seen[n].vis)))
            joints, conf = self._measure_single(t, seen[name])
            tracking = f"single_cam_{name}"
        else:
            return np.full((N_JOINTS, 3), np.nan), np.zeros(N_JOINTS), "lost"

        measured = np.isfinite(joints).all(axis=1)
        self._last[measured] = joints[measured]
        self._last_t[measured] = t
        hold = ~measured & (t - self._last_t <= self.hold_s)
        joints[hold] = self._last[hold]
        conf[hold] = HELD_CONF
        return joints, conf, tracking

    def _measure_3d(self, tri: Triangulation, seen: dict[str, PoseResult]):
        a, b = seen["A"], seen["B"]
        joints = tri.points.copy()
        conf = np.where(tri.ok, np.minimum(a.vis, b.vis), 0.0)
        hip = _hip_mid(joints)
        if hip is None:
            return joints, conf
        views = [p for p in (a, b) if p.world is not None]
        for j in np.flatnonzero(~tri.ok):
            if not views:
                break
            best = max(views, key=lambda p: p.vis[j])
            if best.vis[j] < FALLBACK_MIN_VIS:
                continue
            offset = world_to_mat(best.world[j] - _hip_mid(best.world), self.cams[best.cam].R)
            joints[j] = hip + offset
            conf[j] = best.vis[j]
        return joints, conf

    def _measure_single(self, t: float, p: PoseResult):
        if p.world is None:
            return np.full((N_JOINTS, 3), np.nan), np.zeros(N_JOINTS)
        rel = p.world - _hip_mid(p.world)
        cam = self.cams.get(p.cam)
        body = world_to_mat(rel, cam.R) if cam is not None and cam.calibrated else uncalibrated_world_to_mat(rel)
        if self._hip is not None and t - self._hip_t <= ANCHOR_HOLD_S:
            ax, az = self._hip[0], self._hip[2]
        else:
            ax, az = 0.0, 0.0
        feet_y = np.min(body[FOOT_JOINTS, 1])
        return body + np.array([ax, -feet_y, az]), p.vis.copy()
