"""The remote agent: Engine output -> server wire format, and the command-driven privacy gate."""
import json
import time
from pathlib import Path

import numpy as np

from vision import Engine
from vision.agent import (Agent, SessionRunner, Translator, rep_quality_label, synthetic_outputs,
                          translate_angles, vision_ws_url)
from vision.tests.test_engine import frame, sequence
from vision.types import FrameResult

SAMPLE = Path(__file__).resolve().parents[2] / "server" / "samples" / "engine_frame.json"


def test_angles_become_contract_flexion():
    out = translate_angles({"left_knee": 95.0, "right_knee": 170.0, "left_hip": 100.0, "trunk_lean": -12.4,
                            "trunk_side_lean": 1.2, "pelvis_rotation": 3.0, "knee_to_ankle_gap_ratio": 0.9})
    assert out["knee_l"] == 85.0 and out["knee_r"] == 10.0 and out["hip_l"] == 80.0
    assert out["trunk_lean"] == 12.4 and out["trunk_side"] == 1.2
    assert out["pelvis_rot"] == 3.0 and out["knee_ratio"] == 0.9


def test_rep_quality_label_matches_kiosk_contract():
    assert rep_quality_label(100, []) == "good"
    assert rep_quality_label(79, []) == "fix_form"
    assert rep_quality_label(100, ["knee_valgus"]) == "fix_form"


def test_frame_matches_server_sample_shape():
    sample = json.loads(SAMPLE.read_text())
    tr = Translator("sid-1", "squat", 3, 1)
    fr = frame(10.0, knee=100).to_dict()
    fr["load_l_pct"], fr["heel_l_pct"], fr["heel_r_pct"], fr["com"] = 58.4, 38.9, 43.3, [0.0, 0.75, 0.03]
    (msg,) = tr.frame(fr)
    assert set(sample) <= set(msg)
    assert msg["type"] == "frame" and msg["seq"] == 1 and msg["session_id"] == "sid-1"
    assert msg["set_no"] == 1 and msg["exercise_id"] == "squat"
    assert len(msg["joints"]) == 33 and len(msg["confidence"]) == 33
    assert msg["angles"]["knee_l"] == 80.0
    assert msg["confidence_avg"] == 1.0 and msg["body"] == "ready"


def test_measuring_frames_prompt_stand_straight_at_most_every_5s():
    tr = Translator("sid", "squat", 3, 1)
    fr = frame(0.0, body="measuring").to_dict()
    first = tr.frame(fr)
    assert [m["type"] for m in first] == ["cue", "frame"]
    assert first[0]["id"] == "stand_straight" and "Stand straight" in first[0]["text"]
    assert "measuring" in first[1]["flags"] and first[1]["body"] == "measuring"
    fr["t"] = 2.0
    assert [m["type"] for m in tr.frame(fr)] == ["frame"]
    fr["t"] = 5.0
    assert [m["type"] for m in tr.frame(fr)] == ["cue", "frame"]


def test_events_carry_the_plan_and_set_lifecycle():
    tr = Translator("sid", "squat", 2, 2)
    (arrived,) = tr.event({"type": "arrived", "t": 1.0})
    assert arrived["reps_target"] == 2 and arrived["sets_target"] == 2 and arrived["exercise_id"] == "squat"
    (countdown,) = tr.event({"type": "countdown", "n": 3, "t": 2.0})
    assert countdown["set"] == 1 and countdown["reps_target"] == 2
    (rep,) = tr.event({"type": "rep", "index": 1, "quality": 100, "flags": [], "rom": 70.0, "peak_angle": None,
                       "asym_avg": None, "asym_max": 2.0, "smoothness": None, "duration_s": 2.1, "set": 1, "t": 5.0})
    assert rep["quality"] == "good" and rep["quality_score"] == 100 and rep["set"] == 1
    (done,) = tr.event({"type": "set_done", "set": 1, "reps": 2, "avg_quality": 90.0, "t": 9.0})
    assert done["reps"] == 2 and tr.set_no == 2
    (countdown2,) = tr.event({"type": "countdown", "n": 3, "t": 19.0})
    assert countdown2["set"] == 2
    final = tr.event({"type": "set_done", "set": 2, "reps": 2, "avg_quality": 95.0, "t": 30.0})
    assert [m["type"] for m in final] == ["set_done", "stopped"]
    assert final[1]["reason"] == "program_complete"
    assert tr.stopped(31.0, "stopped") == []  # only once
    (cue,) = tr.event({"type": "cue", "id": "knee_valgus", "t": 32.0})
    assert "knees" in cue["text"] and cue["audio_url"] is None


