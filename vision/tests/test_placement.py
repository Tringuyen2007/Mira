import numpy as np

from vision.config import CameraConfig, load_mat
from vision.frames import look_at_pose, project
from vision.placement import (
    comparison_lines, draw_overlay, draw_person_box, fit_report, ghost_polylines, layout, lock_in_place, next_step, render_map,
    swap_warning, target_aim, target_eye, targets_stale,
)
from vision.markers import placement_hint
from vision.tests.synth import make_cam

MAT = load_mat()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])
EYE_A = np.array([3.18, 0.85, 3.18])


def test_lock_in_place_keeps_each_camera_where_it_is():
    R, t = look_at_pose(EYE_A, [0.3, 0.5, 0.0])
    eye, aim = lock_in_place(R, t, MAT.aim_point)
    np.testing.assert_allclose(eye, EYE_A, atol=1e-9)
    R2, _ = look_at_pose(eye, aim)
    np.testing.assert_allclose(R2, R, atol=1e-9)  # aiming at the locked point reproduces the locked view
    h = placement_hint(R, t, eye, aim, 0.05, 2.0)
    assert h["ok"]


def test_target_aim_prefers_locked():
    cam = CameraConfig(name="A", index=0)
    np.testing.assert_allclose(target_aim(cam, MAT), MAT.aim_point)
    cam.target_aim = np.array([0.2, 0.7, 0.1])
    np.testing.assert_allclose(target_aim(cam, MAT), [0.2, 0.7, 0.1])


def test_targets_stale_after_moving_location():
    assert not targets_stale({"A": EYE_A}, {"A": EYE_A + [0.3, 0, 0]})
    assert targets_stale({"A": EYE_A}, {"A": EYE_A + [2.0, 0, 0]})
    assert not targets_stale({"A": EYE_A}, {})


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


def test_fit_report_whole_person_fits_far_away():
    R, t = look_at_pose(EYE_A, [0.0, 0.9, 0.0])
    assert fit_report(K, R, t, (1280, 720), MAT) == []


def test_fit_report_says_what_is_cut_off():
    eye = np.array([1.6, 0.9, 1.6])  # too close for a 48° webcam
    R, t = look_at_pose(eye, [0.0, 0.9, 0.0])
    msgs = fit_report(K, R, t, (1280, 720), MAT)
    assert any("head" in m for m in msgs) and any("feet" in m for m in msgs)
    R, t = look_at_pose(EYE_A, [0.0, 1.8, 0.0])  # aimed too high
    msgs = fit_report(K, R, t, (1280, 720), MAT)
    assert any("feet" in m and "tilt down" in m for m in msgs)


def test_next_step_picks_the_biggest_correction():
    R, t = look_at_pose(EYE_A, MAT.aim_point)
    right = R[0].copy()
    right[1] = 0
    right /= np.linalg.norm(right)
    R2, t2 = look_at_pose(EYE_A - 0.4 * right + [0, 0.1, 0], MAT.aim_point)
    text, arrow = next_step(placement_hint(R2, t2, EYE_A, MAT.aim_point, 0.05, 2.0))
    assert arrow == "right" and text.startswith("Move right")
    R3, _ = look_at_pose(EYE_A, MAT.aim_point + [0, 0.6, 0])
    text, arrow = next_step(placement_hint(R3, -R3 @ EYE_A, EYE_A, MAT.aim_point, 0.05, 2.0))
    assert arrow == "tilt_down" and text.startswith("Tilt down")
    assert next_step(placement_hint(R, t, EYE_A, MAT.aim_point, 0.05, 2.0)) == (None, None)


def test_swap_warning():
    ok = {"A": {"bearing_deg": 44.0}, "B": {"bearing_deg": -46.0}}
    swapped = {"A": {"bearing_deg": -40.0}, "B": {"bearing_deg": 41.0}}
    assert swap_warning(ok) is None
    assert "swapped" in swap_warning(swapped)


def test_render_map_draws_camera_on_its_side():
    from vision.placement import CAM_COLORS

    R, t = look_at_pose(EYE_A, MAT.aim_point)
    img = render_map(MAT, {"A": {"R": R, "t": t, "K": K, "size": (1280, 720), "target": EYE_A + [0.5, 0, 0]}})
    ys, xs = np.nonzero(np.all(img == CAM_COLORS["A"], axis=2))
    assert xs.size and xs.min() < img.shape[1] / 2 - 100  # +x (patient's left, camera A) is drawn on the left
    assert ys.min() < img.shape[0] / 2  # in front of the mat (Mira side) is drawn at the top
    blank = render_map(MAT, {})
    assert not np.all(blank == CAM_COLORS["A"], axis=2).any()


def test_draw_person_box_runs():
    R, t = look_at_pose(EYE_A, MAT.aim_point)
    tile = np.zeros((360, 640, 3), np.uint8)
    draw_person_box(tile, np.array([0.5, 0.5]), K, R, t, MAT, ok=True)
    assert tile.any()
