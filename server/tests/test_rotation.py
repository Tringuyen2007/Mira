"""RotationCoach: reps from the watch's rotation_deg (excursion ≥ 60 % of the goal, then back within 15° of zero),
rotation_rep/rotation_set events and rows, rep events for the rest of the system, elbow drift from pose keypoints;
and the mock rotation program (MOCK_WRIST=1) running end to end without a watch."""

from __future__ import annotations

import random
from typing import Any

import pytest

from server import engine_bridge
from server.agents import rotation as rotation_mod
from server.agents.rotation import ELBOW_CUE_ID, RepCounter, RotationCoach, elbow_drift_m
from server.config import reload_config
from server.engine_bridge import LiveState
from server.state import SessionRegistry


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class FakeBridge:
    def __init__(self) -> None:
        self.injected: list[dict[str, Any]] = []

    def inject(self, msg: dict[str, Any]) -> None:
        self.injected.append(msg)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.injected if m["type"] == kind]


async def _coach(exercise_id: str = "forearm_rotation", db: Any = None) -> tuple[RotationCoach, FakeHub, FakeBridge,
                                                                                    str]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", exercise_id)
    hub, bridge = FakeHub(), FakeBridge()
    return RotationCoach(hub, bridge, sessions, db_provider=lambda: db), hub, bridge, session.session_id


