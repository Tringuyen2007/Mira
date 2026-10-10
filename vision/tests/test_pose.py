import numpy as np
import pytest

from vision.pose import model_path


def test_blank_frame_has_no_person():
    try:
        model_path("full")
    except FileNotFoundError:
        pytest.skip("model not downloaded")
    from vision.pose import PoseEstimator

    est = PoseEstimator("full")
    try:
        r = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")
        r2 = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")  # same timestamp must not raise
    finally:
        est.close()
    assert r.cam == "A" and not r.detected and not r2.detected
