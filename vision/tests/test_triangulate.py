import numpy as np

from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.triangulate import triangulate_pair, triangulate_points


def test_exact_recovery_without_distortion():
    cams, X = target_cams(), standing_skeleton()
    tri = triangulate_pair(pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert tri.ok.all()
    np.testing.assert_allclose(tri.points, X, atol=1e-6)
    assert np.nanmax(tri.reproj_err) < 1e-4


def test_recovery_with_lens_distortion():
    cams, X = target_cams(dist=[-0.12, 0.05, 0.0, 0.0, 0.0]), standing_skeleton()
    tri = triangulate_pair(pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    np.testing.assert_allclose(tri.points, X, atol=1e-3)


def test_pixel_noise_gives_bounded_error():
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(1)
    a = pose_from_points(cams["A"], X, noise_px=1.0, rng=rng)
    b = pose_from_points(cams["B"], X, noise_px=1.0, rng=rng)
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    assert np.nanmax(np.linalg.norm(tri.points - X, axis=1)) < 0.02


def test_low_visibility_is_skipped():
    cams, X = target_cams(), standing_skeleton()
    vis = np.ones(33)
    vis[15] = 0.2
    tri = triangulate_pair(pose_from_points(cams["A"], X, vis=vis), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert not tri.ok[15] and np.isnan(tri.points[15]).all() and tri.ok[16]


def test_error_along_epipolar_line_is_not_detectable():
    # Both cameras are at the same height, so epipolar lines are near-horizontal: a horizontal
    # 2D error (or a left/right swap) stays consistent across views. Pipeline handles swaps.
    cams, X = target_cams(), standing_skeleton()
    a = pose_from_points(cams["A"], X)
    a.lm2d[13] += [50.0, 0.0]
    tri = triangulate_pair(a, pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert tri.reproj_err[13] < 15


def test_outlier_rejected_by_reprojection():
    cams, X = target_cams(), standing_skeleton()
    a = pose_from_points(cams["A"], X)
    a.lm2d[13] += [0.0, 50.0]  # across the (near-horizontal) epipolar lines
    tri = triangulate_pair(a, pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert not tri.ok[13] and tri.reproj_err[13] > 15


def test_triangulation_angle_near_ninety_at_centre():
    cams = target_cams()
    X = np.array([[0.0, 0.8, 0.0]])
    uv_a = pose_from_points(cams["A"], np.repeat(X, 33, 0)).lm2d[:1]
    uv_b = pose_from_points(cams["B"], np.repeat(X, 33, 0)).lm2d[:1]
    _, _, angle = triangulate_points(uv_a, uv_b, cams["A"], cams["B"])
    assert 85 < angle[0] < 95
