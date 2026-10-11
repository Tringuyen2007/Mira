import numpy as np

from vision.config import MatConfig
from vision.select import PersonSelector, floor_position, mat_distance, pair_position
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cams():
    return {"A": make_cam("A", (3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K),
            "B": make_cam("B", (-3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K)}


def _person(cams, x, z, t=0.0):
    X = standing_skeleton() + [x, 0.0, z]
    return pose_from_points(cams["A"], X, t=t), pose_from_points(cams["B"], X, t=t)


def test_mat_distance():
    assert mat_distance(MAT, (0.0, 0.0)) == 0.0
    assert abs(mat_distance(MAT, (MAT.width_m / 2 + 1.0, 0.0)) - 1.0) < 1e-12


def test_floor_position_from_one_camera():
    cams = _cams()
    a, _ = _person(cams, 0.4, -0.3)
    np.testing.assert_allclose(floor_position(a, cams["A"]), [0.4, -0.3], atol=0.08)


def test_pair_position_same_person_agrees_and_different_people_dont():
    cams = _cams()
    a1, b1 = _person(cams, 0.0, 0.0)
    _, b2 = _person(cams, 2.5, 1.5)
    err, xz = pair_position(a1, b1, cams["A"], cams["B"])
    assert err < 1.0 and np.linalg.norm(xz - [0.0, 0.0]) < 0.05
    bad = pair_position(a1, b2, cams["A"], cams["B"])
    assert bad is None or bad[0] > 25


def test_bystander_away_from_the_mat_is_ignored():
    cams = _cams()
    on_a, on_b = _person(cams, 0.0, 0.2)
    by_a, by_b = _person(cams, 2.6, 0.0)  # 2.3 m to the side of the mat
    sel = PersonSelector(cams, MAT).select(0.0, [by_a, on_a], [on_b, by_b])
    assert sel.a is on_a and sel.b is on_b and sel.dropped is None
    assert sel.ignored == {"A": [by_a], "B": [by_b]}
    only = PersonSelector(cams, MAT).select(0.0, [by_a], [by_b])
    assert only.a is None and only.b is None


def test_person_next_to_the_mat_is_tracked():
    cams = _cams()
    a, b = _person(cams, MAT.width_m / 2 + 0.7, 0.0)
    sel = PersonSelector(cams, MAT).select(0.0, [a], [b])
    assert sel.a is a and sel.b is b


def test_two_on_the_mat_closest_to_centre_then_sticky_then_switch():
    cams = _cams()
    near_a, near_b = _person(cams, 0.0, 0.1)
    edge_a, edge_b = _person(cams, 0.15, -0.75)
    assert PersonSelector(cams, MAT).select(0.0, [edge_a, near_a], [near_b, edge_b]).a is near_a

    sel = PersonSelector(cams, MAT)
    tr_a, tr_b = _person(cams, 0.0, 0.6)  # tracked person, towards the Mira end
    assert sel.select(0.0, [tr_a], [tr_b]).a is tr_a
    ch_a, ch_b = _person(cams, 0.0, -0.05)  # someone steps in at the centre: 0.55 m closer
    picks = [sel.select(0.1 * (k + 1), [ch_a, tr_a], [tr_b, ch_b]).a for k in range(3)]
    assert all(p is tr_a for p in picks)  # not yet: under 0.5 s
    picks = [sel.select(0.4 + 0.1 * k, [ch_a, tr_a], [tr_b, ch_b]).a for k in range(4)]
    assert picks[-1] is ch_a  # closer for over 0.5 s: switched


def test_small_wobble_does_not_switch():
    cams = _cams()
    sel = PersonSelector(cams, MAT)
    a1, b1 = _person(cams, 0.0, 0.3)
    a2, b2 = _person(cams, 0.0, 0.1)  # only 0.2 m closer to the centre than the tracked person
    sel.select(0.0, [a1], [b1])
    assert all(sel.select(0.1 * k, [a2, a1], [b1, b2]).a is a1 for k in range(1, 20))


def test_hand_over_one_lens_drops_that_camera():
    cams = _cams()
    a, b = _person(cams, 0.0, 0.0)
    junk = pose_from_points(cams["A"], standing_skeleton())  # reuse shape, then make it a huge blob
    junk.lm2d = np.random.default_rng(0).uniform([300, 0], [1000, 300], (33, 2))  # big shape, high up
    sel = PersonSelector(cams, MAT).select(0.0, [junk], [b])
    assert sel.a is None and sel.b is b and sel.dropped == "A"
    assert sel.ignored["A"] == [junk]


def test_uncalibrated_falls_back_to_first_person():
    cams = _cams()
    a, b = _person(cams, 0.0, 0.0)
    cams["B"].R = None
    sel = PersonSelector(cams, MAT).select(0.0, [a], [b])
    assert sel.a is a and sel.b is b
