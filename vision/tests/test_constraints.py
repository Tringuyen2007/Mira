import numpy as np

from vision import constraints as C

FRAME = C.body_frame(np.zeros(3), [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])  # left +x, up +y, forward +z


def test_body_frame_is_right_handed_and_follows_the_person():
    f = C.body_frame(np.zeros(3), [1.0, 0.2, 0.0], [0.0, 2.0, 0.0])
    np.testing.assert_allclose(f.up, [0, 1, 0])
    np.testing.assert_allclose(f.left, [1, 0, 0])
    np.testing.assert_allclose(f.fwd, [0, 0, 1])  # left x up = forward (toward Mira when facing it)
    p = np.array([0.3, 0.5, -0.2])
    np.testing.assert_allclose(f.world(f.local(p)), p)
    assert C.body_frame(np.zeros(3), [0, 1, 0], [0, 1, 0]) is None


def test_min_reach_matches_150_degree_flexion():
    d = C.min_reach(0.4, 0.4)
    assert abs(d - 2 * 0.4 * np.sin(np.radians(15))) < 1e-9  # interior angle 30°


def test_hinge_pole_mirrors_a_knee_bending_backwards():
    hip, ankle = np.zeros(3), np.array([0.0, -0.8, 0.0])
    backwards = np.array([0.0, -0.4, -0.1])  # knee behind the line
    pole = C.hinge_pole(hip, ankle, backwards, FRAME.fwd)
    assert pole[2] > 0 and abs(pole[1] + 0.4) < 1e-9
    forwards = np.array([0.0, -0.4, 0.1])
    np.testing.assert_allclose(C.hinge_pole(hip, ankle, forwards, FRAME.fwd), forwards)
    straight = C.hinge_pole(hip, ankle, None, FRAME.fwd)
    assert straight[2] > 0  # a straight or missing knee bends forward


def test_limit_thigh_stops_it_swinging_far_behind():
    back = C.unit(np.array([0.0, -0.5, -0.8]))  # ~58° behind
    out = C.limit_thigh(back, FRAME)
    angle = np.degrees(np.arctan2(-out[2], -out[1]))
    assert abs(angle - C.THIGH_BACK_DEG) < 1e-6 and abs(np.linalg.norm(out) - 1) < 1e-9
    fwd = C.unit(np.array([0.0, -0.5, 0.8]))
    np.testing.assert_allclose(C.limit_thigh(fwd, FRAME), fwd)


def test_push_out_of_torso_goes_out_the_front_or_its_own_side():
    hw, length = 0.18, 0.55
    w, d = hw + C.LIMB_MARGIN_M, C.TORSO_DEPTH_M + C.LIMB_MARGIN_M
    front = np.array([-0.10, 0.3, 0.02])  # left hand crossed to the right, in front of the mid-plane
    p, moved, face = C.push_out_of_torso(front, FRAME, hw, length, side=1.0)
    assert moved and face == "front" and abs(p[2] - d) < 1e-9 and abs(p[0] + 0.10) < 1e-9  # stays on the right
    behind = np.array([-0.10, 0.3, -0.02])  # behind the mid-plane: out through the left arm's own side
    p, moved, face = C.push_out_of_torso(behind, FRAME, hw, length, side=1.0)
    assert moved and face == "side" and abs(p[0] - w) < 1e-9
    behind_right = np.array([0.0, 0.3, -0.05])
    p, moved, face = C.push_out_of_torso(behind_right, FRAME, hw, length, side=-1.0)
    assert moved and face == "side" and abs(p[0] + w) < 1e-9
    outside = np.array([0.5, 0.3, 0.0])
    p, moved, face = C.push_out_of_torso(outside, FRAME, hw, length, side=1.0)
    assert not moved and face is None and np.allclose(p, outside)


def test_push_out_of_torso_keeps_last_frames_face_near_the_boundary():
    hw, length = 0.18, 0.55
    w, d = hw + C.LIMB_MARGIN_M, C.TORSO_DEPTH_M + C.LIMB_MARGIN_M
    near_side = np.array([0.20, 0.3, 0.115])  # side push 0.02, front push 0.015: front chosen
    assert C.push_out_of_torso(near_side, FRAME, hw, length, side=1.0)[2] == "front"
    p, _, face = C.push_out_of_torso(near_side, FRAME, hw, length, side=1.0, prefer="side")
    assert face == "side" and abs(p[0] - w) < 1e-9  # within PUSH_HYSTERESIS_M: keep the side
    deep = np.array([0.0, 0.3, 0.05])  # side push 0.22 vs front 0.08: too far to keep the side
    assert C.push_out_of_torso(deep, FRAME, hw, length, side=1.0, prefer="side")[2] == "front"


def test_foot_yaw_is_limited_held_when_unsure_locked_and_rate_capped():
    fwd = np.array([0.0, 0.0, 1.0])
    out_60 = np.array([np.sin(np.radians(60)), 0.0, np.cos(np.radians(60))])
    y = C.foot_yaw(out_60, fwd, None, 1 / 30, None, True)
    assert abs(np.degrees(y) - C.FOOT_YAW_LIMIT_DEG) < 1e-9  # toe-out limited
    held = C.foot_yaw(None, fwd, np.radians(10), 1 / 30, None, False)
    assert abs(np.degrees(held) - 10) < 1e-9  # unsure: stays where it was
    locked = C.foot_yaw(out_60, fwd, np.radians(5), 1 / 30, np.radians(5), True)
    assert abs(np.degrees(locked) - 5) < 1e-9  # anchored: keeps its yaw
    step = C.foot_yaw(np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), 0.0, 1 / 30, None, True)
    assert abs(np.degrees(step)) <= C.FOOT_YAW_RATE_DPS / 30 + 1e-9  # a 90° jump is spread over frames


def test_foot_yaw_follows_a_real_turn_within_half_a_second():
    y = 0.0
    for k in range(40):
        a = np.radians(min(90, 12 * k))
        d = np.array([np.sin(a), 0.0, np.cos(a)])
        y = C.foot_yaw(d, d, y, 1 / 30, None, True)
    assert abs(np.degrees(y) - 90) < 5


def test_foot_pitch_range_and_flat():
    up = C.unit(np.array([0.0, 1.0, 0.3]))
    assert abs(np.degrees(C.foot_pitch(up, False)) - C.FOOT_PITCH_DEG[1]) < 1e-9
    assert C.foot_pitch(up, True) == 0.0 and C.foot_pitch(None, False) == 0.0


def test_smooth_angle_damps_wobble_and_passes_turns():
    assert C.smooth_angle(None, 1.0, 30, 0.2) == 1.0
    small = C.smooth_angle(0.0, np.radians(2), 30, 0.2)
    assert 0 < small < np.radians(2) * 0.25
    big = C.smooth_angle(0.0, np.radians(60), 30, 0.2)
    assert abs(big - np.radians(60)) < 1e-9
