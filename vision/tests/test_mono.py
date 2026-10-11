import numpy as np

from vision.config import MatConfig
from vision.mono import mono_joints, stature, world_shape
from vision.pipeline import Pipeline
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

K = np.array([[1484.0, 0, 640], [0, 1484.0, 360], [0, 0, 1]])


def _cam_b():
    return make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K)


def test_world_shape_is_the_body_relative_to_its_hips_in_the_mat_frame():
    cam, X = _cam_b(), standing_skeleton()
    W = world_shape(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(W, X - (X[23] + X[24]) / 2, atol=1e-9)


def test_mono_joints_stand_where_the_feet_meet_the_floor():
    cam = _cam_b()
    X = standing_skeleton() + [0.25, 0.0, -0.4]
    J = mono_joints(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(J[[23, 24]].mean(axis=0)[[0, 2]], X[[23, 24]].mean(axis=0)[[0, 2]], atol=0.05)
    assert abs(np.min(J[[29, 30, 31, 32], 1])) < 1e-9  # lowest foot point on the mat


def test_stature_of_the_synthetic_body():
    X = standing_skeleton()
    s = stature(X - (X[23] + X[24]) / 2)
    assert abs(s - X[2, 1] / 0.936 - (0 - np.min(X[[29, 30, 31, 32], 1])) / 0.936) < 1e-9


def test_one_camera_pipeline_fits_a_moving_body_with_the_right_scale():
    cam, X0 = _cam_b(), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    rng = np.random.default_rng(0)
    errs = []
    for k in range(120):
        X = X0 + [0.003 * max(k - 60, 0), 0.0, 0.0]
        v = pose_from_points(cam, X, t=k / 30, noise_px=1.0, rng=rng)
        out = pipe.process(k / 30, None, v)
        if k > 70:
            assert out.tracker == "model"
            errs.append(np.median(np.linalg.norm(out.frame.joints - X, axis=1)))
    assert np.median(errs) < 0.04  # one camera: a few cm


def test_one_camera_without_feet_keeps_the_last_position():
    cam, X = _cam_b(), standing_skeleton()
    J1 = mono_joints(pose_from_points(cam, X), cam)
    p = pose_from_points(cam, X)
    p.vis[[27, 28, 29, 30]] = 0.0
    assert mono_joints(p, cam) is None
    J2 = mono_joints(p, cam, last_xz=J1[[23, 24]].mean(axis=0)[[0, 2]])
    assert J2 is not None
