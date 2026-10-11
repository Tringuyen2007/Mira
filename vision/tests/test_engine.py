import queue
from pathlib import Path

import numpy as np
import pytest

from vision import Engine
from vision.config import CameraConfig
from vision.pipeline import PipelineOutput
from vision.record import Recorder
from vision.physics import PhysicsResult
from vision.tests.synth import standing_skeleton
from vision.types import FrameResult


def frame(t, knee=170, *, raised=False, tracking="single_cam_B", body="ready", held=False):
    joints = standing_skeleton()
    joints[[23, 24], 1] -= max(0, 170 - knee) * .006  # squat depth follows knee bend
    if raised:
        joints[15, 1] = 1.85
    return FrameResult(t, joints, np.ones(33),
                       angles={"left_knee": knee, "right_knee": knee},
                       image_metrics={"knee_mean": 175 - (170 - knee),
                                      "hip_y": 400 + (.95 - joints[[23, 24], 1].mean()) * 200,
                                      "torso_px": 125},
                       tracking=tracking, body=body,
                       flags=["pose_held"] if held else [])


def sequence():
    values = [frame(t) for t in (0, .5, 1, 1.5)]
    values += [frame(t, raised=True) for t in (2, 2.5, 3, 3.5)]
    values += [frame(t) for t in (4, 4.5, 5, 5.5, 6, 6.5)]
    values += [frame(6.6, 170), frame(6.8, 148), frame(7, 142), frame(7.3, 125),
               frame(7.6, 90), frame(7.8, 95), frame(8, 120),
               frame(8.3, 145), frame(8.6, 160)]
    return values


def drain(engine):
    out = []
    while True:
        try:
            out.append(engine.events.get_nowait())
        except queue.Empty:
            return out


def test_synthetic_session_reaches_set_done():
    engine = Engine(source=sequence())
    engine.start("squat", 1, 1, {})
    output = list(engine.frames())
    events = drain(engine)
    assert [e["type"] for e in events] == ["arrived", "awaiting_gesture", "gesture",
                                          "countdown", "countdown", "countdown", "rep", "set_done"]
    assert [e["n"] for e in events if e["type"] == "countdown"] == [3, 2, 1]
    assert events[-2]["quality"] == 100 and events[-2]["index"] == 1
    assert events[-1]["reps"] == 1 and engine.session.state == "done"
    assert output[-1].rep == 1 and output[-1].on_mat


def test_replay_uses_same_downstream_path(tmp_path):
    path = tmp_path / "session.jsonl"
    with Recorder(path, {}, {}) as recorder:
        for item in sequence():
            recorder.write(PipelineOutput(item, {"A": None, "B": None}))
    engine = Engine(replay_path=path, replay_realtime=False)
    engine.start("squat", 1, 1, {})
    assert [f.t for f in engine.frames()][-1] == 8.6
    assert [e["type"] for e in drain(engine)][-2:] == ["rep", "set_done"]


def test_lost_measuring_and_held_interrupt_attempts():
    for kwargs in ({"tracking": "lost"}, {"body": "measuring"}, {"held": True}):
        engine = Engine(source=[])
        engine.start("squat", 1, 1, {})
        engine.session.state = "running"
        engine.process_frame(frame(0, 170))
        engine.process_frame(frame(.2, 140))
        engine.process_frame(frame(.4, 105, **kwargs))
        engine.process_frame(frame(.6, 120))
        engine.process_frame(frame(.8, 157))
        assert engine.counter.rep == 0
        assert not any(e["type"] == "rep" for e in drain(engine))


def test_bridge_suppresses_fall_and_camera_event_once():
    engine = Engine(source=[])
    engine.start("glute_bridge", 1, 1, {})
    engine.session.state = "running"
    engine.physics.apply = lambda f: PhysicsResult(stance="floor")
    before = frame(0)
    before.com = (0, 1, 0)
    engine.process_frame(before, camera_moved=["B"])
    assert "camera_moved" in before.flags and before.angles == {}
    for t, height in ((.2, .3), (.4, .25), (.6, .25), (.8, .25)):
        current = frame(t)
        current.com = (0, height, 0)
        engine.process_frame(current, camera_moved=["B"])
    assert [e["type"] for e in drain(engine)] == ["camera_moved"]


