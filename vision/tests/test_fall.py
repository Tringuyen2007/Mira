import numpy as np

from vision.fall import FallDetector
from vision.tests.synth import standing_skeleton
from vision.types import FrameResult


def frame(t, height, phase="idle", lost=False, held=False):
    return FrameResult(t=t, joints=standing_skeleton(), conf=np.ones(33), com=(0.0, height, 0.0),
                       tracking="lost" if lost else "single_cam_B", phase=phase,
                       flags=["pose_held"] if held else [])


def test_staged_fall_needs_fast_drop_then_five_seconds_still():
    d = FallDetector()
    assert d.update(frame(0, 0.9)) == []
    assert d.update(frame(0.25, 0.45)) == [{"type": "fall", "stage": "suspected", "t": 0.25}]
    events = []
    for t in np.arange(0.5, 5.75, 0.25):
        events += d.update(frame(float(t), 0.32))
    assert events == [{"type": "fall", "stage": "confirmed", "t": 5.75 - 0.25}]


def test_slow_lie_down_and_fast_sit_do_not_confirm():
    d = FallDetector()
    for n in range(16):
        assert d.update(frame(n * 0.25, 0.9 - n * 0.04)) == []
    d.reset()
    d.update(frame(0, 0.9))
    assert d.update(frame(0.25, 0.48))[0]["stage"] == "suspected"
    assert d.update(frame(0.5, 0.48)) == []
    for n in range(1, 25):
        assert d.update(frame(0.5 + n * 0.25, 0.48)) == []


def test_floor_exercise_and_untrusted_frames_suppress_falls():
    d = FallDetector()
    d.update(frame(0, 0.9))
    assert d.update(frame(0.25, 0.3, phase="bridge")) == []
    assert d.update(frame(0.5, 0.3)) == []
    d.update(frame(0.75, 0.9))
    assert d.update(frame(1.0, 0.3, held=True)) == []
    assert d.update(frame(1.25, 0.3)) == []
    d.update(frame(1.5, 0.9))
    assert d.update(frame(1.75, 0.3, lost=True)) == []
    assert d.update(frame(2.0, 0.3)) == []


def test_recovery_cancels_suspicion_and_motion_resets_still_timer():
    d = FallDetector(still_s=0.5)
    d.update(frame(0, 0.9))
    d.update(frame(0.25, 0.3))
    d.update(frame(0.5, 0.3))
    assert d.update(frame(0.75, 0.3, phase="bird_dog")) == []
    assert d.stage is None
    assert d.update(frame(1.0, 0.3)) == []


def test_held_pose_and_tracking_loss_interrupt_confirmation_hold():
    d = FallDetector(still_s=0.75)
    d.update(frame(0, 0.9))
    d.update(frame(0.25, 0.3))
    d.update(frame(0.5, 0.3))
    d.update(frame(0.75, 0.3))
    assert d.update(frame(1.0, 0.3, held=True)) == []
    assert d.update(frame(1.25, 0.3)) == []
    assert d.update(frame(1.5, 0.3, lost=True)) == []
    events = []
    for t in (1.75, 2.0, 2.25, 2.5):
        events += d.update(frame(t, 0.3))
    assert events == [{"type": "fall", "stage": "confirmed", "t": 2.5}]


def test_motion_breaks_confirmation_hold():
    d = FallDetector(still_s=0.5)
    d.update(frame(0, 0.9))
    d.update(frame(0.25, 0.3))
    d.update(frame(0.5, 0.3))
    moving = frame(0.75, 0.3)
    moving.com = (0.2, 0.3, 0.0)
    assert d.update(moving) == []
    assert d.update(frame(1.0, 0.3)) == []
    assert d.update(frame(1.25, 0.3)) == []
    assert d.update(frame(1.5, 0.3)) == [{"type": "fall", "stage": "confirmed", "t": 1.5}]
