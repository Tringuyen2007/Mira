import time

import numpy as np

from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.tracker import ModelTracker, forward, measure, params_from_joints


def _views(cams, X, rng=None, noise=0.0):
    return {n: pose_from_points(c, X, noise_px=noise, rng=rng) for n, c in cams.items()}


def _measured(cams, X, frames=12):
    tr = ModelTracker(cams, frames_needed=10)
    for k in range(frames):
        tr.update(k / 30, X, "3d", _views(cams, X))
    return tr


def test_params_round_trip_reproduces_a_standing_body():
    X = standing_skeleton()
    shape = measure([X] * 12)
    Y = forward(params_from_joints(X, shape), shape)
    assert np.linalg.norm(Y - X, axis=1).max() < 0.002


def test_fit_follows_a_moving_body_from_both_cameras_quickly():
    cams, X = target_cams(), standing_skeleton()
    tr = _measured(cams, X)
    rng = np.random.default_rng(1)
    times, errs = [], []
    for k in range(12, 60):
        Z = X + [0.004 * k, 0.0, 0.0]
        t0 = time.perf_counter()
        out = tr.update(k / 30, Z, "3d", _views(cams, Z, rng, 1.0))
        times.append(time.perf_counter() - t0)
        errs.append(np.median(np.linalg.norm(out - Z, axis=1)))
    assert max(errs[3:]) < 0.01 and np.median(times) < 0.02  # (the body jumps 5 cm at frame 12; settles in 2)


def test_fit_recovers_from_a_wrong_pose():
    cams, X = target_cams(), standing_skeleton()
    tr = _measured(cams, X)
    tr._p = tr._p.copy()
    tr._p[3:6] = [0.0, np.pi, 0.0]  # facing backwards
    out = None
    for k in range(12, 16):
        out = tr.update(k / 30, X, "3d", _views(cams, X))
    assert tr.recoveries >= 1 and np.median(np.linalg.norm(out - X, axis=1)) < 0.01


def test_pipeline_default_is_unchanged_and_model_takes_over_after_measuring():
    cams, X = target_cams(), standing_skeleton()
    tri, model = Pipeline(cams), Pipeline(cams, tracker="model")
    outs = []
    for k in range(50):
        v = _views(cams, X)
        assert tri.process(k / 30, v["A"], v["B"]).tracker == "triangulate"
        outs.append(model.process(k / 30, v["A"], v["B"]))
    assert outs[0].tracker == "triangulate" and outs[-1].tracker == "model" and outs[-1].fit_ms is not None
    J = outs[-1].frame.joints
    assert np.isfinite(J).all() and np.linalg.norm(np.median(J, axis=0) - np.median(X, axis=0)) < 0.02
