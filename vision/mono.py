"""3D from one calibrated camera: MediaPipe's world landmarks for shape, the floor for position and scale.

MediaPipe estimates a metric, hip-centred 3D pose for each person (world landmarks, camera-aligned
axes). Rotated into the mat frame with the camera's rotation it gives the body's shape and depth; the
floor fixes where it stands: rays through the visible feet meet the mat surface, and the body is moved so
its feet are there and its lowest point is on the floor.
"""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import world_to_mat
from vision.select import floor_feet
from vision.types import LEFT_HIP, RIGHT_HIP, PoseResult

FEET = [27, 28, 29, 30, 31, 32]
EYES = (2, 5)
EYE_HEIGHT_RATIO = 0.936  # eye height / stature


def world_shape(pose: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """MediaPipe's 3D pose in the mat frame, relative to the hip midpoint (metres)."""
    if pose is None or not pose.detected or pose.world is None or cam is None or not cam.calibrated:
        return None
    hip = (pose.world[LEFT_HIP] + pose.world[RIGHT_HIP]) / 2
    return world_to_mat(pose.world - hip, cam.R)


def mono_joints(pose: PoseResult, cam: CameraConfig, scale: float = 1.0, last_xz=None) -> np.ndarray | None:
    """Full 33-joint body in the mat frame from one camera: the world-landmark shape (times scale), moved so
    the feet stand where their rays meet the floor (or, when the feet aren't visible, with the ankle midpoint
    at last_xz: pass the previous result's ankle midpoint (x, z) to hold it in place)."""
    body = world_shape(pose, cam)
    if body is None:
        return None
    body = body * scale
    placed = floor_feet(pose, cam)
    if placed is None:  # holding: last_xz is the ankle midpoint of the last placed body
        if last_xz is None:
            return None
        feet_xz, used = np.asarray(last_xz, float), [27, 28]
    else:
        feet_xz, used = placed  # aligned by the same foot joints that placed it (one foot in a lunge)
    body_feet_xz = _side_mean(body, used)
    off_xz = feet_xz - body_feet_xz
    return body + np.array([off_xz[0], -np.min(body[FEET, 1]), off_xz[1]])


def _side_mean(body: np.ndarray, used: list[int]) -> np.ndarray:
    """(x, z) of the body's foot joints `used`, combined as floor_feet combines their floor hits: the median
    within each foot, the mean of the two feet."""
    sides = [[j for j in side if j in used] for side in ((27, 29), (28, 30))]
    return np.mean([np.median(body[side][:, [0, 2]], axis=0) for side in sides if side], axis=0)


def stature(shape_rel: np.ndarray) -> float:
    """Height (m) of a hip-relative body from its eye height above its lowest foot point."""
    eyes = (shape_rel[EYES[0], 1] + shape_rel[EYES[1], 1]) / 2
    return float((eyes - np.min(shape_rel[FEET, 1])) / EYE_HEIGHT_RATIO)
