import numpy as np

from vision.rig import FRAMES_NEEDED, BodyRig, two_bone_ik
from vision.tests.synth import standing_skeleton

DT = 1 / 30
AFTER = FRAMES_NEEDED * DT  # first frame after measuring (later than FORGET_S would re-measure)


def _length(J, a, b):
    return float(np.linalg.norm(J[a] - J[b]))


def _measure(rig, X, n=FRAMES_NEEDED, t0=0.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = None
    for k in range(n):
        out = rig.update(t0 + k * DT, X + rng.normal(0, noise, X.shape), "3d")
    return out


def test_measures_the_body_then_locks_lengths():
    X = standing_skeleton()
    rig = BodyRig()
    first = rig.update(0.0, X, "3d")
    np.testing.assert_allclose(first, X)  # while measuring, the raw joints are shown
    assert 0 < rig.progress < 1 and rig.lengths is None
    _measure(rig, X, t0=DT)
    assert rig.progress == 1.0 and rig.lengths is not None
    upper_arm = (_length(X, 11, 13) + _length(X, 12, 14)) / 2  # left and right averaged
    assert abs(rig.lengths["upper_arm"] - upper_arm) < 1e-9
    assert abs(rig.lengths["shoulder_width"] - _length(X, 11, 12)) < 1e-9


def test_bone_lengths_stay_fixed_while_the_detections_wobble():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X, noise=0.01)
    rng = np.random.default_rng(5)
    raw_len, rig_len = [], []
    for k in range(60):
        J = X + rng.normal(0, 0.02, X.shape)
        P = rig.update(AFTER + k * DT, J, "3d")
        raw_len.append(_length(J, 23, 25))
        rig_len.append([_length(P, 23, 25), _length(P, 25, 27), _length(P, 11, 13), _length(P, 13, 15),
                        _length(P, 11, 12), _length(P, 23, 24)])
    assert np.std(raw_len) > 0.01
    assert np.array(rig_len).std(axis=0).max() < 1e-9  # every bone keeps one length
    np.testing.assert_allclose(np.array(rig_len)[0, :2], [rig.lengths["thigh"], rig.lengths["shin"]], atol=1e-9)


def test_two_bone_ik_reaches_the_target_and_bends_toward_the_pole():
    root, target, pole = np.zeros(3), np.array([0.0, -0.7, 0.2]), np.array([0.0, -0.4, 0.4])
    mid, end = two_bone_ik(root, target, pole, 0.45, 0.42)
    np.testing.assert_allclose(end, target, atol=1e-9)
    assert abs(np.linalg.norm(mid - root) - 0.45) < 1e-9 and abs(np.linalg.norm(end - mid) - 0.42) < 1e-9
    axis = target / np.linalg.norm(target)
    off = (mid - root) - ((mid - root) @ axis) * axis
    assert off @ (pole - (pole @ axis) * axis) > 0  # bent the same way as the tracked knee


def test_two_bone_ik_out_of_reach_points_straight_at_the_target():
    mid, end = two_bone_ik(np.zeros(3), np.array([0.0, -2.0, 0.0]), np.array([0.0, -1.0, 0.5]), 0.45, 0.42)
    assert np.linalg.norm(end) <= 0.87 + 1e-9 and end[1] < -0.86
    assert abs(np.linalg.norm(mid) - 0.45) < 1e-9


def test_planted_ankle_stays_put_with_fixed_leg_lengths():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X)
    J = X.copy()
    J[23:25, 1] -= 0.2  # hips drop (a squat); ankles stay
    J[25:27, 2] += 0.15  # knees forward
    P = rig.update(AFTER, J, "3d")
    np.testing.assert_allclose(P[27], X[27], atol=1e-6)
    assert P[25, 2] > X[25, 2]  # knee bends forward like the tracked knee
    assert abs(_length(P, 23, 25) - rig.lengths["thigh"]) < 1e-9


def test_new_person_or_b_key_re_measures():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X)
    rig.update(1.5, None, "lost")
    rig.update(4.0, X, "single_cam_A")  # one camera still sees them: same person
    assert rig.lengths is not None  # brief loss: keep the body
    rig.update(9.5, X, "3d")  # more than 5 s with nobody tracked: assume someone new
    assert rig.lengths is None and rig.progress < 1
    _measure(rig, X, t0=10.0)
    rig.reset()
    assert rig.lengths is None