def ev(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": sid, "t": fields.pop("t", 1.0), **fields}


async def _turn(coach: RotationCoach, sid: str, peak: float) -> None:
    for deg in (0, 10, peak / 2, peak, peak / 2, 10, 0):
        await coach.on_event(ev(sid, "wrist", rotation_deg=deg))


async def _start_set(coach: RotationCoach, sid: str, set_no: int = 1) -> None:
    for n in (3, 2, 1):
        await coach.on_event(ev(sid, "countdown", n=n, set=set_no, reps_target=10, exercise_id="forearm_rotation"))


# ---- pure parts ---------------------------------------------------------------------------------------------------

def test_rep_counter_hysteresis() -> None:
    counter = RepCounter(80.0)
    assert [counter.update(d) for d in (0, 10, 30, 47, 30, 10)] == [None] * 6  # 47 < 60 % of 80: no rep
    out = [counter.update(d) for d in (0, 20, 60, 82, 40, 16)]
    assert out == [None] * 6  # 16 is still outside the 15° zero zone
    assert counter.update(15) == 82  # back within 15° of zero: one rep at its peak
    assert counter.update(-70) is None and counter.update(-5) == 70  # signed turns count by magnitude


def test_elbow_drift_is_horizontal_distance_from_the_shoulder() -> None:
    joints = [[0.0, 0.0, 0.0] for _ in range(33)]
    joints[12], joints[14] = [-0.19, 1.40, 0.0], [-0.25, 1.13, 0.08]
    assert elbow_drift_m(joints, "right") == pytest.approx(0.1)
    joints[11], joints[13] = [0.19, 1.40, 0.0], [0.19, 1.13, 0.0]
    assert elbow_drift_m(joints, "left") == pytest.approx(0.0)
    assert elbow_drift_m([], "right") is None


# ---- the coach ----------------------------------------------------------------------------------------------------

async def test_reps_sets_and_rep_events() -> None:
    coach, hub, bridge, sid = await _coach()
    await _turn(coach, sid, 70.0)  # before the set: ignored
    await _start_set(coach, sid)
    await _turn(coach, sid, 62.0)
    await _turn(coach, sid, 82.0)
    await _turn(coach, sid, 30.0)  # too small: not a rep
    await coach.on_event(ev(sid, "set_done", set=1))
    reps = hub.of("rotation_rep")
    assert [(r["rep"], r["angle_deg"], r["reached"], r["reps_target"], r["set_index"]) for r in reps] == [
        (1, 62.0, False, 10, 1), (2, 82.0, True, 10, 1)]
    assert reps[0]["goal_deg"] == 80.0 and reps[0]["exercise_id"] == "forearm_rotation"
    [summary] = hub.of("rotation_set")
    assert summary["angles"] == [62.0, 82.0] and summary["best_deg"] == 82.0 and summary["avg_deg"] == 72.0
    assert summary["set_index"] == 1 and summary["goal_deg"] == 80.0
    injected = bridge.of("rep")
    assert [(r["index"], r["set"], r["quality"], r["source"], r["peak_angle"]) for r in injected] == [
        (1, 1, "partial", "watch", 62.0), (2, 1, "good", "watch", 82.0)]


async def test_paused_turns_do_not_count_and_watch_reps_are_ignored() -> None:
    coach, hub, _, sid = await _coach()
    await _start_set(coach, sid)
    await coach.on_event(ev(sid, "paused", reason="hr"))
    await _turn(coach, sid, 80.0)
    await coach.on_event(ev(sid, "resumed"))
    await coach.on_event(ev(sid, "rep", index=9, quality="good", source="watch"))
    await _turn(coach, sid, 80.0)
    assert [r["rep"] for r in hub.of("rotation_rep")] == [1]


async def test_non_watch_exercises_are_ignored() -> None:
    coach, hub, bridge, sid = await _coach("squat")
    await _start_set(coach, sid)
    await _turn(coach, sid, 80.0)
    await coach.on_event(ev(sid, "set_done", set=1))
    assert hub.events == [] and bridge.injected == []


async def test_rotation_set_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    written: list[tuple[Any, ...]] = []

    async def fake(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(rotation_mod.writes, "upsert_rotation_set", fake)
    db = object()
    coach, _, _, sid = await _coach(db=db)

    async def no_rx(*_: Any) -> list[dict[str, Any]]:
        return [{"exercise_id": "forearm_rotation", "reps": 10, "targets": {"rotation_deg": 70}}]

    monkeypatch.setattr(rotation_mod.queries, "ro_prescriptions", no_rx)
    coach._states.clear()
    await _start_set(coach, sid)
    await _turn(coach, sid, 72.0)
    await coach.on_event(ev(sid, "set_done", set=1))
    for _ in range(3):
        await asyncio.sleep(0)
    assert written == [(db, sid, "maya", "forearm_rotation", 1, [72.0], 72.0, 72.0, 70.0)]


def _frame(sid: str, t: float, drift: float) -> dict[str, Any]:
    body = engine_bridge.rotation_frame_body(drift, random.Random(1))
    return {"type": "frame", "session_id": sid, "t": t, **body}


async def test_elbow_drift_cues_once_and_flags_the_next_rep() -> None:
    coach, hub, bridge, sid = await _coach()
    await _start_set(coach, sid)
    t = 100.0
    for i in range(30):  # 1 s at 30 Hz with the elbow 12 cm out
        coach.on_frame(_frame(sid, t + i / 30, 0.12))
    cues = bridge.of("cue")
    assert [(c["id"], c["text"]) for c in cues] == [(ELBOW_CUE_ID, "Keep your elbow at your side")]
    assert [h["pattern"] for h in hub.of("haptic")] == ["fix_form"]
    for i in range(30):  # back at the side, then out again → a second cue
        coach.on_frame(_frame(sid, t + 1 + i / 30, 0.0))
    for i in range(20):
        coach.on_frame(_frame(sid, t + 2 + i / 30, 0.12))
    assert len(bridge.of("cue")) == 2
    await _turn(coach, sid, 80.0)
    await _turn(coach, sid, 80.0)
    reps = bridge.of("rep")
    assert reps[0]["flags"] == [ELBOW_CUE_ID] and reps[1]["flags"] == []


async def test_a_short_lift_does_not_cue() -> None:
    coach, _, bridge, sid = await _coach()
    await _start_set(coach, sid)
    for i in range(12):  # 0.4 s
        coach.on_frame(_frame(sid, 100 + i / 30, 0.12))
    coach.on_frame(_frame(sid, 100.5, 0.0))
    assert bridge.of("cue") == []


# ---- the mock program -------------------------------------------------------------------------------------------

def test_rotation_schedule_has_no_engine_reps() -> None:
    schedule = [e for _, e in engine_bridge._program_schedule("forearm_rotation")]
    assert not [e for e in schedule if e["type"] == "rep"]
    assert [e["set"] for e in schedule if e["type"] == "set_done"] == [1, 2, 3]
    assert {e["exercise_id"] for e in schedule if e["type"] == "countdown"} == {"forearm_rotation"}
    squat = [e for _, e in engine_bridge._program_schedule()]
    assert len([e for e in squat if e["type"] == "rep"]) == 30  # the default program is unchanged


async def test_mock_rotation_program_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_WRIST", "1")
    reload_config()
    emitted: list[dict[str, Any]] = []

    class Inbox:
        def get_nowait(self) -> Any:
            import queue

            raise queue.Empty

    class Stop:
        def __init__(self) -> None:
            self.n = 0

        def is_set(self) -> bool:
            self.n += 1
            return self.n > int((engine_bridge._PROGRAM_S + 40) * engine_bridge.FRAME_HZ)

        def wait(self, timeout: float | None = None) -> bool:
            return False

    src = engine_bridge.MockSource(speed=1e9, exercise_id="forearm_rotation")
    src._arrive_due_s = 0.0
    monkeypatch.setattr(engine_bridge, "MOCK_GREETING_HOLD_S", 0.0)
    monkeypatch.setattr(engine_bridge, "MOCK_GESTURE_AFTER_S", 0.0)
    src.run(emitted.append, Inbox(), Stop())

    assert not [e for e in emitted if e["type"] == "rep"]
    assert [e for e in emitted if e["type"] == "wrist"]
    coach, hub, bridge, sid = await _coach()
    for msg in emitted:
        msg["session_id"] = sid
        if msg["type"] == "frame":
            coach.on_frame(msg)
        else:
            await coach.on_event(msg)
    sets = hub.of("rotation_set")
    assert [s["set_index"] for s in sets] == [1, 2, 3]
    for s in sets:
        assert len(s["angles"]) == 10
        for got, peak in zip(s["angles"], engine_bridge._ROTATION_PEAKS, strict=True):
            assert peak - 3.0 <= got <= peak + 0.05
    assert [c["id"] for c in bridge.of("cue")] == [ELBOW_CUE_ID] * 3
    reps = bridge.of("rep")
    assert len(reps) == 30 and reps[5]["flags"] == [ELBOW_CUE_ID]


def test_live_state_main_angle_follows_the_watch_for_rotation() -> None:
    live = LiveState()
    live.on_session_start("Maya", "forearm_rotation")
    live.on_frame({"type": "frame", "exercise_id": "forearm_rotation", "angles": {"knee_l": 5.0, "knee_r": 6.0}})
    assert live.main_angle is None
    live.on_event({"type": "wrist", "rotation_deg": -41.5})
    assert live.main_angle == 41.5
    live.on_session_start("Maya", "squat")
    live.on_event({"type": "wrist", "rotation_deg": 30.0})
    live.on_frame({"type": "frame", "exercise_id": "squat", "angles": {"knee_l": 50.0, "knee_r": 60.0}})
    assert live.main_angle == 60.0


# ---- the MOCK_WRIST gate: the default demo is unchanged -------------------------------------------------------------

def test_demo_exercise_is_squat_unless_mock_wrist(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.main import _mock_exercise_id

    assert _mock_exercise_id(reload_config()) == "squat"
    monkeypatch.setenv("MOCK_WRIST", "1")
    assert _mock_exercise_id(reload_config()) == "forearm_rotation"


def test_no_mock_wrist_samples_without_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    reload_config()
    emitted: list[dict[str, Any]] = []

    class Inbox:
        def get_nowait(self) -> Any:
            import queue

            raise queue.Empty

    class Stop:
        def __init__(self) -> None:
            self.n = 0

        def is_set(self) -> bool:
            self.n += 1
            return self.n > int(20 * engine_bridge.FRAME_HZ)

        def wait(self, timeout: float | None = None) -> bool:
            return False

    src = engine_bridge.MockSource(speed=1e9, exercise_id="forearm_rotation")
    src._arrive_due_s = 0.0
    monkeypatch.setattr(engine_bridge, "MOCK_GREETING_HOLD_S", 0.0)
    monkeypatch.setattr(engine_bridge, "MOCK_GESTURE_AFTER_S", 0.0)
    src.run(emitted.append, Inbox(), Stop())
    assert not [e for e in emitted if e["type"] in ("wrist", "rep")]
    assert {e["exercise_id"] for e in emitted if e["type"] == "frame"} == {"forearm_rotation"}


def test_partial_watch_reps_have_a_quality_score() -> None:
    from server.db.persist import QUALITY_SCORES

    assert QUALITY_SCORES["partial"] == 0.5 and QUALITY_SCORES["good"] == 1.0
