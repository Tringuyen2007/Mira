from vision.calib_refine import REFINED_ZOOM_STD, refined_zoom_std


def test_refined_zoom_counts_as_measured_but_never_worse():
    assert refined_zoom_std(None) == REFINED_ZOOM_STD <= 0.03
    assert refined_zoom_std(0.08) == REFINED_ZOOM_STD
    assert refined_zoom_std(0.01) == 0.01


def test_countdown_gives_time_to_walk_to_the_mat():
    from vision.calib_refine import COUNTDOWN_S

    assert COUNTDOWN_S == 8.0


def test_refine_views_uses_lone_people_even_when_the_cameras_disagree():
    from vision.calib_refine import refine_views
    from vision.config import MatConfig
    from vision.pairing import Pair
    from vision.pipeline import Pipeline
    from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
    from vision.types import CameraPeople

    cams, X = target_cams(), standing_skeleton()
    pa = pose_from_points(cams["A"], X)
    pb = pose_from_points(cams["B"], X + [0.0, 0.25, 0.0])  # calibration off: the views don't agree
    pair = Pair(0.0, CameraPeople("A", 0.0, [pa]), CameraPeople("B", 0.0, [pb]), 0.0)
    out = Pipeline(cams, mat=MatConfig()).process(1000.0, pair.a, pair.b, 0.0)
    assert out.dropped is not None  # selection kept only one camera
    a, b = refine_views(pair, out)
    assert a is pa and b is pb

    other = pose_from_points(cams["A"], X + [2.5, 0.0, 0.0])
    pair2 = Pair(0.0, CameraPeople("A", 0.0, [other, pa]), CameraPeople("B", 0.0, [pb]), 0.0)
    out2 = Pipeline(cams, mat=MatConfig()).process(1000.0, pair2.a, pair2.b, 0.0)
    a, b = refine_views(pair2, out2)
    assert a is out2.raw["A"] and b is out2.raw["B"]


def test_calib_refine_explains_it_needs_two_cameras(monkeypatch, capsys):
    import sys

    import vision.calib_refine as R
    from vision.tests.synth import target_cams

    monkeypatch.setattr(R, "load_cameras", lambda: {"B": target_cams()["B"]})
    monkeypatch.setattr(sys, "argv", ["calib_refine"])
    assert R.main() == 1
    assert "needs two calibrated cameras" in capsys.readouterr().out