def test_lost_or_missing_hips_shows_nothing_new():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X)
    assert rig.update(AFTER, None, "lost") is None
    J = X.copy()
    J[23] = np.nan
    assert rig.update(AFTER + DT, J, "3d") is None


def test_limb_never_seen_hangs_down_at_its_measured_length():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X)
    J = X.copy()
    J[[13, 15]] = np.nan  # left arm missing from the very first rigged frame
    P = rig.update(AFTER, J, "3d")
    assert abs(_length(P, 11, 13) - rig.lengths["upper_arm"]) < 1e-9
    assert abs(_length(P, 13, 15) - rig.lengths["forearm"]) < 1e-9
    assert P[15, 1] < P[11, 1] - 0.4  # hanging below the shoulder


def test_missing_wrist_holds_the_arm():
    X = standing_skeleton()
    rig = BodyRig()
    _measure(rig, X)
    P1 = rig.update(AFTER, X, "3d")
    J = X.copy()
    J[15] = np.nan
    P2 = rig.update(AFTER + DT, J, "3d")
    np.testing.assert_allclose(P2[15], P1[15], atol=1e-6)
    assert np.isfinite(P2[13]).all()


def test_single_camera_frames_are_posed_but_not_measured():
    X = standing_skeleton()
    rig = BodyRig()
    rig.update(0.0, X, "single_cam_A")
    assert rig.progress == 0.0
    _measure(rig, X, t0=DT)
    P = rig.update(AFTER + DT, X + [0.0, 0.0, 0.01], "single_cam_B")
    assert P is not None and abs(_length(P, 25, 27) - rig.lengths["shin"]) < 1e-9


def test_the_frame_that_completes_measuring_is_already_rigged():
    rng = np.random.default_rng(3)
    X = standing_skeleton()
    rig = BodyRig()
    out = None
    for k in range(FRAMES_NEEDED):
        J = X + rng.normal(0, 0.01, X.shape)
        out = rig.update(k * DT, J, "3d")
    assert rig.lengths is not None
    assert abs(_length(out, 25, 27) - rig.lengths["shin"]) < 1e-9  # not the raw, wobbly frame


def _rigged(rig, X, J):
    _measure(rig, X)
    return rig.update(AFTER, J, "3d")


def test_constrained_knee_never_bends_backwards():
    X = standing_skeleton()
    J = X.copy()
    J[23:25, 1] -= 0.15  # slight squat
    J[25:27, 2] -= 0.12  # but the detected knees point backwards
    P = _rigged(BodyRig(), X, J)
    for hip, knee, ankle in ((23, 25, 27), (24, 26, 28)):
        d = (P[ankle] - P[hip]) / np.linalg.norm(P[ankle] - P[hip])
        off = (P[knee] - P[hip]) - ((P[knee] - P[hip]) @ d) * d
        assert off[2] > 0  # knee in front of the hip-ankle line
    plain = _rigged(BodyRig(constraints=False), X, J)
    assert plain[25, 2] < P[25, 2]  # the plain rig follows the backwards knee


def test_constrained_arm_is_kept_out_of_the_torso():
    from vision.avatar_eval import inside_torso

    X = standing_skeleton()
    J = X.copy()
    J[15] = (X[11] + X[23]) / 2 * [0.2, 1.0, 1.0]  # left wrist detected inside the belly
    J[13] = (J[11] + J[15]) / 2 + [0.0, 0.0, -0.02]
    rig = BodyRig()
    P = _rigged(rig, X, J)
    assert not inside_torso(P, P[15]) and not inside_torso(P, P[13])
    assert abs(_length(P, 11, 13) - rig.lengths["upper_arm"]) < 1e-9
    assert abs(_length(P, 13, 15) - rig.lengths["forearm"]) < 1e-9


def test_constrained_feet_point_forward_and_stay_flat():
    X = standing_skeleton()
    J = X.copy()
    J[31] = J[29] + [0.16, 0.0, 0.0]  # left toe detected pointing straight out sideways (90°)
    P = _rigged(BodyRig(), X, J)
    v = P[31] - P[29]
    yaw = np.degrees(np.arctan2(v[0], v[2]))
    assert yaw <= 35 + 1e-6 and abs(v[1]) < 1e-9  # within toe-out limit, flat on the floor


def test_constrained_rig_follows_confident_tracking_closely():
    X = standing_skeleton()
    J = X.copy()
    J[15] = X[15] + [0.05, 0.25, 0.25]  # left hand raised forward, reachable
    P = _rigged(BodyRig(), X, J)
    assert np.linalg.norm(P[15] - J[15]) < 0.02
