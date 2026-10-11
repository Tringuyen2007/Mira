import numpy as np

from vision.cleanup import HELD_CONF, Cleaner
from vision.config import CameraConfig
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.triangulate import triangulate_pair


def _setup():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    return cams, X, a, b


def test_all_triangulated_is_3d():
    cams, X, a, b = _setup()
    a.vis[:] = 0.9
    b.vis[:] = 0.7
    joints, conf, tracking = Cleaner(cams).update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    assert tracking == "3d"
    np.testing.assert_allclose(joints, X, atol=1e-6)
    np.testing.assert_allclose(conf, 0.7)


def test_failed_joint_falls_back_to_world_landmarks():
    cams, X, a, b = _setup()
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    tri.ok[15] = False
    tri.points[15] = np.nan
    joints, conf, _ = Cleaner(cams).update(0.0, tri, {"A": a, "B": b})
    np.testing.assert_allclose(joints[15], X[15], atol=1e-6)
    assert conf[15] == 1.0


def test_hold_last_good_then_expire():
    cams, X, a, b = _setup()
    c = Cleaner(cams)
    c.update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    a.world = b.world = None  # no single-view fallback available
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    tri.ok[15] = False
    tri.points[15] = np.nan
    joints, conf, _ = c.update(0.2, tri, {"A": a, "B": b})
    np.testing.assert_allclose(joints[15], X[15], atol=1e-6)
    assert conf[15] == HELD_CONF
    joints, conf, _ = c.update(0.6, tri, {"A": a, "B": b})
    assert np.isnan(joints[15]).all() and conf[15] == 0.0


def test_single_camera_anchored_at_last_hip_with_feet_on_floor():
    cams, X, a, b = _setup()
    X = X + [0.1, 0.0, -0.2]
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    c = Cleaner(cams)
    c.update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    joints, _, tracking = c.update(0.5, None, {"A": None, "B": b})
    assert tracking == "single_cam_B"
    np.testing.assert_allclose(joints[:, [0, 2]], X[:, [0, 2]], atol=1e-6)
    feet = [27, 28, 29, 30, 31, 32]
    assert abs(np.min(joints[feet, 1])) < 1e-9
    np.testing.assert_allclose(joints[:, 1], X[:, 1] - X[feet, 1].min(), atol=1e-6)


def test_uncalibrated_single_camera_has_head_up():
    cams, X, a, _ = _setup()
    raw = {"A": CameraConfig(name="A", index=0)}
    joints, _, tracking = Cleaner(raw).update(0.0, None, {"A": a, "B": None})
    assert tracking == "single_cam_A"
    assert joints[0, 1] > joints[27, 1]


def test_nobody_is_lost():
    cams, *_ = _setup()
    joints, conf, tracking = Cleaner(cams).update(0.0, None, {"A": None, "B": None})
    assert tracking == "lost" and np.isnan(joints).all() and not conf.any()
