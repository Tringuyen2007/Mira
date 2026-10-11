import numpy as np

from vision.presence import PresenceDetector
from vision.tests.synth import standing_skeleton
from vision.types import FrameResult


def frame(t, x=0.0, lost=False, held=False, facing=True):
    j = standing_skeleton() + [x, 0, 0]
    if not facing:
        j[[11, 12]] = j[[12, 11]]
    return FrameResult(t=t, joints=j, conf=np.ones(33), tracking="lost" if lost else "single_cam_B",
                       flags=["pose_held"] if held else [])


def test_arrival_requires_both_feet_inside_margin_and_facing_hold():
    d = PresenceDetector()
    assert d.update(frame(0, facing=False)) == []
    assert d.update(frame(1, x=0.20)) == []
    assert d.on_mat is False
    assert d.update(frame(2)) == []
    assert d.on_mat is True
    assert d.update(frame(3.4)) == []  # long unsampled gap cannot satisfy hold
    assert d.update(frame(3.9)) == []
    assert d.update(frame(4.4)) == []
    assert d.update(frame(4.9)) == [{"type": "arrived", "t": 4.9}]
    assert d.update(frame(5.0)) == []


def test_off_mat_pauses_once_after_two_seconds_and_loss_does_not_count():
    d = PresenceDetector()
    d.update(frame(0), in_set=True)
    for t in np.arange(0.25, 2.0, 0.25):
        assert d.update(frame(float(t), x=0.25), in_set=True) == []
    assert d.update(frame(2.0, lost=True), in_set=True) == []
    assert d.on_mat is None
    assert d.update(frame(2.25, x=0.25), in_set=True) == []
    events = []
    for t in np.arange(2.5, 4.5, 0.25):
        events += d.update(frame(float(t), x=0.25), in_set=True)
    assert events == [{"type": "paused", "reason": "off_mat", "t": 4.25}]
    assert d.update(frame(4.5, x=0.25), in_set=True) == []
    assert d.update(frame(4.75), in_set=True) == []


def test_held_pose_cannot_complete_arrival_or_off_mat_timer():
    d = PresenceDetector(arrival_s=0.5, off_mat_s=0.5)
    d.update(frame(0), in_set=True)
    d.update(frame(0.25, held=True), in_set=True)
    assert d.update(frame(0.5), in_set=True) == []
    d.update(frame(0.75, x=0.25), in_set=True)
    assert d.update(frame(1.0, x=0.25, held=True), in_set=True) == []
    assert d.update(frame(1.25, x=0.25), in_set=True) == []


def test_floor_exercise_suppresses_timed_presence_events():
    d = PresenceDetector(arrival_s=0.5, off_mat_s=0.5)
    for t in np.arange(0, 1.5, 0.25):
        assert d.update(frame(float(t)), in_set=True, floor_exercise=True) == []
    for t in np.arange(1.5, 3.0, 0.25):
        assert d.update(frame(float(t), x=0.25), in_set=True, floor_exercise=True) == []
    assert not d.arrived and not d.paused_off_mat
