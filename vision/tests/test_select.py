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


def test_only_one_calibrated_camera_selects_from_that_camera_by_the_floor():
    cams = _cams()
    on_a, on_b = _person(cams, 0.0, 0.2)
    by_a, by_b = _person(cams, 2.6, 0.0)
    cams["A"].R = None  # A not calibrated (or unplugged): B alone
    sel = PersonSelector(cams, MAT).select(0.0, [on_a], [by_b, on_b])
    assert sel.a is None and sel.b is on_b and sel.ignored["B"] == [by_b]
    np.testing.assert_allclose(sel.position, [0.0, 0.2], atol=0.08)

def _giant(cam, x=0.0, z=0.0, scale=2.0):
    """A skeleton standing at (x, z) in one view, blown up about its feet: far too tall for a person
    (head well above the image), like MediaPipe fitting a body to a hand over the lens."""
    p = pose_from_points(cam, standing_skeleton() + [x, 0.0, z])
    feet = p.lm2d[[27, 28, 29, 30]].mean(axis=0)
    p.lm2d = feet + (p.lm2d - feet) * scale
    return p


def test_plausible_height():
    from vision.select import plausible_height

    cams = _cams()
    a, _ = _person(cams, 0.0, 0.0)
    assert plausible_height(a, cams["A"], floor_position(a, cams["A"]))
    giant = _giant(cams["A"])
    assert not plausible_height(giant, cams["A"], floor_position(giant, cams["A"]))
    tiny = _giant(cams["A"], scale=0.25)  # ~0.4 m: not a person
    assert not plausible_height(tiny, cams["A"], floor_position(tiny, cams["A"]))
    squat = _giant(cams["A"], scale=0.42)  # ~0.7 m tall: the bottom of a deep squat is still a person
    assert plausible_height(squat, cams["A"], floor_position(squat, cams["A"]))
    headless = pose_from_points(cams["A"], standing_skeleton())
    headless.vis[[0, 2, 5]] = 0.0
    assert not plausible_height(headless, cams["A"], floor_position(headless, cams["A"]))


def test_oversized_junk_on_the_mat_in_one_camera_loses_to_the_real_person():
    cams = _cams()
    junk = _giant(cams["A"])  # its feet put it at the mat centre, but it is ~3 m tall
    assert mat_distance(MAT, floor_position(junk, cams["A"])) == 0.0
    _, b = _person(cams, 0.0, -0.8)  # the real person, at the mat edge, seen only by B
    sel = PersonSelector(cams, MAT).select(0.0, [junk], [b])
    assert sel.a is None and sel.b is b and sel.dropped == "A"
    assert sel.ignored["A"] == [junk]


def test_single_camera_fallback_never_switches_to_a_closer_person():
    cams = _cams()
    sel = PersonSelector(cams, MAT)
    tr_a, tr_b = _person(cams, 0.0, 0.6)
    assert sel.select(0.0, [tr_a], [tr_b]).a is tr_a
    ch_a, _ = _person(cams, 0.0, -0.05)  # plausible person 0.55 m closer to the centre, only in A
    picks = [sel.select(0.1 * k, [ch_a, tr_a], []).a for k in range(1, 20)]  # nobody pairs: fallback
    assert all(p is tr_a for p in picks)


def test_one_empty_frame_keeps_the_track_but_a_long_gap_forgets_it():
    cams = _cams()
    tr_a, tr_b = _person(cams, 0.0, 0.6)  # tracked, towards the edge
    ch_a, ch_b = _person(cams, 0.0, -0.05)  # closer to the centre
    sel = PersonSelector(cams, MAT)
    assert sel.select(0.0, [tr_a], [tr_b]).a is tr_a
    assert sel.select(0.033, [], []).a is None  # one frame with nobody
    assert sel.select(0.066, [ch_a, tr_a], [tr_b, ch_b]).a is tr_a  # no instant swap

    sel = PersonSelector(cams, MAT)
    assert sel.select(0.0, [tr_a], [tr_b]).a is tr_a
    for k in range(1, 37):  # 1.2 s of nobody
        sel.select(k / 30, [], [])
    assert sel.select(1.2, [ch_a, tr_a], [tr_b, ch_b]).a is ch_a  # track forgotten: centre wins at once


def test_floor_position_trusts_the_planted_foot_not_the_lifted_one():
    cams = _cams()
    X = standing_skeleton()
    X[[27, 29, 31], 1] += 0.25  # left foot lifted 25 cm (a step)
    a = pose_from_points(cams["A"], X)
    xz = floor_position(a, cams["A"])
    np.testing.assert_allclose(xz, X[[28, 30], :][:, [0, 2]].mean(axis=0), atol=0.06)  # the right (planted) foot
