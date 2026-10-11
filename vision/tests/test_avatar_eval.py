import numpy as np

from vision.avatar_eval import HEADER, evaluate, hinge_violation, inside_torso
from vision.pipeline import Pipeline
from vision.rig import BodyRig
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_inside_torso_detects_a_hand_in_the_belly_only():
    X = standing_skeleton()
    belly = (X[11] + X[12] + X[23] + X[24]) / 4
    assert inside_torso(X, belly)
    assert not inside_torso(X, X[15])  # hand hanging at the side
    assert not inside_torso(X, belly + [0.0, 0.0, 0.3])  # in front of the body


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
    assert "arm∈torso" in HEADER and r.row("synthetic").startswith("synthetic")
