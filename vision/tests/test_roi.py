import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import project
from vision.roi import crop, mat_roi, uncrop
from vision.tests.synth import make_cam

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cam(eye=(3.18, 0.9, 3.18)):
    cam = make_cam("A", eye, target=(0.0, 0.9, 0.0), K=K)
    cam.width, cam.height = 1280, 720
    return cam


def test_roi_covers_the_mat_area_and_is_inside_the_image():
    cam = _cam((4.5, 1.0, 4.5))
    x0, y0, x1, y1 = mat_roi(cam, MAT, margin_m=0.3)
    assert 0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 720
    feet = project(np.array([[x, 0.0, z] for x in (-0.3, 0.3) for z in (-0.86, 0.86)]), cam.K, cam.dist, cam.R, cam.t)
    head = project(np.array([[0.0, 1.9, 0.0]]), cam.K, cam.dist, cam.R, cam.t)
    for u, v in np.vstack([feet, head]):
        assert x0 <= u <= x1 and y0 <= v <= y1
    assert (x1 - x0) * (y1 - y0) < 1280 * 720  # it actually narrows the search


def test_roi_none_when_uncalibrated_or_box_behind_camera():
    assert mat_roi(CameraConfig(name="A", index=0), MAT) is None
    assert mat_roi(_cam((0.5, 1.0, 0.5)), MAT) is None  # camera inside the grown box


def test_crop_and_uncrop_round_trip():
    frame = np.arange(720 * 1280 * 3, dtype=np.uint32).reshape(720, 1280, 3)
    roi = (100, 50, 900, 650)
    sub, off = crop(frame, roi)
    assert sub.shape == (600, 800, 3) and off == (100, 50)
    np.testing.assert_array_equal(sub[0, 0], frame[50, 100])
    np.testing.assert_allclose(uncrop(np.array([[0.0, 0.0], [10.5, 3.0]]), off), [[100, 50], [110.5, 53]])
    full, off0 = crop(frame, None)
    assert full is frame and off0 == (0, 0)
