import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import project
from vision.moved import MoveMonitor
from vision.tests.synth import make_cam

MAT = MatConfig()


def _cams():
    cam = make_cam("A", (3.18, 0.85, 3.18))
    cam.marker_obs = {i: project(c, cam.K, None, cam.R, cam.t) for i, c in MAT.marker_corners().items()}
    return {"A": cam, "B": CameraConfig(name="B", index=1)}


def _shifted(cams, dx):
    return {i: c + [dx, 0.0] for i, c in cams["A"].marker_obs.items()}


def test_steady_shift_is_moved_after_three_checks():
    cams = _cams()
    mon = MoveMonitor(cams)
    assert [mon.observe("A", _shifted(cams, 5.0), t) for t in range(3)] == ["ok", "ok", "moved"]
    assert abs(mon.shift_px("A") - 5.0) < 1e-9 and "has moved (5 px)" in mon.message("A")


def test_one_off_outliers_and_missing_markers_are_not_moved():
    cams = _cams()
    mon = MoveMonitor(cams)
    seq = [_shifted(cams, 8.0), {}, _shifted(cams, 0.2), _shifted(cams, 8.0), {0: cams["A"].marker_obs[0]}]
    assert all(mon.observe("A", d, t) == "ok" for t, d in enumerate(seq))
    assert mon.message("A") is None


def test_moved_clears_after_three_good_checks():
    cams = _cams()
    mon = MoveMonitor(cams)
    for t in range(3):
        mon.observe("A", _shifted(cams, 5.0), t)
    assert [mon.observe("A", _shifted(cams, 0.0), 3 + t) for t in range(3)] == ["moved", "moved", "ok"]


def test_all_markers_hidden_is_blind_not_moved():
    cams = _cams()
    mon = MoveMonitor(cams, blind_s=10.0)
    mon.observe("A", _shifted(cams, 0.0), 0.0)
    assert mon.observe("A", {}, 9.0) == "ok"
    assert mon.observe("A", {}, 10.5) == "blind" and "markers hidden" in mon.message("A")
    assert mon.observe("A", _shifted(cams, 0.0), 11.0) == "ok"


def test_uncalibrated_camera_is_unknown():
    mon = MoveMonitor(_cams())
    assert mon.observe("B", {}, 0.0) == "unknown" and mon.message("B") is None


class _FakeDetector:
    def __init__(self, det):
        self.det, self.calls = det, 0

    def detect(self, image):
        self.calls += 1
        return self.det


def test_update_is_rate_limited():
    cams = _cams()
    det = _FakeDetector(_shifted(cams, 5.0))
    mon = MoveMonitor(cams, det, interval_s=1.0)
    for t in np.arange(0, 2.95, 0.1):
        mon.update("A", None, float(t))
    assert det.calls == 3 and mon.status("A") == "moved"
    mon.update("B", None, 5.0)
    assert det.calls == 3
