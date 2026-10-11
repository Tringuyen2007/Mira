"""3D from one calibrated camera: MediaPipe's world landmarks for shape, the floor for position and scale.

MediaPipe estimates a metric, hip-centred 3D pose for each person (world landmarks, camera-aligned
axes). Rotated into the mat frame with the camera's rotation it gives the body's shape and depth; the
floor fixes where it stands: rays through the visible feet meet the mat surface, and the body is moved so
its feet are there and its lowest point is on the floor. The world shape leans (mostly toward the
camera); the model tracker measures that lean once per person (upright_rotation) and it is taken out.
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
UPRIGHT_KNEE_DEG = 150.0  # roughly standing (MediaPipe's world-shape knees median ~155°)
UPRIGHT_MIN_FRAMES = 15  # at least this many standing frames before correcting the lean
UPRIGHT_MAX_DEG = 20.0  # never tilt the body more than this
UPRIGHT_HIP_DEG = 160.0  # trunk in line with the legs (shoulders, hips, knees): not bent over
UPRIGHT_SPREAD_DEG = 6.0  # standing frames whose lean differs by more than this (median) don't agree on one


def world_shape(pose: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """MediaPipe's 3D pose in the mat frame, relative to the hip midpoint (metres)."""
    if pose is None or not pose.detected or pose.world is None or cam is None or not cam.calibrated:
        return None
    hip = (pose.world[LEFT_HIP] + pose.world[RIGHT_HIP]) / 2
    return world_to_mat(pose.world - hip, cam.R)


def mono_joints(pose: PoseResult, cam: CameraConfig, scale: float = 1.0, last_xz=None,
                upright: np.ndarray | None = None) -> np.ndarray | None:
    """Full 33-joint body in the mat frame from one camera: the world-landmark shape (turned by `upright`
    about the hips, times scale), moved so the feet stand where their rays meet the floor (or, when the feet
    aren't visible, with the ankle midpoint at last_xz: pass the previous result's ankle midpoint (x, z) to
    hold it in place)."""
    body = world_shape(pose, cam)
    if body is None:
        return None
    if upright is not None:
        body = body @ np.asarray(upright).T
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


def _angle_deg(a: np.ndarray, at: np.ndarray, b: np.ndarray) -> float:
    """Angle (degrees) at point `at` between the directions to a and to b."""
    u, v = a - at, b - at
    with np.errstate(invalid="ignore"):
        c = u @ v / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def _knee_deg(J: np.ndarray, hip: int, knee: int, ankle: int) -> float:
    return _angle_deg(J[hip], J[knee], J[ankle])


def is_standing(J) -> bool:
    """Roughly standing straight: both knees above UPRIGHT_KNEE_DEG (MediaPipe's own knees rarely look
    fully straight) and not bent at the waist (shoulders, hips and knees in line within UPRIGHT_HIP_DEG).
    False when a needed joint is missing."""
    J = np.asarray(J, float)
    if not np.isfinite(J[[11, 12, 23, 24, 25, 26, 27, 28]]).all():
        return False
    if min(_knee_deg(J, 23, 25, 27), _knee_deg(J, 24, 26, 28)) <= UPRIGHT_KNEE_DEG:
        return False
    hip_mid = (J[23] + J[24]) / 2
    return _angle_deg((J[11] + J[12]) / 2, hip_mid, (J[25] + J[26]) / 2) > UPRIGHT_HIP_DEG


def upright_rotation(shapes, min_frames: int = UPRIGHT_MIN_FRAMES, max_deg: float = UPRIGHT_MAX_DEG
                     ) -> np.ndarray | None:
    """Rotation (tilt only, about a horizontal axis) that makes the median ankles-to-shoulders line of
    `shapes` (bodies in the mat frame) vertical, from frames where the person is roughly standing
    (is_standing). MediaPipe's world shape leans (mostly toward the camera); this takes the lean out. None
    with fewer than `min_frames` such frames, or when they disagree (median angle to their median
    up-direction above UPRIGHT_SPREAD_DEG); the tilt is capped at `max_deg` so one odd session can't tip
    the body over."""
    ups = []
    for J in shapes:
        J = np.asarray(J, float)
        if not is_standing(J):
            continue
        v = (J[11] + J[12]) / 2 - (J[27] + J[28]) / 2
        n = float(np.linalg.norm(v))
        if n > 1e-6:
            ups.append(v / n)
    if len(ups) < min_frames:
        return None
    u = np.median(ups, axis=0)
    u = u / np.linalg.norm(u)
    if np.median(np.degrees(np.arccos(np.clip(np.asarray(ups) @ u, -1, 1)))) > UPRIGHT_SPREAD_DEG:
        return None  # the standing frames don't agree on one lean
    axis = np.cross(u, [0.0, 1.0, 0.0])  # horizontal: no yaw
    s, c = float(np.linalg.norm(axis)), float(u[1])
    if s < 1e-9:
        return np.eye(3)
    angle = min(float(np.arctan2(s, c)), np.radians(max_deg))
    k = axis / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)


def stature(shape_rel: np.ndarray) -> float:
    """Height (m) of a hip-relative body from its eye height above its lowest foot point."""
    eyes = (shape_rel[EYES[0], 1] + shape_rel[EYES[1], 1]) / 2
    return float((eyes - np.min(shape_rel[FEET, 1])) / EYE_HEIGHT_RATIO)
