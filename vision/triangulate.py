"""Two-view triangulation in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vision.config import CameraConfig
from vision.frames import cam_center, mat_to_cam, project
from vision.types import PoseResult


@dataclass
class Triangulation:
    points: np.ndarray  # (N, 3) mat frame, NaN where not accepted
    reproj_err: np.ndarray  # (N,) worst of the two views in px, NaN where not attempted
    angle_deg: np.ndarray  # (N,) angle between the two viewing rays
    ok: np.ndarray  # (N,) bool


def _undistort(uv: np.ndarray, cam: CameraConfig) -> np.ndarray:
    return cv2.undistortPoints(uv.reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K).reshape(-1, 2)


def _projection_matrix(cam: CameraConfig) -> np.ndarray:
    return cam.K @ np.hstack([cam.R, np.asarray(cam.t, float).reshape(3, 1)])


def triangulate_points(uv_a, uv_b, cam_a: CameraConfig, cam_b: CameraConfig):
    """Triangulate matching pixel observations. Returns (X, reprojection error, ray angle)."""
    uv_a = np.asarray(uv_a, float).reshape(-1, 2)
    uv_b = np.asarray(uv_b, float).reshape(-1, 2)
    n = len(uv_a)
    if n == 0:
        return np.empty((0, 3)), np.empty(0), np.empty(0)
    ua, ub = _undistort(uv_a, cam_a), _undistort(uv_b, cam_b)
    Xh = cv2.triangulatePoints(_projection_matrix(cam_a), _projection_matrix(cam_b), ua.T, ub.T)
    with np.errstate(invalid="ignore", divide="ignore"):
        X = (Xh[:3] / Xh[3]).T
        err_a = np.linalg.norm(project(X, cam_a.K, cam_a.dist, cam_a.R, cam_a.t) - uv_a, axis=1)
        err_b = np.linalg.norm(project(X, cam_b.K, cam_b.dist, cam_b.R, cam_b.t) - uv_b, axis=1)
        ra = X - cam_center(cam_a.R, cam_a.t)
        rb = X - cam_center(cam_b.R, cam_b.t)
        cos = np.sum(ra * rb, axis=1) / (np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1))
    return X, np.maximum(err_a, err_b), np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def triangulate_pair(
    a: PoseResult, b: PoseResult, cam_a: CameraConfig, cam_b: CameraConfig,
    min_vis: float = 0.5, max_reproj_px: float = 15.0,
) -> Triangulation:
    X, err, angle = triangulate_points(a.lm2d, b.lm2d, cam_a, cam_b)
    vis_ok = (a.vis >= min_vis) & (b.vis >= min_vis)
    with np.errstate(invalid="ignore"):
        in_front = (mat_to_cam(X, cam_a.R, cam_a.t)[:, 2] > 0) & (mat_to_cam(X, cam_b.R, cam_b.t)[:, 2] > 0)
        ok = vis_ok & in_front & np.isfinite(err) & (err <= max_reproj_px)
    return Triangulation(
        points=np.where(ok[:, None], X, np.nan),
        reproj_err=np.where(vis_ok, err, np.nan),
        angle_deg=np.where(vis_ok, angle, np.nan),
        ok=ok,
    )