def test_measuring_pose_cannot_trigger_arrival():
    engine = Engine(source=[])
    engine.start("squat", 1, 1, {})
    for t in (0, .5, 1, 1.5, 2):
        engine.process_frame(frame(t, body="measuring"))
    assert engine.session.state == "idle"
    assert drain(engine) == []


def test_second_set_resets_counter_after_rest():
    engine = Engine(source=[])
    engine.start("squat", 1, 2, {})
    engine.session.state = "running"
    for f in sequence()[-9:]:
        engine.process_frame(f)
    assert engine.session.state == "rest" and engine.counter.rep == 0
    engine.process_frame(frame(18.6))
    assert engine.session.state == "countdown"
    engine.process_frame(frame(21.6))
    assert engine.session.state == "running" and engine.counter.rep == 0
    assert [e["type"] for e in drain(engine)][-4:] == ["set_done", "countdown", "countdown", "countdown"]


def test_pause_keeps_completed_set_reps_and_discards_partial_attempt():
    engine = Engine(source=[])
    engine.start("squat", 2, 1, {})
    engine.session.state = "running"
    for i, f in enumerate(sequence()[-9:]):
        engine.process_frame(frame(i * .25, f.angles["left_knee"]))
    assert engine.counter.rep == 1
    engine.process_frame(frame(2.25, 140))
    engine.command("pause", 2.25)
    assert engine.counter.rep == 1 and engine.counter.phase == "idle"
    engine.command("resume", 2.3)
    engine.process_frame(frame(2.5, 157))
    assert engine.counter.rep == 1


def test_off_mat_pause_flows_through_session():
    engine = Engine(source=[])
    engine.start("squat", 1, 1, {})
    engine.session.state = "running"
    engine.process_frame(frame(0))
    for t in (.5, 1, 1.5, 2, 2.5):
        outside = frame(t)
        outside.joints[:, 0] += .25
        engine.process_frame(outside)
    assert engine.session.state == "paused"
    assert {"type": "paused", "reason": "off_mat", "t": 2.5} in drain(engine)


@pytest.mark.parametrize("patient,expected_m", [({"height_cm": 182}, 1.82), ({}, 1.70)])
def test_one_camera_live_pipeline_gets_patient_or_saved_height_without_hardware(
        tmp_path, monkeypatch, patient, expected_m):
    import vision.engine as engine_module

    (tmp_path / "mat.yaml").write_text((Path(__file__).parents[2] / "config" / "mat.yaml").read_text())
    (tmp_path / "person.yaml").write_text("height_cm: 170\n")
    engine = Engine(config_dir=tmp_path)
    engine.cams = {"B": CameraConfig("B", 0)}
    engine.start("squat", 1, 1, patient)
    captured = {}

    class FakeSource:
        def __init__(self, cams, mat):
            captured["cams"] = cams

        def start(self):
            return self

        def wait_ready(self):
            return []

        def poll(self):
            return [type("Pair", (), {"t": 1.0, "a": None, "b": None, "skew_s": None})()]

        def stop(self):
            pass

    class FakePipeline:
        def __init__(self, cams, *, mat, height_m):
            captured["height_m"] = height_m

        def process(self, *args):
            return PipelineOutput(frame(1), {"A": None, "B": None})

    monkeypatch.setattr(engine_module, "LiveSource", FakeSource)
    monkeypatch.setattr(engine_module, "Pipeline", FakePipeline)
    output = engine._live_outputs()
    next(output)
    output.close()
    assert list(captured["cams"]) == ["B"]
    assert captured["height_m"] == pytest.approx(expected_m)
