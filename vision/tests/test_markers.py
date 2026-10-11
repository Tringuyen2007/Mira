import cv2
import numpy as np

from vision.config import MatConfig
from vision.frames import cam_center, look_at_pose, project, rotation_angle_deg
from vision.markers import (
    MarkerDetector, aruco_dictionary, average_detections, camera_warnings, estimate_focal, nominal_K,
    placement_hint, solve_camera_pose, solve_with_zoom,
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
    h = placement_hint(R, t, TARGET_A, AIM, 0.05, 2.0)
    assert h["ok"] and h["msg"] == "OK"
    assert h["position_msg"] == "Position OK" and h["aim_msg"] == "Aim OK"


def test_placement_hint_says_move_left():
    R, t = look_at_pose(TARGET_A, AIM)
    right = R[0].copy()
    right[1] = 0
    right /= np.linalg.norm(right)
    R2, t2 = look_at_pose(TARGET_A + 0.15 * right, AIM)
    h = placement_hint(R2, t2, TARGET_A, AIM, 0.05, 2.0)
    assert not h["ok"] and not h["position_ok"] and h["aim_ok"]
    assert h["dx_cm"] == -15 and h["position_msg"] == "Move 15 cm left"


def test_placement_hint_says_back_when_too_close():
    R, t = look_at_pose(TARGET_A * 0.9 + AIM * 0.1, AIM)
    h = placement_hint(R, t, TARGET_A, AIM, 0.05, 2.0)
    assert "cm back" in h["position_msg"]


def _rolled(R, deg):
    a = np.radians(deg)
    x, y = R[0], R[1]
    return np.vstack([np.cos(a) * x - np.sin(a) * y, np.sin(a) * x + np.cos(a) * y, R[2]])


def test_placement_hint_roll_direction():
    R, t = look_at_pose(TARGET_A, AIM)
    R2 = _rolled(R, 3.0)
    assert R2[0][1] > 0  # image right edge raised
    h = placement_hint(R2, -R2 @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
    assert h["position_ok"] and not h["aim_ok"]
    assert h["roll_deg"] == 3.0 and h["aim_msg"] == "Rotate clockwise 3.0°"
    h = placement_hint(_rolled(R, -3.0), -_rolled(R, -3.0) @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
    assert h["aim_msg"] == "Rotate counter-clockwise 3.0°"


def test_placement_hint_turn_direction():
    R, t = look_at_pose(TARGET_A, AIM)
    right = R[0].copy()
    right[1] = 0
    aim_left = AIM - 0.3 * right / np.linalg.norm(right)
    R2, _ = look_at_pose(TARGET_A, aim_left)  # pointing left of the aim point
    h = placement_hint(R2, -R2 @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
    assert h["aim_msg"].startswith("Turn right")


def test_camera_warnings():
    R, t = look_at_pose(TARGET_A, AIM)
    assert camera_warnings(R, t) == []
    R, t = look_at_pose(np.array([1.0, -0.5, 2.0]), AIM)
    assert any("below the floor" in w for w in camera_warnings(R, t))
    R, t = look_at_pose(np.array([1.0, 0.85, -2.0]), AIM)
    assert any("behind" in w for w in camera_warnings(R, t))

FAR_A = np.array([3.18, 0.85, 3.18])
K_WEBCAM = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _webcam_obs(ids=(0, 1, 2, 3), noise=0.3, seed=0, eye=FAR_A):
    cam = make_cam("A", eye, K=K_WEBCAM)
    rng = np.random.default_rng(seed)
    return cam, {i: o + rng.normal(0, noise, o.shape) for i, o in _obs(cam, ids).items()}


def test_estimate_focal_from_four_markers():
    _, obs = _webcam_obs()
    est, reason = estimate_focal(obs, MAT, (1280, 720))
    assert reason is None
    assert abs(est.f / 1425 - 1) < 0.03 and est.f_std / est.f < 0.03
    assert abs(est.hfov_deg - 48.4) < 1.5 and est.rms < 1.0
    np.testing.assert_allclose(est.K[:2, 2], [640, 360])


def test_estimate_focal_needs_both_ends():
    _, obs = _webcam_obs(ids=(0, 1))
    est, reason = estimate_focal(obs, MAT, (1280, 720))
    assert est is None and "both ends" in reason


def test_estimate_focal_rejects_uncertain_estimates():
    _, obs = _webcam_obs(ids=(1, 3), noise=3.0)
    est, reason = estimate_focal(obs, MAT, (1280, 720))
    assert est is None and "uncertain" in reason


def test_nominal_K():
    K = nominal_K(1280, 720)
    assert abs(np.degrees(2 * np.arctan(np.hypot(640, 360) / K[0, 0])) - 55) < 1e-9


def test_solve_with_zoom_measures_then_falls_back():
    cam, obs = _webcam_obs(noise=0.1)
    sol, good = solve_with_zoom(obs, MAT, (1280, 720))
    assert not sol.guessed and "zoom 48" in sol.zoom_msg and good is not None
    assert np.linalg.norm(cam_center(sol.R, sol.t) - FAR_A) < 0.10
    near = {i: obs[i] for i in (0, 1)}
    sol2, good2 = solve_with_zoom(near, MAT, (1280, 720), last_good=good)
    assert sol2.guessed and good2 is good and np.allclose(sol2.K, good.K)
    sol3, good3 = solve_with_zoom(near, MAT, (1280, 720))
    assert sol3.guessed and good3 is None and "guessed" in sol3.zoom_msg
    assert solve_with_zoom({}, MAT, (1280, 720)) == (None, None)
