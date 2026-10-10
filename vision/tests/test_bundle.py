from dataclasses import replace

import cv2
import numpy as np
import pytest

from vision.bundle import evaluate_guards, refine_extrinsics, rodrigues
from vision.config import MatConfig
from vision.frames import cam_center, project, rotation_angle_deg
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams

MAT = MatConfig()


def _scene(n_frames=120, seed=0):
    rng = np.random.default_rng(seed)
    truth = target_cams()
    for cam in truth.values():
        cam.marker_obs = {i: project(c, cam.K, cam.dist, cam.R, cam.t) + rng.normal(0, 0.2, (4, 2))
                          for i, c in MAT.marker_corners().items()}
    skel = standing_skeleton()
    pairs = []
    for f in range(n_frames):
        yaw = rng.uniform(-0.6, 0.6)
        Ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
        X = skel @ Ry.T + [rng.uniform(-0.2, 0.2), 0, rng.uniform(-0.6, 0.6)] + rng.normal(0, 0.03, skel.shape)
        pairs.append((pose_from_points(truth["A"], X, t=f / 30, noise_px=0.5, rng=rng),
                      pose_from_points(truth["B"], X, t=f / 30, noise_px=0.5, rng=rng)))
    return truth, pairs, rng


def _perturbed(cam, rng, move_m, rot_deg):
    d = rng.normal(size=3)
    axis = rng.normal(size=3)
    dR, _ = cv2.Rodrigues(axis / np.linalg.norm(axis) * np.radians(rot_deg))
    R2 = dR @ cam.R
    C2 = cam_center(cam.R, cam.t) + d / np.linalg.norm(d) * move_m
    return replace(cam, R=R2, t=-R2 @ C2)


def test_rodrigues_matches_opencv():
    r = np.array([0.1, -0.4, 0.25])
    np.testing.assert_allclose(rodrigues(r), cv2.Rodrigues(r)[0], atol=1e-12)
    np.testing.assert_allclose(rodrigues(np.zeros(3)), np.eye(3))


def test_refine_recovers_perturbed_poses():
    truth, pairs, rng = _scene()
    start = {n: _perturbed(c, rng, 0.05, 1.5) for n, c in truth.items()}
    report = refine_extrinsics(start, MAT, pairs)
    assert report.ok, report.reasons
    assert report.body_rms_after < report.body_rms_before and report.body_rms_after < 1.0
    for n in ("A", "B"):
        assert np.linalg.norm(report.centers[n] - cam_center(truth[n].R, truth[n].t)) < 0.01
        assert rotation_angle_deg(report.R[n], truth[n].R) < 0.3
    assert 75 < report.median_angle_deg < 100
    assert "body reprojection" in report.summary()


def test_too_few_observations_raises():
    truth, pairs, _ = _scene(n_frames=2)
    with pytest.raises(ValueError, match="not enough"):
        refine_extrinsics(truth, MAT, pairs)


def test_missing_markers_raises():
    truth, pairs, _ = _scene(n_frames=10)
    for cam in truth.values():
        cam.marker_obs = None
    with pytest.raises(ValueError, match="marker"):
        refine_extrinsics(truth, MAT, pairs)


def test_guards():
    ok = evaluate_guards(5.0, 2.0, {"A": 0.1}, {"A": 2.0}, {"A": 0.8}, {"A": 1.2})
    assert ok == []
    bad = evaluate_guards(2.0, 3.0, {"A": 0.7}, {"A": 20.0}, {"A": 0.5}, {"A": 3.0})
    assert len(bad) == 4
