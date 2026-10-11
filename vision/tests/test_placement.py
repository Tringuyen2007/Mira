import numpy as np

from vision.config import CameraConfig, load_mat
from vision.frames import look_at_pose, project
from vision.placement import (
    comparison_lines, draw_overlay, ghost_polylines, layout, lock_targets, target_eye,
)
from vision.markers import placement_hint
from vision.tests.synth import make_cam

MAT = load_mat()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])
EYE_A = np.array([3.18, 0.85, 3.18])


def test_lock_targets_is_mirror_symmetric():
    a, b = lock_targets([3.0, 0.9, 3.3], [-3.4, 0.8, 3.1])
    np.testing.assert_allclose(a, [3.2, 0.85, 3.2])
    np.testing.assert_allclose(b, a * [-1, 1, 1])


def test_lock_targets_keeps_symmetric_input():
    a, b = lock_targets(EYE_A, EYE_A * [-1, 1, 1])
    np.testing.assert_allclose(a, EYE_A)
    np.testing.assert_allclose(b, EYE_A * [-1, 1, 1])


def test_target_eye_prefers_locked():
    cam = CameraConfig(name="A", index=0)
    np.testing.assert_allclose(target_eye(cam, MAT, "A"), MAT.camera_targets["A"])
    cam.target_eye = EYE_A
    np.testing.assert_allclose(target_eye(cam, MAT, "A"), EYE_A)
    np.testing.assert_allclose(target_eye(None, MAT, "B"), MAT.camera_targets["B"])


def test_ghost_matches_markers_when_at_target():
    cam = make_cam("A", EYE_A, target=MAT.aim_point, K=K)
    polys = ghost_polylines(MAT, K, EYE_A, MAT.aim_point)
    assert len(polys) == 5  # mat outline + 4 markers
    for i, poly in zip(sorted(MAT.marker_corners()), polys[1:]):
        expected = project(MAT.marker_corners()[i], cam.K, None, cam.R, cam.t)
        np.testing.assert_allclose(poly, expected, atol=0.01)


def test_layout_and_comparison():
    ra, ta = look_at_pose(EYE_A, MAT.aim_point)
    rb, tb = look_at_pose(EYE_A * [-1, 1, 1] + [0, 0.05, 0], MAT.aim_point)
    la, lb = layout(ra, ta), layout(rb, tb)
    assert abs(la["dist_m"] - np.hypot(3.18, 3.18)) < 1e-9 and abs(la["bearing_deg"] - 45) < 1e-9
    assert abs(lb["bearing_deg"] + 45) < 1e-9 and abs(lb["height_m"] - 0.90) < 1e-9
    lines = comparison_lines({"A": la, "B": lb})
    assert len(lines) == 3 and "height 5 cm" in lines[2] and "distance 0 cm" in lines[2]


def test_draw_overlay_runs():
    R, t = look_at_pose(EYE_A, MAT.aim_point)
    tile = np.zeros((360, 640, 3), np.uint8)
    hint = placement_hint(R, t, EYE_A, MAT.aim_point, 0.05, 2.0)
    draw_overlay(tile, np.array([0.5, 0.5]), K, R, t, MAT, EYE_A, hint)
    assert tile.any()
