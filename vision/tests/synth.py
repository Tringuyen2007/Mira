"""Synthetic cameras and skeletons for tests."""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import look_at_pose, project
from vision.types import PoseResult

K_DEFAULT = np.array([[900.0, 0, 640], [0, 900, 360], [0, 0, 1]])

# Person standing at the mat centre facing Mira (+z); +x is their left.
_LEFT = {
    1: (0.02, 1.64, 0.08), 2: (0.035, 1.645, 0.075), 3: (0.05, 1.64, 0.07),
    7: (0.075, 1.62, 0.0), 9: (0.02, 1.55, 0.08),
    11: (0.18, 1.42, 0.0), 13: (0.22, 1.14, 0.0), 15: (0.24, 0.88, 0.02),
    17: (0.25, 0.80, 0.03), 19: (0.24, 0.79, 0.05), 21: (0.23, 0.82, 0.05),
    23: (0.10, 0.95, 0.0), 25: (0.11, 0.52, 0.02), 27: (0.11, 0.09, 0.0),
    29: (0.11, 0.04, -0.05), 31: (0.12, 0.02, 0.15),
}
_MIRROR = {1: 4, 2: 5, 3: 6, 7: 8, 9: 10, **{k: k + 1 for k in range(11, 32, 2)}}


def standing_skeleton() -> np.ndarray:
    X = np.zeros((33, 3))
    X[0] = (0.0, 1.60, 0.10)
    for left, p in _LEFT.items():
        X[left] = p
        X[_MIRROR[left]] = (-p[0], p[1], p[2])
    return X


def swap_left_right(arr: np.ndarray) -> np.ndarray:
    out = np.array(arr, copy=True)
    for left, right in _MIRROR.items():
        out[left], out[right] = arr[right], arr[left]
    return out


def make_cam(name, eye, target=(0.0, 0.8, 0.0), dist=None, K=None) -> CameraConfig:
    R, t = look_at_pose(np.asarray(eye, float), np.asarray(target, float))
    return CameraConfig(
        name=name, index=0,
        K=(K_DEFAULT if K is None else np.asarray(K, float)).copy(),
        dist=np.zeros(5) if dist is None else np.asarray(dist, float),
        R=R, t=t,
    )


def target_cams(dist=None) -> dict[str, CameraConfig]:
    return {"A": make_cam("A", (1.98, 0.85, 1.98), dist=dist), "B": make_cam("B", (-1.98, 0.85, 1.98), dist=dist)}


def pose_from_points(cam, X, t=0.0, vis=1.0, noise_px=0.0, rng=None) -> PoseResult:
    X = np.asarray(X, float)
    uv = project(X, cam.K, cam.dist, cam.R, cam.t)
    if noise_px:
        uv = uv + (rng or np.random.default_rng(0)).normal(0, noise_px, uv.shape)
    hip = (X[23] + X[24]) / 2
    world = (X - hip) @ cam.R.T
    v = np.full(len(X), float(vis)) if np.isscalar(vis) else np.asarray(vis, float)
    return PoseResult(cam=cam.name, t=t, lm2d=uv, vis=v, world=world)
