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


def test_left_right_swap_in_one_view_is_undone_by_continuity():
    cams, X = target_cams(), standing_skeleton()
    for flipped in ("A", "B"):
        pipe = Pipeline(cams, smoothing=False)
        pipe.process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
        a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
        out = pipe.process(1.033, _flip(a) if flipped == "A" else a, _flip(b) if flipped == "B" else b)
        assert out.swapped == flipped
        np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)
        assert out.raw[flipped].lm2d[11][0] == (a if flipped == "A" else b).lm2d[11][0]  # raw left untouched


def test_no_swap_without_a_recent_3d_frame():
    cams, X = target_cams(), standing_skeleton()
    b = _flip(pose_from_points(cams["B"], X))
    out = Pipeline(cams, smoothing=False).process(1.0, pose_from_points(cams["A"], X), b)
    assert out.swapped is None
