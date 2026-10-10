import cv2
import numpy as np

from vision.calib_intrinsics import (
    PATTERN, board_image, board_object_points, calibrate, calibrate_with_outlier_drop, is_novel, view_descriptor,
)

K = np.array([[1000.0, 0, 640], [0, 1000, 360], [0, 0, 1]])
DIST = np.array([0.1, -0.2, 0, 0, 0])


def _views(n=20, seed=0):
    rng = np.random.default_rng(seed)
    obj = board_object_points(0.03)
    obj = (obj - obj.mean(axis=0)).astype(np.float32)
    objs, imgs = [], []
    while len(objs) < n:
        rvec = rng.normal(0, 0.35, 3)
        tvec = np.array([rng.uniform(-0.1, 0.1), rng.uniform(-0.07, 0.07), rng.uniform(0.4, 0.7)])
        img, _ = cv2.projectPoints(obj, rvec, tvec, K, DIST)
        img = img.reshape(-1, 2) + rng.normal(0, 0.1, (len(obj), 2))
        if (img[:, 0] > 0).all() and (img[:, 0] < 1280).all() and (img[:, 1] > 0).all() and (img[:, 1] < 720).all():
            objs.append(obj)
            imgs.append(img.astype(np.float32))
    return objs, imgs


def test_board_image_has_detectable_pattern():
    img = board_image()
    found, corners = cv2.findChessboardCornersSB(img, PATTERN)
    assert found and len(corners) == PATTERN[0] * PATTERN[1]


def test_calibrate_recovers_intrinsics():
    objs, imgs = _views()
    rms, K2, dist2, errs = calibrate(objs, imgs, (1280, 720))
    assert rms < 0.3 and len(errs) == 20
    assert abs(K2[0, 0] - 1000) < 5 and abs(K2[0, 2] - 640) < 5 and abs(K2[1, 2] - 360) < 5


def test_outlier_view_is_dropped():
    objs, imgs = _views()
    imgs[3] = imgs[3] + np.random.default_rng(5).normal(0, 6, imgs[3].shape).astype(np.float32)
    rms, _, _, _, dropped = calibrate_with_outlier_drop(objs, imgs, (1280, 720))
    assert dropped >= 1 and rms < 0.3


def test_novelty():
    _, imgs = _views(2)
    d = view_descriptor(imgs[0], (1280, 720))
    assert not is_novel(d, [d])
    assert is_novel(d, [])
    assert is_novel(view_descriptor(imgs[0] + [400, 0], (1280, 720)), [d])
