import numpy as np

from vision.avatar_eval import HEADER, arm_in_torso, evaluate, hinge_violation, inside_torso
from vision.pipeline import Pipeline
from vision.rig import BodyRig
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_inside_torso_detects_a_hand_in_the_belly_only():
    X = standing_skeleton()
    belly = (X[11] + X[12] + X[23] + X[24]) / 4
    assert inside_torso(X, belly)
    assert not inside_torso(X, X[15])  # hand hanging at the side
    assert not inside_torso(X, belly + [0.0, 0.0, 0.3])  # in front of the body


def test_a_hand_inside_the_torso_counts_as_arm_in_torso():
    X = standing_skeleton()
    assert not arm_in_torso(X)
    J = X.copy()
    J[19] = (X[11] + X[12] + X[23] + X[24]) / 4  # left index finger in the belly, elbow and wrist outside
    assert arm_in_torso(J)


def test_hinge_violation_flags_a_backwards_knee_and_over_flexion():
    X = standing_skeleton()
    assert not hinge_violation(X, 23, 25, 27, True)
    back = X.copy()
    back[25] = back[25] + [0.0, 0.0, -0.08]
    assert hinge_violation(back, 23, 25, 27, True)
    folded = X.copy()
    folded[15] = folded[11] + [0.0, -0.01, 0.0]  # wrist almost back at the shoulder: elbow past 150°
    assert hinge_violation(folded, 11, 13, 15, False)


def test_evaluate_reports_a_still_synthetic_person_as_clean():
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X, noise_px=1.0, rng=rng),
                         pose_from_points(cams["B"], X, noise_px=1.0, rng=rng)) for k in range(90)]
    r = evaluate(iter(outs), BodyRig())
    assert r.frames > 30 and r.arm_in_torso_pct == 0 and r.hinge_pct == 0
    assert r.bone_std_mm < 1e-6 and r.foot_yaw_max < 10 and r.truth_mean_cm < 5
    assert r.truth_mid_cm < 5 and r.truth_held_cm < 5 and r.foot_vs_tracked_p95 < 10
    assert "arm∈torso" in HEADER and r.row("synthetic").startswith("synthetic") and "\n" not in r.row("x")


def test_recording_outputs_use_the_recordings_own_calibration(tmp_path, monkeypatch):
    from vision.avatar_eval import recording_outputs
    from vision.record import Recorder

    cams, X = target_cams(), standing_skeleton()
    path = tmp_path / "rec.jsonl"
    pipe = Pipeline(cams)
    with Recorder(path, cams, {}) as rec:
        for k in range(90):  # the rig needs a few seconds of tracking to measure the body
            rec.write(pipe.process(1000 + k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)))
    monkeypatch.setattr("vision.config.load_cameras", lambda *a, **k: {})  # a recalibrated config that no longer matches
    assert evaluate(recording_outputs(path), BodyRig()).frames > 0
    assert evaluate(recording_outputs(path, current_calibration=True), BodyRig()).frames == 0
