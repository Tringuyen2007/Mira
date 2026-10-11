from vision.capture import FpsMeter, camera_problem, resolution_warning
from vision.config import CameraConfig


def test_fps_meter():
    m = FpsMeter()
    for k in range(61):
        m.tick(k / 30)
    assert abs(m.value - 30.0) < 0.5
    assert FpsMeter().value == 0.0


def test_resolution_warning():
    cfg = CameraConfig(name="A", index=0, width=1280, height=720)
    assert resolution_warning(cfg, (1280, 720)) is None
    assert resolution_warning(cfg, None) is None
    msg = resolution_warning(cfg, (640, 480))
    assert "640x480" in msg and "1280x720" in msg


def test_camera_problem():
    cfg = CameraConfig(name="A", index=3, width=1280, height=720)
    assert camera_problem(cfg, None, (1280, 720)) is None
    assert "index 3" in camera_problem(cfg, None, None)  # no frames yet
    assert camera_problem(cfg, "camera A (index 3) failed to open", None) == "camera A (index 3) failed to open"
    assert "1920x1080" in camera_problem(cfg, None, (1920, 1080))


def _finished(thread):
    thread.run = lambda: None  # no hardware: the thread starts and ends at once
    thread.start()
    thread.stop()
    thread.join(timeout=1.0)  # crashed with "'Event' object is not callable" when _stop was shadowed
    return not thread.is_alive()


def test_camera_and_pose_threads_join_after_stop():
    import queue

    from vision.capture import Camera
    from vision.pose import PoseWorker

    cam = Camera("A", 0)
    assert _finished(cam)
    assert _finished(PoseWorker(cam, queue.Queue()))


def test_brief_read_failures_are_not_errors():
    import numpy as np

    from vision.capture import Camera

    cam = Camera("B", 1)
    frame = np.zeros((720, 1280, 3), np.uint8)
    cam._on_read(False, None, 10.0)
    cam._on_read(False, None, 10.5)
    assert cam.error is None  # a webcam warming up drops a few frames
    cam._on_read(False, None, 11.2)
    assert cam.error == "camera B: read failed"
    cam._on_read(True, frame, 11.3)
    assert cam.error is None and cam.actual_size == (1280, 720) and cam.latest()[0] == 1
