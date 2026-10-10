import numpy as np

from vision.config import CameraConfig
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.types import swap_left_right


def test_calibrated_pair_gives_3d():
    cams, X = target_cams(), standing_skeleton()
    out = Pipeline(cams).process(1000.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.004)
    assert out.frame.tracking == "3d" and out.frame.t == 1000.0
    np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)
    assert out.tri is not None and out.skew_s == 0.004 and out.raw["A"].cam == "A" and out.swapped is None


def test_uncalibrated_runs_single_camera():
    cams, X = target_cams(), standing_skeleton()
    a = pose_from_points(cams["A"], X)
    out = Pipeline({"A": CameraConfig(name="A", index=0)}).process(1.0, a, None)
    assert out.frame.tracking == "single_cam_A" and out.tri is None


def test_too_few_triangulated_joints_falls_back_to_single():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    b.lm2d = b.lm2d + 200.0  # wildly inconsistent second view
    out = Pipeline(cams).process(1.0, a, b)
    assert out.frame.tracking.startswith("single_cam") and out.tri is not None


def _flip(p):
    p.lm2d, p.vis, p.world = swap_left_right(p.lm2d), swap_left_right(p.vis), swap_left_right(p.world)
    return p


def test_left_right_flip_in_one_view_is_undone_without_history():
    cams, X = target_cams(), standing_skeleton()
    for flipped in ("A", "B"):
        a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
        out = Pipeline(cams, smoothing=False).process(1.0, _flip(a) if flipped == "A" else a,
                                                      _flip(b) if flipped == "B" else b)
        assert out.swapped == flipped
        np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)
        assert out.raw[flipped].lm2d[11][0] == (a if flipped == "A" else b).lm2d[11][0]  # raw left untouched


def test_both_views_flipped_is_undone():
    cams, X = target_cams(), standing_skeleton()
    out = Pipeline(cams, smoothing=False).process(
        1.0, _flip(pose_from_points(cams["A"], X)), _flip(pose_from_points(cams["B"], X)))
    assert out.swapped == "AB"
    np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)


def test_wrong_start_does_not_lock_in():
    cams, X = target_cams(), standing_skeleton()
    pipe = Pipeline(cams, smoothing=False)
    for k in range(5):  # B flipped while the person is first picked up
        pipe.process(k / 30, pose_from_points(cams["A"], X), _flip(pose_from_points(cams["B"], X)))
    for k in range(5, 10):  # MediaPipe corrects itself
        out = pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
        assert out.swapped is None
        np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)


def test_noisy_world_landmarks_do_not_trigger_flips():
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(3)
    pipe = Pipeline(cams, smoothing=False)
    for k in range(20):
        a = pose_from_points(cams["A"], X, noise_px=1.0, rng=rng)
        b = pose_from_points(cams["B"], X, noise_px=1.0, rng=rng)
        a.world = a.world + rng.normal(0, 0.04, a.world.shape)
        b.world = b.world + rng.normal(0, 0.04, b.world.shape)
        assert pipe.process(k / 30, a, b).swapped is None
