import cv2
import numpy as np

from vision.config import MatConfig
from vision.frames import cam_center, look_at_pose, project, rotation_angle_deg
from vision.markers import (
    MarkerDetector, aruco_dictionary, average_detections, camera_warnings, placement_hint, solve_camera_pose,
)
from vision.tests.synth import make_cam

MAT = MatConfig()
AIM = np.array([0.0, 0.8, 0.0])
TARGET_A = np.array([1.98, 0.85, 1.98])


def _obs(cam, ids=(0, 1, 2, 3)):
    corners = MAT.marker_corners()
    return {i: project(corners[i], cam.K, cam.dist, cam.R, cam.t) for i in ids}


def test_solve_recovers_target_pose():
    cam = make_cam("A", TARGET_A)
    R, t, rms = solve_camera_pose(_obs(cam), MAT, cam.K, cam.dist)
    np.testing.assert_allclose(cam_center(R, t), TARGET_A, atol=1e-4)
    assert rotation_angle_deg(R, cam.R) < 0.01 and rms < 1e-3


def test_solve_with_two_markers_and_none():
    cam = make_cam("B", (-1.98, 0.85, 1.98))
    R, t, _ = solve_camera_pose(_obs(cam, ids=(1, 3)), MAT, cam.K, cam.dist)
    np.testing.assert_allclose(cam_center(R, t), cam_center(cam.R, cam.t), atol=1e-3)
    assert solve_camera_pose({}, MAT, cam.K, cam.dist) is None


def _render(cam, size=(1280, 720)):
    img = np.full((size[1], size[0]), 255, np.uint8)
    d = aruco_dictionary(MAT.aruco_dictionary)
    for i, corners3d in MAT.marker_corners().items():
        tile = cv2.aruco.generateImageMarker(d, i, 240)
        src = np.float32([[0, 0], [240, 0], [240, 240], [0, 240]])
        dst = project(corners3d, cam.K, cam.dist, cam.R, cam.t).astype(np.float32)
        H = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(tile, H, size, borderValue=255)
        mask = cv2.warpPerspective(np.full_like(tile, 255), H, size, borderValue=0) > 0
        img[mask] = warped[mask]
    return img


def test_detect_rendered_markers_then_solve():
    cam = make_cam("T", (0.0, 1.6, 1.6), target=(0.0, 0.0, 0.0))
    det = MarkerDetector(MAT).detect(_render(cam))
    assert set(det) == {0, 1, 2, 3}
    expected = _obs(cam)
    for i in det:
        assert np.max(np.linalg.norm(det[i] - expected[i], axis=1)) < 1.5
    R, t, _ = solve_camera_pose(det, MAT, cam.K, cam.dist)
    assert np.linalg.norm(cam_center(R, t) - cam_center(cam.R, cam.t)) < 0.02
    assert rotation_angle_deg(R, cam.R) < 0.5


def test_average_detections_requires_half_the_frames():
    a = {0: np.zeros((4, 2)), 1: np.ones((4, 2))}
    b = {0: np.full((4, 2), 2.0)}
    avg = average_detections([a, b, b])
    assert set(avg) == {0} and np.allclose(avg[0], 2.0)
    assert average_detections([]) == {}


def test_placement_hint_ok_at_target():
    R, t = look_at_pose(TARGET_A, AIM)
    h = placement_hint(R, t, TARGET_A, AIM, 0.10, 5.0)
    assert h["ok"] and h["msg"] == "OK"


def test_placement_hint_says_move_left():
    R, t = look_at_pose(TARGET_A, AIM)
    right = R[0].copy()
    right[1] = 0
    right /= np.linalg.norm(right)
    R2, t2 = look_at_pose(TARGET_A + 0.15 * right, AIM)
    h = placement_hint(R2, t2, TARGET_A, AIM, 0.10, 5.0)
    assert not h["ok"] and h["dx_cm"] == -15 and h["msg"].startswith("Move 15 cm left")


def test_camera_warnings():
    R, t = look_at_pose(TARGET_A, AIM)
    assert camera_warnings(R, t) == []
    R, t = look_at_pose(np.array([1.0, -0.5, 2.0]), AIM)
    assert any("below the floor" in w for w in camera_warnings(R, t))
    R, t = look_at_pose(np.array([1.0, 0.85, -2.0]), AIM)
    assert any("behind" in w for w in camera_warnings(R, t))
