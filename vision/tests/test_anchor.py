import numpy as np

from vision.anchor import FEET, FootAnchor
from vision.tests.synth import standing_skeleton

DT = 1 / 30


def _run(frames, anchor=None):
    anchor = anchor or FootAnchor()
    outs, locks = [], []
    for k, X in enumerate(frames):
        o, lk = anchor.update(k * DT, X)
        outs.append(o)
        locks.append(lk)
    return np.array(outs), locks


def test_jittery_planted_foot_is_held_still():
    rng = np.random.default_rng(0)
    X = standing_skeleton()
    frames = [X + rng.normal(0, 0.01, X.shape) for _ in range(60)]
    outs, locks = _run(frames)
    assert all(lk["l"] and lk["r"] for lk in locks[10:])
    for side, idx in FEET.items():
        assert outs[10:, list(idx)].std(axis=0).max() < 0.002  # raw jitter was 1 cm
    toe_y = outs[-1, 31, 1]
    assert abs(toe_y - 0.02) < 0.01  # lowest point on the mat surface
    assert outs[10:, 0].std(axis=0).max() > 0.005  # the rest of the body is untouched


def test_real_step_releases_quickly_without_a_jump():
    X = standing_skeleton()
    frames = [X.copy() for _ in range(15)]
    for k in range(1, 16):  # left foot steps 30 cm forward over 0.5 s
        Y = X.copy()
        Y[[27, 29, 31], 2] += 0.02 * k
        frames.append(Y)
    outs, locks = _run(frames)
    assert locks[14]["l"]
    first_away = next(k for k in range(15, 30) if frames[k][29, 2] - X[29, 2] > 0.05)
    assert not locks[first_away + 1]["l"]  # released within 2 frames of really moving
    assert locks[-1]["r"]  # the other foot stays planted
    jumps = np.linalg.norm(np.diff(outs[:, 29], axis=0), axis=1)
    assert jumps.max() < 0.06  # blends instead of jumping
    assert np.linalg.norm(outs[-1, 29] - frames[-1][29]) < 0.03  # follows the foot again


def test_squat_keeps_feet_locked_while_hips_drop():
    rng = np.random.default_rng(1)
    X = standing_skeleton()
    frames = []
    for k in range(60):
        Y = X + rng.normal(0, 0.008, X.shape)
        drop = 0.4 * min(k / 30, 1.0)
        Y[:25, 1] -= drop  # upper body and hips go down, feet stay
        frames.append(Y)
    outs, locks = _run(frames)
    assert all(lk["l"] and lk["r"] for lk in locks[10:])
    assert outs[-1, 23, 1] < X[23, 1] - 0.35


def test_lifted_or_missing_foot_is_not_locked():
    X = standing_skeleton()
    lifted = X.copy()
    lifted[[27, 29, 31], 1] += 0.25
    _, locks = _run([lifted] * 20)
    assert not locks[-1]["l"] and locks[-1]["r"]
    gone = X.copy()
    gone[29] = np.nan
    a = FootAnchor()
    _run([X] * 20, a)
    out, lk = a.update(1.0, gone)
    assert not lk["l"] and np.isnan(out[29]).all()


def test_pipeline_anchors_feet_and_can_be_turned_off():
    from vision.pipeline import Pipeline
    from vision.tests.synth import pose_from_points, target_cams

    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(2)
    on, off = Pipeline(cams, smoothing=False), Pipeline(cams, smoothing=False, anchor_feet=False)
    heels_on, heels_off, locked = [], [], []
    for k in range(45):
        a = pose_from_points(cams["A"], X, noise_px=2.0, rng=rng)
        b = pose_from_points(cams["B"], X, noise_px=2.0, rng=rng)
        o1, o2 = on.process(k / 30, a, b), off.process(k / 30, a, b)
        heels_on.append(o1.frame.joints[29])
        heels_off.append(o2.frame.joints[29])
        locked.append(o1.feet_locked)
    assert locked[-1] == {"l": True, "r": True} and off.process(2.0, a, b).feet_locked is None
    assert np.std(heels_on[15:], axis=0).max() < 0.3 * np.std(heels_off[15:], axis=0).max()
