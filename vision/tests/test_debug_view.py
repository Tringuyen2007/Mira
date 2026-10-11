import numpy as np

from vision.config import MatConfig
from vision.debug_view import FRONT, TOP, compose_view
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_top_view_orientation():
    c = TOP(np.array([[0.0, 0.0]]))[0]
    left = TOP(np.array([[1.0, 0.0]]))[0]  # +x (patient's left) is drawn to the left from above
    front = TOP(np.array([[0.0, 1.0]]))[0]  # +z (Mira) is drawn at the top
    assert left[0] < c[0] and front[1] < c[1]


def test_front_view_orientation():
    c = FRONT(np.array([[0.0, 1.0]]))[0]
    assert FRONT(np.array([[0.5, 1.0]]))[0][0] > c[0]  # Mira's right is drawn to the right
    assert FRONT(np.array([[0.0, 1.5]]))[0][1] < c[1]


def test_compose_view_renders_with_and_without_images():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    out = Pipeline(cams).process(1.0, a, b, 0.004)
    stats = {"A": {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": "camera A delivers 640x480"},
             "B": {"fps": 29.0, "pose_ms": 21.0, "error": None, "warning": None}}
    skew = {"median_ms": 4.0, "p95_ms": 9.0, "unpaired_pct": 0.0}
    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, {"A": a, "B": b}, out, stats, skew, "REC")
    assert img.shape == (720, 1280, 3)
    img = compose_view({}, MatConfig(), {}, {}, None, None, None, None)
    assert img.shape == (720, 1280, 3)


def test_compose_view_shows_camera_moved_banner():
    cams = target_cams()
    plain = compose_view(cams, MatConfig(), {}, {}, None, None, None, None)
    alarm = compose_view(cams, MatConfig(), {}, {}, None, None, None, None,
                         alarms={"A": "Camera A has moved (6 px): re-run calib_extrinsics", "B": None})
    assert (alarm[:360, :640] != plain[:360, :640]).any()
    np.testing.assert_array_equal(alarm[:360, 640:], plain[:360, 640:])


def test_compose_view_draws_tracking_area_and_ignored_people():
    from vision.types import CameraPeople

    cams, X = target_cams(), standing_skeleton()
    on = (pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    far = X + [2.6, 0.0, 0.0]
    by = (pose_from_points(cams["A"], far), pose_from_points(cams["B"], far))
    out = Pipeline(cams).process(1.0, CameraPeople("A", 1.0, [by[0], on[0]]), CameraPeople("B", 1.0, [on[1], by[1]]))
    assert out.ignored["A"] == [by[0]]
    stats = {n: {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": None, "roi": (200, 100, 1000, 700)}
             for n in ("A", "B")}
    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, out, stats, None, None)
    plain = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, None, None, None, None)
    assert (img[:360, :640] != plain[:360, :640]).any()
    assert (img[:360, :640] == (255, 255, 0)).all(axis=2).any()  # the cyan tracking-area outline