def test_fall_mapping_and_status_cadence():
    tr = Translator("sid", "squat", 3, 1)
    (possible,) = tr.event({"type": "fall", "stage": "suspected", "t": 10.0})
    assert possible["stage"] == "possible"
    (fall,) = tr.event({"type": "fall", "stage": "confirmed", "t": 15.0})
    assert fall["stage"] == "confirmed" and fall["posture"] == "lying" and fall["session_id"] == "sid"
    lying = frame(15.5).to_dict()
    lying["com"] = [0.0, 0.3, 0.0]
    assert [m["type"] for m in tr.frame(lying)] == ["frame"]
    lying["t"] = 17.0
    msgs = tr.frame(lying)
    assert [m["type"] for m in msgs] == ["frame", "fall_status"]
    assert msgs[1]["posture"] == "lying" and msgs[1]["moving"] is False and msgs[1]["still_s"] > 0
    sitting = dict(lying, t=19.5, com=[0.0, 0.6, 0.3])
    status = tr.frame(sitting)[1]
    assert status["posture"] == "sitting" and status["moving"] is True and status["still_s"] == 0.0
    tr.fall_closed()
    assert [m["type"] for m in tr.frame(dict(sitting, t=22.0))] == ["frame"]


def test_runner_streams_a_synthetic_session_in_wire_order():
    sent = []
    engine = Engine(source=sequence())
    runner = SessionRunner(engine, Translator("sid", "squat", 1, 1), sent.append, exercise_id="squat",
                           reps_target=1, sets_target=1, patient={})
    runner.run()
    kinds = [m["type"] for m in sent if m["type"] != "frame"]
    assert kinds == ["arrived", "awaiting_gesture", "gesture", "countdown", "countdown", "countdown", "rep",
                     "set_done", "stopped"]
    assert all(m["session_id"] == "sid" for m in sent)
    assert [m["seq"] for m in sent] == list(range(1, len(sent) + 1))
    assert sum(m["type"] == "frame" for m in sent) == len(sequence())


def test_privacy_gate_opens_on_start_and_closes_on_stop():
    opened = []

    def factory():
        opened.append(1)
        return Engine(source=iter(lambda: frame(time.time()), None))  # endless frames until stopped

    sent = []
    agent = Agent(factory, sent.append, exercise_id="squat", reps_target=3, sets_target=1)
    agent.handle({"type": "frame", "session_id": "x"})  # noise: not a command
    assert not opened and not agent.running
    agent.handle({"type": "command", "name": "start", "payload": {"source": "ios"}, "session_id": "s1"})
    assert opened == [1] and agent.running and agent.session_id == "s1"
    deadline = time.time() + 2
    while not sent and time.time() < deadline:
        time.sleep(0.01)
    assert sent and sent[0]["type"] == "frame" and sent[0]["session_id"] == "s1"
    agent.handle({"type": "command", "name": "stop", "payload": {"source": "rest"}, "session_id": "s1"})
    assert not agent.running and agent.session_id is None
    assert sent[-1]["type"] == "stopped"
    agent.handle({"type": "command", "name": "start", "payload": {"source": "ios"}, "session_id": "s2"})
    assert opened == [1, 1] and agent.running
    agent.stop_session()


def test_synthetic_source_yields_contract_frames():
    frames = []
    for fr in synthetic_outputs(realtime=False):
        frames.append(fr)
        if len(frames) == 200:
            break
    assert isinstance(frames[0], FrameResult) and frames[0].body == "ready"
    assert frames[0].joints.shape == (33, 3)
    assert np.all(np.diff([f.t for f in frames]) > 0)
    assert frames[50].joints[15, 1] > 1.8  # raised hand between 2 s and 4 s


def test_ws_url_forms():
    assert vision_ws_url("45.76.255.253", "tok") == "wss://45.76.255.253/ws/vision?token=tok"
    assert vision_ws_url("http://localhost:8000/", "tok") == "ws://localhost:8000/ws/vision?token=tok"
    assert vision_ws_url("https://mira.example", "tok") == "wss://mira.example/ws/vision?token=tok"
