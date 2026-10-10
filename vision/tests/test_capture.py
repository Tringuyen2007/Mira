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
