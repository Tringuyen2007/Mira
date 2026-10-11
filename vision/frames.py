"""Coordinate conventions.

Mat frame: right-handed, origin mat centre on the mat surface, +y up, +z toward
Mira, +x toward the patient's left (Mira's right). Extrinsics map mat -> camera:
X_cam = R @ X_mat + t, with OpenCV camera axes (x right, y down, z forward).
MediaPipe world landmarks use camera-aligned axes too, so R.T maps them into the
mat frame.
"""
from __future__ import annotations

import cv2
import numpy as np


def mat_to_cam(X, R, t) -> np.ndarray:
    return np.asarray(X, float) @ np.asarray(R, float).T + np.ravel(t)


def cam_center(R, t) -> np.ndarray:
    return -np.asarray(R, float).T @ np.ravel(t)


def project(X, K, dist, R, t) -> np.ndarray:
    """Project mat-frame points to distorted pixels. Non-finite rows stay NaN."""
    X = np.asarray(X, float).reshape(-1, 3)
    out = np.full((len(X), 2), np.nan)
    ok = np.isfinite(X).all(axis=1)
    if ok.any():
        rvec, _ = cv2.Rodrigues(np.asarray(R, float))
        d = np.zeros(5) if dist is None else np.asarray(dist, float)
        px, _ = cv2.projectPoints(X[ok], rvec, np.asarray(t, float).reshape(3, 1), np.asarray(K, float), d)
        out[ok] = px.reshape(-1, 2)
    return out


def look_at_pose(eye, target, up=(0.0, 1.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """Extrinsics (R, t) of a camera at `eye` looking at `target`, image up = world up."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, np.asarray(up, float))
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return R, -R @ eye


def world_to_mat(world, R) -> np.ndarray:
    """Rotate camera-aligned offsets (MediaPipe world landmarks) into the mat frame."""
    return np.asarray(world, float) @ np.asarray(R, float)


def uncalibrated_world_to_mat(world) -> np.ndarray:
    """Without extrinsics: flip MediaPipe's y-down/z-away axes to y-up (a 180° turn about x)."""
    return np.asarray(world, float) * np.array([1.0, -1.0, -1.0])


def rotation_angle_deg(R1, R2) -> float:
    c = (np.trace(np.asarray(R1).T @ np.asarray(R2)) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def angle_between_deg(u, v) -> float:
    u, v = np.asarray(u, float), np.asarray(v, float)
    c = u @ v / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))
