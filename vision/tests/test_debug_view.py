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
