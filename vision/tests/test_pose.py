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
        r2 = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A", roi=(100, 50, 900, 650))  # same timestamp: no raise
    finally:
        est.close()
    assert r == [] and r2 == []


def test_people_are_mapped_from_the_crop_to_full_frame_pixels():
    try:
        model_path("full")
    except FileNotFoundError:
        pytest.skip("model not downloaded")
    from types import SimpleNamespace

    from vision.pose import PoseEstimator

    def person(x, y):
        return [SimpleNamespace(x=x, y=y, z=0.0, visibility=0.9)] * 33

    fake = SimpleNamespace(pose_landmarks=[person(0.5, 0.25), person(0.1, 0.9)],
                           pose_world_landmarks=[person(0.0, 0.0), person(0.0, 0.0)])
    est = PoseEstimator("full")
    try:
        seen = {}

        def detect_for_video(image, ts):
            seen["shape"] = image.numpy_view().shape
            return fake

        est._landmarker.detect_for_video = detect_for_video
        people = est.detect(np.zeros((720, 1280, 3), np.uint8), 2.0, "B", roi=(400, 100, 1200, 500))
    finally:
        est._landmarker.detect_for_video = lambda *a: None
        est.close()
    assert seen["shape"][:2] == (400, 800)  # only the crop was searched
    assert len(people) == 2 and all(p.cam == "B" and p.t == 2.0 for p in people)
    np.testing.assert_allclose(people[0].lm2d[0], [400 + 0.5 * 800, 100 + 0.25 * 400])
    np.testing.assert_allclose(people[1].lm2d[0], [400 + 0.1 * 800, 100 + 0.9 * 400])
    assert people[0].world.shape == (33, 3)
