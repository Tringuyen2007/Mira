import numpy as np

from vision.physics import PhysicsEstimator
from vision.tests.synth import standing_skeleton
from vision.types import FrameResult


def frame(t=0.0, joints=None, conf=None, **kwargs):
    kwargs.setdefault("tracking", "single_cam_B")
    return FrameResult(t=t, joints=standing_skeleton() if joints is None else joints,
                       conf=np.ones(33) if conf is None else conf, **kwargs)


def test_static_stance_com_cop_and_symmetric_estimates():
    estimator = PhysicsEstimator()
    result = estimator.apply(frame())
    assert result.stance == "double" and result.contact_l and result.contact_r
    assert result.com is not None and 0.7 < result.com[1] < 1.3
    assert result.cop is not None and abs(result.cop[0]) < 0.01
    assert 45 < result.load_l_pct < 55
    assert 0 < result.heel_l_pct < 100 and 0 < result.heel_r_pct < 100
    assert 0 < result.load_conf < 0.65


def test_weight_shift_changes_left_estimate_without_moving_feet():
    estimator = PhysicsEstimator()
    neutral = estimator.estimate(frame())
    shifted = standing_skeleton()
    shifted[:27, 0] += 0.065  # head, trunk and upper legs lean toward patient-left foot
    left = estimator.estimate(frame(0.1, shifted))
    assert left.load_l_pct > neutral.load_l_pct + 10
    assert left.cop[0] > neutral.cop[0]


def test_lifted_foot_gives_single_leg_and_missing_heel():
    joints = standing_skeleton()
    joints[[28, 30, 32], 1] += 0.25
    result = PhysicsEstimator().estimate(frame(joints=joints))
    assert result.stance == "left" and result.contact_l and not result.contact_r
    assert result.load_l_pct == 100
    assert result.heel_r_pct is None


def test_motion_reduces_confidence_and_fast_motion_suppresses_load():
    estimator = PhysicsEstimator()
    base = estimator.estimate(frame())
    moving = standing_skeleton()
    moving[:27, 0] += 0.025
    mid = estimator.estimate(frame(0.1, moving))
    assert mid.load_conf < base.load_conf
    moving[:27, 0] += 0.30
    fast = estimator.estimate(frame(0.2, moving))
    assert fast.com is not None
    assert fast.load_l_pct is None and fast.cop is None and fast.load_conf == 0


def test_occlusion_and_held_pose_do_not_reuse_pressure():
    estimator = PhysicsEstimator()
    estimator.estimate(frame())
    conf = np.ones(33)
    conf[[27, 29, 31]] = 0.1
    partial = estimator.estimate(frame(0.1, conf=conf))
    assert partial.com is not None and partial.stance == "right"
    assert not partial.contact_l and partial.heel_l_pct is None
    held = estimator.estimate(frame(0.2, flags=["pose_held"]))
    assert held.com is None and held.load_conf == 0
    lost = estimator.estimate(frame(0.3, tracking="lost"))
    assert lost.stance == "unknown" and lost.cop is None


def test_floor_exercise_keeps_com_but_suppresses_foot_load():
    joints = standing_skeleton()
    joints[[11, 12], 1] = 0.35
    joints[[23, 24], 1] = 0.45
    result = PhysicsEstimator().estimate(frame(joints=joints))
    assert result.stance == "floor" and result.com is not None
    assert result.cop is None and result.load_l_pct is None and result.load_conf == 0


def test_deep_squat_is_not_mistaken_for_floor_exercise():
    joints = standing_skeleton()
    joints[[11, 12], 1] = 0.76
    joints[[23, 24], 1] = 0.39
    result = PhysicsEstimator().estimate(frame(joints=joints))
    assert result.stance == "double" and result.load_l_pct is not None


def test_implausibly_short_foot_does_not_claim_contact():
    joints = standing_skeleton()
    joints[31] = joints[29] + [0.0, 0.0, 0.02]
    joints[32] = joints[30] + [0.0, 0.0, 0.02]
    result = PhysicsEstimator().estimate(frame(joints=joints))
    assert result.stance == "none" and result.load_l_pct is None


def test_sliding_foot_loses_contact_after_motion_window():
    estimator = PhysicsEstimator()
    estimator.estimate(frame())
    result = None
    for i in range(1, 7):
        joints = standing_skeleton()
        joints[[27, 29, 31], 2] += 0.04 * i
        result = estimator.estimate(frame(0.05 * i, joints))
    assert result.stance == "right" and not result.contact_l


def test_missing_torso_and_measuring_return_unknown():
    conf = np.ones(33)
    conf[23] = 0.1
    assert PhysicsEstimator().estimate(frame(conf=conf)).com is None
    assert PhysicsEstimator().estimate(frame(body="measuring")).load_conf == 0
