import numpy as np

from vision.gestures import GestureDetector
from vision.tests.synth import standing_skeleton
from vision.types import FrameResult


def frame(t, gesture=None, lost=False, held=False, body="ready"):
    j = standing_skeleton()
    if gesture == "raised_hand":
        j[15, 1] = 1.85
    if gesture == "crossed_arms":
        j[15] = [-0.13, 1.16, 0.1]
        j[16] = [0.13, 1.16, 0.1]
    return FrameResult(t=t, joints=j, conf=np.ones(33), tracking="lost" if lost else "single_cam_B",
                       flags=["pose_held"] if held else [], body=body)


def test_raised_hand_hold_mid_rep_suppression_and_rearm():
    d = GestureDetector()
    events = []
    for t in np.arange(0, 1.25, 0.25):
        events += d.update(frame(float(t), "raised_hand"), mid_rep=True)
    assert events == []
    for t in np.arange(1.25, 2.5, 0.25):
        events += d.update(frame(float(t), "raised_hand"))
    assert events == [{"type": "gesture", "name": "raised_hand", "t": 2.25}]
    assert d.update(frame(2.5, "raised_hand")) == []
    d.update(frame(2.75))
    for t in np.arange(3, 4.25, 0.25):
        out = d.update(frame(float(t), "raised_hand"))
    assert out == [{"type": "gesture", "name": "raised_hand", "t": 4.0}]


def test_crossed_arms_allowed_mid_rep_but_held_and_lost_do_not_count():
    d = GestureDetector()
    d.update(frame(0, "crossed_arms"), mid_rep=True)
    d.update(frame(0.25, "crossed_arms", held=True), mid_rep=True)
    d.update(frame(0.5, "crossed_arms", lost=True), mid_rep=True)
    events = []
    for t in np.arange(0.75, 2.0, 0.25):
        events += d.update(frame(float(t), "crossed_arms"), mid_rep=True)
    assert events == [{"type": "paused", "reason": "stop_gesture", "t": 1.75}]


def test_low_confidence_or_measuring_pose_is_ignored():
    d = GestureDetector()
    for t in np.arange(0, 2, 0.25):
        assert d.update(frame(float(t), "raised_hand", body="measuring")) == []
    f = frame(2, "raised_hand")
    f.conf[15] = 0.1
    assert d.update(f) == []


def test_floor_exercise_suppresses_gestures():
    d = GestureDetector()
    for t in np.arange(0, 2, 0.25):
        assert d.update(frame(float(t), "crossed_arms"), floor_exercise=True) == []
    assert d.update(frame(2.0, "crossed_arms")) == []
