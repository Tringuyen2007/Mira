from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from server.engine_bridge import RING_SECONDS, EngineBridge, RingBuffer
from server.hub import Hub

FRAME_KEYS = {"type", "seq", "t", "session_id", "exercise_id", "set_no", "joints", "confidence", "angles", "com",
              "load_l_pct", "heel_l_pct", "heel_r_pct", "confidence_avg", "flags"}
ANGLE_KEYS = {"knee_l", "knee_r", "hip_l", "hip_r", "trunk_lean", "trunk_side", "pelvis_rot", "knee_ratio"}
SESSION = "sess-test-1"


class Collector:
    def __init__(self, bridge: EngineBridge) -> None:
        self.frames: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        bridge.subscribe_frames(self.frames.append)
        bridge.subscribe_events(self._on_event)

    async def _on_event(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def run_bridge() -> AsyncIterator[Callable[..., Any]]:
    bridges: list[EngineBridge] = []

    async def factory(**kwargs: Any) -> tuple[EngineBridge, Collector]:
        bridge = EngineBridge(Hub(), asyncio.get_running_loop(), session_provider=lambda: SESSION, **kwargs)
        collector = Collector(bridge)
        await bridge.start()
        bridges.append(bridge)
        return bridge, collector

    yield factory
    for bridge in bridges:
        await bridge.stop()


async def test_mock_produces_contract_frames_and_events(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=15)
    await wait_until(lambda: len(got.of_type("rep")) >= 5)  # reps end at 6, 9, 12, 15, 18 s

    frame = got.frames[-1]
    assert FRAME_KEYS <= set(frame)
    assert frame["type"] == "frame" and frame["session_id"] == SESSION and frame["exercise_id"] == "squat"
    assert len(frame["joints"]) == 33 and all(len(j) == 3 for j in frame["joints"])
    assert len(frame["confidence"]) == 33
    assert set(frame["angles"]) == ANGLE_KEYS
    assert len(frame["com"]) == 3
    assert isinstance(frame["flags"], list)
    json.dumps(frame)  # JSON types only

    knees = [f["angles"]["knee_l"] for f in got.frames]
    # Flexion convention: standing ~5 degrees, bottom of the squat ~80-90 degrees.
    assert min(knees) < 15 and 70 < max(knees) <= 95, "squat flexion should go from ~5 (standing) to ~70-90"
    assert any("knee_cave_l" in f["flags"] for f in got.frames)

    seqs = [f["seq"] for f in got.frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    all_seqs = sorted(seqs + [e["seq"] for e in got.events])
    assert all_seqs == list(range(all_seqs[0], all_seqs[0] + len(all_seqs)))
    assert bridge.stats()["seq_gaps"] == 0

    assert [e["n"] for e in got.of_type("countdown")][:3] == [3, 2, 1]
    assert got.of_type("cue")[0]["id"] == "set_start"
    assert [e["index"] for e in got.of_type("rep")][:4] == [1, 2, 3, 4]
    assert got.of_type("hr")

    live = bridge.live.payload()
    assert live["phase"] == "active" and live["reps_target"] == 10 and live["sets_target"] == 3
    assert live["rep"] >= 4 and live["main_angle"] is not None


async def test_ring_buffer_bounded_to_60s(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=60)
    await wait_until(lambda: got.frames and got.frames[-1]["t"] - got.frames[0]["t"] > RING_SECONDS + 15, 10)
    ring = bridge.ring_buffer
    assert ring.span_s() <= RING_SECONDS
    assert len(ring) <= RING_SECONDS * 30 + 1
    assert ring.latest() is got.frames[-1]
    recent = ring.frames_since(ring.latest()["t"] - 1.0)
    assert 29 <= len(recent) <= 31


def test_ring_buffer_unit() -> None:
    ring = RingBuffer(seconds=1.0)
    for i in range(100):
        ring.append({"t": i * 0.1})
    assert len(ring) == 11 and ring.span_s() == pytest.approx(1.0)
    assert [f["t"] for f in ring.frames_since(9.75)] == [pytest.approx(9.8), pytest.approx(9.9)]


async def test_mock_fall_script(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock:fall", speed=20, responds_at_s=6)
    await wait_until(lambda: len(got.of_type("fall_status")) >= 4)

    fall = got.of_type("fall")[0]
    assert fall["stage"] == "confirmed" and fall["drop_m"] == 0.9 and fall["peak_down_speed_mps"] == 2.1
    assert fall["posture"] == "unknown" and fall["on_mat"] is True
    assert fall["session_id"] == SESSION and fall["exercise_id"] == "squat" and fall["set"] == 1
    t0 = got.frames[0]["t"]
    assert fall["t"] - t0 == pytest.approx(20, abs=0.1)

    statuses = got.of_type("fall_status")
    gaps = [b["t"] - a["t"] for a, b in zip(statuses, statuses[1:], strict=False)]
    assert all(g == pytest.approx(2.0, abs=0.05) for g in gaps)
    assert statuses[0]["moving"] is False and statuses[0]["still_s"] == 2.0

    gesture = got.of_type("gesture")[0]
    assert gesture["name"] == "thumbs_up" and gesture["t"] - fall["t"] == pytest.approx(6, abs=0.1)
    assert statuses[-1]["moving"] is True
    assert got.frames[-1]["com"][1] < 0.3, "frames show her on the floor"
    assert bridge.live.payload()["phase"] == "incident"

    bridge.send_to_engine({"type": "incident_closed"})
    await asyncio.sleep(0.3)
    count = len(got.of_type("fall_status"))
    await asyncio.sleep(0.5)  # 10 s of simulated time
    assert len(got.of_type("fall_status")) == count
    assert got.of_type("countdown"), "program resumes after the incident closes"


async def test_mock_fall_without_response_never_sends_gesture(run_bridge: Callable[..., Any]) -> None:
    _, got = await run_bridge(source="mock:fall", speed=40, responds_at_s=None)
    await wait_until(lambda: len(got.of_type("fall_status")) >= 10)
    assert not got.of_type("gesture")
    assert all(s["moving"] is False for s in got.of_type("fall_status"))


async def test_trigger_fall_in_mock(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=10)
    await wait_until(lambda: len(got.frames) > 10)
    await bridge.trigger_fall(4)
    await wait_until(lambda: bool(got.of_type("gesture")) and len(got.of_type("fall_status")) >= 3)
    assert got.of_type("fall")[0]["stage"] == "confirmed"


async def test_replay_source_and_loop_side_fall(run_bridge: Callable[..., Any], tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    lines = [json.dumps({"type": "frame", "seq": 900 + i, "t": 1000 + i / 30, "session_id": "old", "joints": [],
                         "confidence": [], "load_l_pct": 50.0, "flags": []}) for i in range(30)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bridge, got = await run_bridge(source=f"replay:{path}", speed=10)
    await wait_until(lambda: len(got.frames) >= 45)
    assert [f["seq"] for f in got.frames[:3]] == [1, 2, 3]
    assert all(f["session_id"] == SESSION for f in got.frames)
    assert got.frames[0]["t"] > time.time() - 60

    await bridge.trigger_fall(2)
    await wait_until(lambda: bool(got.of_type("gesture")) and len(got.of_type("fall_status")) >= 2)
    bridge.send_to_engine({"type": "incident_closed"})
    await asyncio.sleep(0.1)
    count = len(got.of_type("fall_status"))
    await asyncio.sleep(0.4)
    assert len(got.of_type("fall_status")) == count


async def test_inject_stamps_and_routes(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=1)
    bridge.inject({"type": "hr", "bpm": 101})
    await wait_until(lambda: any(e.get("bpm") == 101 for e in got.events))
    ev = next(e for e in got.events if e.get("bpm") == 101)
    assert ev["session_id"] == SESSION and isinstance(ev["seq"], int) and ev["t"] == pytest.approx(time.time(), 2)
    assert bridge.live.hr == 101
    assert bridge.stats()["seq_gaps"] == 0, "injected messages don't count as engine gaps"


async def test_seq_gap_counted(run_bridge: Callable[..., Any]) -> None:
    bridge = EngineBridge(Hub(), asyncio.get_running_loop(), source="mock")
    for seq in (1, 2, 5, 6, 10):
        bridge._on_engine_msg({"type": "fall_status", "seq": seq, "t": 1.0})
    stats = bridge.stats()
    assert stats["seq_gaps"] == 2 and stats["dropped"] == 5


async def test_lag_monitor_reports_numbers(run_bridge: Callable[..., Any]) -> None:
    bridge, _ = await run_bridge(source="mock", speed=1)
    await asyncio.sleep(0.35)
    time.sleep(0.25)  # deliberately block the loop
    await asyncio.sleep(0.25)
    lag = bridge.stats()["event_loop_lag_ms"]
    assert lag["samples"] >= 3
    assert lag["p50"] is not None and lag["p50"] >= 0
    assert lag["max"] >= 100


async def test_process_mode(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", mode="process", speed=10)
    await wait_until(lambda: len(got.frames) >= 30, timeout=20)
    assert FRAME_KEYS <= set(got.frames[-1])
    await bridge.trigger_fall(None)
    await wait_until(lambda: len(got.of_type("fall_status")) >= 2, timeout=10)
    bridge.send_to_engine({"type": "incident_closed"})
    await asyncio.sleep(0.3)
    count = len(got.of_type("fall_status"))
    await asyncio.sleep(0.5)
    assert len(got.of_type("fall_status")) == count
    assert bridge.stats()["mode"] == "process"


async def test_mock_emits_arrived_once_before_the_program(run_bridge: Callable[..., Any]) -> None:
    _, got = await run_bridge(source="mock", speed=15)
    await wait_until(lambda: len(got.of_type("countdown")) >= 1)
    arrived = got.of_type("arrived")
    program = [e["type"] for e in got.events if e["type"] != "hr"]
    assert len(arrived) == 1 and program[0] == "arrived", "the squat program starts after the patient arrives"
    assert arrived[0]["session_id"] == SESSION and arrived[0]["patient_id"] == "maya"


async def test_notify_session_start_restarts_mock_program(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=15)
    await wait_until(lambda: len(got.of_type("arrived")) == 1)
    assert got.of_type("arrived")[0]["patient_id"] == "maya"
    reps_before = len(got.of_type("rep"))

    bridge.notify_session_start("liam")
    await wait_until(lambda: len(got.of_type("arrived")) == 2)
    second = got.of_type("arrived")[1]
    assert second["patient_id"] == "liam" and second["session_id"] == SESSION

    # The squat program restarted from the top: a fresh countdown/rep sequence follows the new arrival.
    arrived_seq = second["seq"]
    await wait_until(lambda: any(e["seq"] > arrived_seq for e in got.of_type("rep")))
    reps_after_restart = [e for e in got.of_type("rep") if e["seq"] > arrived_seq]
    assert reps_after_restart[0]["index"] == 1 and reps_after_restart[0]["set"] == 1
    assert len(got.of_type("rep")) >= reps_before  # the program kept running (no gap/crash)


async def test_notify_session_start_noop_for_live_and_replay(run_bridge: Callable[..., Any],
                                                              tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    path.write_text(json.dumps({"type": "frame", "seq": 1, "t": 0.0, "joints": [], "confidence": [],
                                "load_l_pct": 0.0, "flags": []}) + "\n", encoding="utf-8")
    bridge, got = await run_bridge(source=f"replay:{path}", speed=10)
    bridge.notify_session_start("liam")  # must not raise; replay has no reverse-channel handler for this
    await wait_until(lambda: len(got.frames) >= 3)
    assert not got.of_type("arrived")


async def test_mock_fall_still_scripted_after_session_restart(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock:fall", speed=20, responds_at_s=None)
    await wait_until(lambda: len(got.of_type("arrived")) == 1)

    bridge.notify_session_start("liam")
    await wait_until(lambda: len(got.of_type("arrived")) == 2)

    await bridge.trigger_fall(None)
    await wait_until(lambda: bool(got.of_type("fall")) and len(got.of_type("fall_status")) >= 2)
    assert got.of_type("fall")[0]["stage"] == "confirmed"


async def test_mock_user_pause_freezes_program_then_start_resumes(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=20)
    await wait_until(lambda: len(got.of_type("rep")) >= 2)
    reps_at_pause = len(got.of_type("rep"))

    bridge.send_to_engine({"type": "command", "name": "pause", "payload": {}})
    await wait_until(lambda: bool(got.of_type("paused")))
    assert got.of_type("paused")[-1]["reason"] == "user"
    assert bridge.live.payload()["paused"] is True

    await asyncio.sleep(0.3)  # time passes (simulated ~6s at speed=20) but the program must not advance
    assert len(got.of_type("rep")) == reps_at_pause
    assert got.frames[-1]["angles"]["knee_l"] < 10, "standing still while paused"
    assert len(got.of_type("paused")) == 1, "pause is idempotent while already paused"

    bridge.send_to_engine({"type": "command", "name": "start", "payload": {}})
    await wait_until(lambda: bool(got.of_type("resumed")))
    assert bridge.live.payload()["paused"] is False
    await wait_until(lambda: len(got.of_type("rep")) > reps_at_pause)


async def test_mock_user_skip_ends_the_set_and_moves_to_the_next(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=20)
    await wait_until(lambda: len(got.of_type("rep")) >= 2)

    bridge.send_to_engine({"type": "command", "name": "skip", "payload": {}})
    await wait_until(lambda: bool(got.of_type("set_done")))
    done = got.of_type("set_done")[-1]
    assert done["set"] == 1 and done["exercise_id"] == "squat"
    assert 0 < done["reps"] < 10, "a partial set: fewer reps than the full target"

    await wait_until(lambda: len(got.of_type("countdown")) >= 4)  # 3 countdown ticks for set 1, then set 2's
    assert got.of_type("countdown")[-1]["set"] == 2, "skip moves straight into the next set's countdown"


async def test_mock_user_stop_ends_the_program(run_bridge: Callable[..., Any]) -> None:
    bridge, got = await run_bridge(source="mock", speed=20)
    await wait_until(lambda: len(got.of_type("rep")) >= 2)

    bridge.send_to_engine({"type": "command", "name": "stop", "payload": {}})
    await wait_until(lambda: bool(got.of_type("stopped")))
    assert bridge.live.payload()["phase"] == "done"

    rep_count = len(got.of_type("rep"))
    await asyncio.sleep(0.3)
    assert len(got.of_type("rep")) == rep_count, "no more reps once stopped"
    assert got.frames[-1]["angles"]["knee_l"] < 10, "standing still once stopped"

    # a later start/pause/skip has no effect: the program is over for this session
    bridge.send_to_engine({"type": "command", "name": "start", "payload": {}})
    await asyncio.sleep(0.2)
    assert not got.of_type("resumed")
    assert bridge.live.payload()["phase"] == "done"


async def test_event_enricher_runs_before_hub_and_subscribers(run_bridge: Callable[..., Any]) -> None:
    published: list[dict[str, Any]] = []
    bridge = EngineBridge(Hub(), asyncio.get_running_loop(), source="mock", session_provider=lambda: SESSION)
    bridge.hub.publish = lambda channel, msg: published.append(dict(msg)) if channel == "events" else None
    got = Collector(bridge)

    def broken(msg: dict[str, Any]) -> None:
        raise RuntimeError("enricher bug")

    def add_text(msg: dict[str, Any]) -> None:
        if msg["type"] == "cue":
            msg["text"] = "enriched"

    bridge.add_event_enricher(broken)
    bridge.add_event_enricher(add_text)
    await bridge.start()
    try:
        bridge.inject({"type": "cue", "id": "knees_out"})
        await wait_until(lambda: bool(got.of_type("cue")))
    finally:
        await bridge.stop()
    assert [m["text"] for m in published if m["type"] == "cue"] == ["enriched"]
    assert got.of_type("cue")[0]["text"] == "enriched"
    assert bridge.stats()["subscriber_errors"] >= 1
