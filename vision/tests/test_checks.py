import numpy as np

from vision.checks import (
    estimate_height_cm, bone_length_stats, outputs_from_recording, format_checks, knee_angle_deg, marker_center_distances, run_checks,
    triangulate_marker_centers,
)
from vision.config import MatConfig
from vision.frames import project
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams

MAT = MatConfig()


def _cams():
    cams = target_cams()
    for cam in cams.values():
        cam.marker_obs = {i: project(c, cam.K, cam.dist, cam.R, cam.t) for i, c in MAT.marker_corners().items()}
    return cams


def test_bone_lengths_and_knee_angle():
    X = standing_skeleton()
    stats = bone_length_stats(np.stack([X] * 20))
    assert abs(stats["thigh_l"][0] - np.linalg.norm(X[23] - X[25])) < 1e-12 and stats["thigh_l"][1] < 1e-12
    assert 170 < knee_angle_deg(X, "l") < 180
    bent = X.copy()
    bent[25] = bent[23] + [0, -0.43, 0]  # thigh vertical
    bent[27] = bent[25] + [0, 0, -0.43]  # shin horizontal: 90° at the knee
    assert abs(knee_angle_deg(bent, "l") - 90) < 1e-6


def test_marker_centres_and_distances():
    cams = _cams()
    centers = triangulate_marker_centers(cams["A"], cams["B"])
    np.testing.assert_allclose(centers[0], MAT.marker_centers()[0], atol=1e-3)
    dists = {label: (m, e) for label, m, e in marker_center_distances(centers, MAT)}
    assert abs(dists["0–2"][1] - 1.52) < 1e-9 and abs(dists["0–1"][1] - 0.40) < 1e-9
    assert abs(dists["0–2"][0] - 1.52) < 2e-3


def test_run_checks_all_pass_on_clean_synthetic_data():
    cams, X = _cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X, noise_px=0.3, rng=rng),
                         pose_from_points(cams["B"], X, noise_px=0.3, rng=rng), 0.005) for k in range(40)]
    checks = run_checks(outs, cams, MAT, fps={"A": 30.0, "B": 29.5})
    names = {c.name for c in checks}
    assert not any(c.name.startswith("lens RMS") for c in checks)
    assert {"frames tracked in 3D", "mean body reprojection", "thigh_l length std",
            "standing knee angle l", "median triangulation angle", "marker distance 0–2",
            "pair skew p95", "capture fps A", "feet anchored while standing", "foot height above mat"} <= names
    assert abs({c.name: c for c in checks}["foot height above mat"].value - 2.0) < 0.5
    failed = [c for c in checks if c.passed is False]
    assert failed == [], format_checks(failed)
    assert "PASS" in format_checks(checks)


def test_run_checks_with_no_outputs():
    checks = run_checks([], {}, MAT)
    assert all(c.passed is None for c in checks)


def test_checks_from_recording_use_unsmoothed_joints(tmp_path):
    from vision.record import Recorder

    cams, X = _cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    path = tmp_path / "still.jsonl"
    with Recorder(path, cams, {}) as rec:
        pipe = Pipeline(cams)
        for k in range(30):
            rec.write(pipe.process(1000 + k / 30, pose_from_points(cams["A"], X, noise_px=4.0, rng=rng),
                                   pose_from_points(cams["B"], X, noise_px=4.0, rng=rng), 0.0))
    outs = outputs_from_recording(path, cams)
    for out in outs:
        assert out.feet_locked is None  # checks measure the feet as triangulated, not anchored
        np.testing.assert_allclose(out.frame.joints[out.tri.ok], out.tri.points[out.tri.ok])
    anchored = {c.name: c for c in run_checks(outs, cams, MAT)}["feet anchored while standing"]
    assert anchored.value is not None  # computed from the joints, not from the pipeline's anchor


def test_feet_below_the_mat_fail_foot_height():
    cams, X = _cams(), standing_skeleton() - [0.0, 0.08, 0.0]
    pipe = Pipeline(cams, anchor_feet=False)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0) for k in range(20)]
    c = {c.name: c for c in run_checks(outs, cams, MAT)}["foot height above mat"]
    assert abs(c.value - -6.0) < 0.5 and c.passed is False


def test_height_estimate_and_check():
    cams, X = _cams(), standing_skeleton()
    expected = (X[2, 1] + X[5, 1]) / 2 / 0.936 * 100
    assert abs(estimate_height_cm(np.stack([X] * 10)) - expected) < 1e-9
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0) for k in range(20)]
    ok = {c.name: c for c in run_checks(outs, cams, MAT, height_cm=expected * 1.03)}["estimated height"]
    bad = {c.name: c for c in run_checks(outs, cams, MAT, height_cm=expected * 1.10)}["estimated height"]
    assert ok.passed is True and bad.passed is False
    assert "estimated height" not in {c.name for c in run_checks(outs, cams, MAT)}


def test_camera_moved_checks():
    checks = {c.name: c for c in run_checks([], {}, MAT, moved={"A": ("ok", 0.8), "B": ("moved", 6.0)})}
    assert checks["camera A not moved"].passed is True and checks["camera B not moved"].passed is False
    blind = {c.name: c for c in run_checks([], {}, MAT, moved={"A": ("blind", None)})}
    assert blind["camera A not moved"].passed is None


def test_pose_time_checks():
    checks = {c.name: c for c in run_checks([], {}, MAT, pose_ms={"A": 30.0, "B": 60.0})}
    assert checks["pose time A"].passed is True and checks["pose time B"].passed is False
    assert checks["pose time B"].value == 60.0 and checks["pose time B"].target == "≤ 45"
    assert "pose time A" not in {c.name for c in run_checks([], {}, MAT)}


def test_standing_straight_check_catches_a_tilted_calibration():
    from vision.checks import body_tilt_deg

    X = standing_skeleton()
    assert body_tilt_deg(np.stack([X] * 10)) < 1.0
    a = np.radians(5.0)  # whole body tipped 5° toward +z (what a slightly tilted calibration does)
    R = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    tilted = X @ R.T
    assert abs(body_tilt_deg(np.stack([tilted] * 10)) - 5.0) < 0.5
    cams = _cams()
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0) for k in range(20)]
    c = {c.name: c for c in run_checks(outs, cams, MAT)}["standing straight"]
    assert c.passed is True and c.unit == "°"


def test_one_camera_checks_skip_two_camera_ones():
    from vision.tests.test_mono import _cam_b
    from vision.mono import stature

    cam, X = _cam_b(), standing_skeleton()
    h = stature(X - (X[23] + X[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MAT, height_m=h)
    rng = np.random.default_rng(0)
    outs = [pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30, noise_px=0.5, rng=rng)) for k in range(90)]
    checks = {c.name: c for c in run_checks(outs, {"B": cam}, MAT, height_cm=h * 100)}
    assert {"frames tracked", "body fit to the camera", "jitter while still", "standing straight",
            "estimated height", "fit time"} <= set(checks)
    assert not {"median triangulation angle", "pair skew p95", "thigh_l length std"} & set(checks)
    for name in ("frames tracked", "body fit to the camera", "jitter while still", "estimated height"):
        assert checks[name].passed is True, (name, checks[name].value)
    assert checks["standing straight"].target == "≤ 3"  # one camera: a lean is MediaPipe's, not the calibration
