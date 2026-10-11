from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import pytest

from server.agents.safety_guardian import Incident, SafetyGuardian
from server.engine_bridge import RingBuffer, lying_frame_body
from server.state import SessionRegistry

TIMEOUT_S = 0.05
INTERVAL_S = 0.3


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class FakeBridge:
    def __init__(self) -> None:
        self.ring_buffer = RingBuffer()
        self.injected: list[dict[str, Any]] = []
        self.to_engine: list[dict[str, Any]] = []

    def inject(self, msg: dict[str, Any]) -> None:
        self.injected.append(msg)

    def send_to_engine(self, msg: dict[str, Any]) -> None:
        self.to_engine.append(msg)


class FakeNotifier:
    """Records calls; `ongoing` decides whether escalation puts the incident on a live call."""

    def __init__(self, ongoing: bool = True, call: bool = True) -> None:
        self.ongoing = ongoing
        self.call = call
        self.escalations: list[Incident] = []
        self.pushes: list[tuple[str, str, float]] = []
        self.guardian: SafetyGuardian | None = None

    def attach(self, guardian: SafetyGuardian) -> None:
        self.guardian = guardian

    async def escalate(self, inc: Incident) -> dict[str, Any]:
        self.escalations.append(inc)
        if not self.call:
            return {"mode": "test", "error": "no_contact_phone"}
        inc.call_id = f"call-{len(self.escalations)}"
        inc.call_state = "ongoing" if self.ongoing else "registered"
        return {"mode": "test", "contact_call_id": inc.call_id}

    async def push_update(self, inc: Incident, text: str, status: str) -> bool:
        if inc.call_state != "ongoing":
            return False
        self.pushes.append((text, status, time.monotonic()))
        return True


@pytest.fixture
async def setup() -> Any:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    hub, bridge, notifier = FakeHub(), FakeBridge(), FakeNotifier()
    steps: list[tuple[str, str]] = []
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, TIMEOUT_S, update_interval_s=INTERVAL_S,
                              alert_hooks=[lambda inc, step, payload: steps.append((inc.alert_id, step))])
    yield guardian, hub, bridge, notifier, session.session_id, steps
    await guardian.stop()


def ev(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": sid, "t": fields.pop("t", time.time()), **fields}


def fall(sid: str) -> dict[str, Any]:
    return ev(sid, "fall", stage="confirmed", drop_m=0.9, posture="unknown", on_mat=True, exercise_id="squat",
              set=1, rep=6)


async def until(cond: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def step_names(steps: list[tuple[str, str]]) -> list[str]:
    return [s for _, s in steps]


async def escalated(guardian: SafetyGuardian, sid: str, notifier: FakeNotifier) -> Incident:
    await guardian.on_event(fall(sid))
    await until(lambda: guardian.incidents.get(sid) is not None and guardian.incidents[sid].escalation is not None)
    return guardian.incidents[sid]


async def test_fall_checks_in_and_a_response_resolves_and_cancels_the_timer(setup: Any) -> None:
    guardian, hub, bridge, notifier, sid, steps = setup
    await guardian.on_event(fall(sid))
    inc = guardian.incidents[sid]
    assert inc.state == "checking_in" and step_names(steps) == ["detected", "checked_in"]
    assert hub.of("cue")[0]["id"] == "check_in" and hub.of("haptic")[0]["pattern"] == "check_in"
    alert = hub.of("alert")[-1]
    assert alert["step"] == "checked_in" and alert["alert_id"] == inc.alert_id and alert["show_call_911"] is False
    assert alert["message"] == "Fall detected. Asking Maya if she is OK."

    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))
    assert inc.state == "resolved" and sid not in guardian.incidents
    await asyncio.sleep(TIMEOUT_S * 3)
    assert inc.timer is not None and inc.timer.cancelled()
    assert notifier.escalations == []
    assert step_names(steps) == ["detected", "checked_in", "resolved"]
    assert bridge.to_engine == [{"type": "incident_closed"}]


async def test_timeout_escalates_exactly_once_even_with_duplicate_falls(setup: Any) -> None:
    guardian, hub, _, notifier, sid, steps = setup
    await guardian.on_event(fall(sid))
    await guardian.on_event(fall(sid))
    inc = await escalated(guardian, sid, notifier)
    await guardian.on_event(fall(sid))
    await asyncio.sleep(TIMEOUT_S * 3)
    assert len(notifier.escalations) == 1 and len(guardian.incidents) == 1
    assert step_names(steps).count("detected") == 1 and step_names(steps).count("escalated") == 1
    assert inc.state == "escalated" and inc.snapshot["kind"] == "fall"
    assert hub.of("alert")[-1]["show_call_911"] is True


async def test_thumbs_up_after_escalation_pushes_one_update_and_keeps_the_incident(setup: Any) -> None:
    guardian, _, bridge, notifier, sid, steps = setup
    inc = await escalated(guardian, sid, notifier)
    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))
    # the engine's status after the response: moving, still on the mat — not separate news
    await guardian.on_event(ev(sid, "fall_status", posture="unknown", moving=True, still_s=0.0, on_mat=True))
    await asyncio.sleep(INTERVAL_S * 1.5)
    assert guardian.incidents.get(sid) is inc and inc.state == "escalated"
    assert len(notifier.escalations) == 1
    assert [p[0] for p in notifier.pushes] == ["Maya just gave a thumbs up."]
    assert notifier.pushes[0][1].startswith("Responded.")
    assert "patient_responded" in step_names(steps)
    assert bridge.to_engine == []


async def test_live_updates_are_throttled_merged_and_never_repeated(setup: Any) -> None:
    guardian, _, _, notifier, sid, _ = setup
    inc = await escalated(guardian, sid, notifier)
    await guardian.on_event(ev(sid, "hr", bpm=150))  # baseline is None → first reading sets it
    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))
    assert notifier.pushes == []  # event intake never waits on the network: pushes run in the background
    await until(lambda: len(notifier.pushes) == 1)
    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))  # same fact: never twice
    await guardian.on_event(ev(sid, "fall_status", posture="sitting", moving=True, still_s=0.0, on_mat=True))
    await guardian.on_event(ev(sid, "command", name="im_ok", payload={}))
    await asyncio.sleep(INTERVAL_S / 3)
    assert len(notifier.pushes) == 1  # thumbs up went out; the rest waits for the throttle
    await until(lambda: len(notifier.pushes) == 2)
    await asyncio.sleep(INTERVAL_S * 1.5)
    assert len(notifier.pushes) == 2
    first, second = notifier.pushes
    assert first[0] == "Maya just gave a thumbs up."
    assert second[0] == "Maya is now sitting up on the mat. Maya just pressed I'm OK on her phone."
    assert second[2] - first[2] >= INTERVAL_S - 0.01
    assert inc.last_pushed == {"responded": "im_ok", "posture": "sitting"}


async def test_status_changes_while_still_unresponsive_become_updates(setup: Any) -> None:
    guardian, _, _, notifier, sid, _ = setup
    await escalated(guardian, sid, notifier)  # no frames yet: position unknown at escalation
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=2.0, on_mat=True))
    await until(lambda: len(notifier.pushes) == 1)
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=33.0, on_mat=True))
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=35.0, on_mat=True))
    await until(lambda: len(notifier.pushes) == 2)
    assert [p[0] for p in notifier.pushes] == ["Maya is now lying down on the mat.",
                                               "Maya is still not moving and has not responded."]
    assert notifier.pushes[1][1] == "Still lying down on the mat, not moving. No response yet."
    await guardian.on_event(ev(sid, "hr", bpm=140))  # sets the baseline
    await guardian.on_event(ev(sid, "hr", bpm=128))  # within 15 bpm: no news
    await guardian.on_event(ev(sid, "hr", bpm=160))  # +20: news
    await until(lambda: len(notifier.pushes) == 3)
    assert notifier.pushes[2][0] == "Her heart rate is now 160."


async def test_a_fact_that_changes_back_is_pushed_again(setup: Any) -> None:
    """lying → sitting → lying: the caller must hear she is lying down again (F3-#1); HR 131 → 115 → 131 too."""
    guardian, _, _, notifier, sid, _ = setup
    inc = await escalated(guardian, sid, notifier)
    for n, posture in enumerate(("lying", "sitting", "lying"), start=1):
        await guardian.on_event(ev(sid, "fall_status", posture=posture, moving=False, still_s=1.0, on_mat=True))
        await until(lambda n=n: len(notifier.pushes) == n)
    assert [p[0] for p in notifier.pushes] == ["Maya is now lying down on the mat.",
                                               "Maya is now sitting up on the mat.",
                                               "Maya is now lying down on the mat."]
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=2.0, on_mat=True))
    for bpm in (131, 115, 131):  # 131 sets the baseline; each later change is > 15 bpm
        await guardian.on_event(ev(sid, "hr", bpm=bpm))
        await asyncio.sleep(INTERVAL_S * 1.2)
    assert [p[0] for p in notifier.pushes[3:]] == ["Her heart rate is now 115.", "Her heart rate is now 131."]
    assert inc.last_pushed["posture"] == "lying" and inc.last_pushed["hr"] == "131"


async def test_a_newer_state_replaces_a_queued_one(setup: Any) -> None:
    """Within one throttle window, sitting then lying again (already pushed) leaves nothing to push."""
    guardian, _, _, notifier, sid, _ = setup
    await escalated(guardian, sid, notifier)
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=1.0, on_mat=True))
    await until(lambda: len(notifier.pushes) == 1)
    await guardian.on_event(ev(sid, "fall_status", posture="sitting", moving=False, still_s=1.0, on_mat=True))
    await guardian.on_event(ev(sid, "fall_status", posture="lying", moving=False, still_s=1.0, on_mat=True))
    await asyncio.sleep(INTERVAL_S * 1.5)
    assert [p[0] for p in notifier.pushes] == ["Maya is now lying down on the mat."]


async def test_updates_wait_until_the_call_is_ongoing(setup: Any) -> None:
    guardian, _, _, notifier, sid, _ = setup
    notifier.ongoing = False
    inc = await escalated(guardian, sid, notifier)
    await guardian.on_event(ev(sid, "gesture", name="raised_hand"))
    assert notifier.pushes == [] and inc.pending
    inc.call_state = "ongoing"
    guardian.flush_updates(inc)  # the notifier does this when the call connects
    await until(lambda: len(notifier.pushes) == 1)
    assert [p[0] for p in notifier.pushes] == ["Maya just raised her hand."]


async def test_response_after_a_failed_escalation_closes_the_incident(setup: Any) -> None:
    guardian, _, bridge, notifier, sid, steps = setup
    notifier.call = False
    inc = await escalated(guardian, sid, notifier)
    await guardian.on_event(ev(sid, "command", name="im_ok", payload={}))
    assert inc.state == "closed" and sid not in guardian.incidents
    assert step_names(steps)[-2:] == ["patient_responded", "closed"]
    assert bridge.to_engine == [{"type": "incident_closed"}]


async def test_close_removes_the_incident_and_tells_the_engine(setup: Any) -> None:
    guardian, hub, bridge, notifier, sid, steps = setup
    inc = await escalated(guardian, sid, notifier)
    await guardian.close(inc)
    await guardian.close(inc)  # idempotent
    assert sid not in guardian.incidents and inc.state == "closed"
    assert step_names(steps).count("closed") == 1
    assert bridge.to_engine == [{"type": "incident_closed"}]
    assert hub.of("alert")[-1]["show_call_911"] is False
    await guardian.on_event(fall(sid))  # a later fall opens a new incident
    assert guardian.incidents[sid] is not inc


async def test_hr_over_limit_for_2s_does_nothing_for_5s_opens_an_incident(setup: Any) -> None:
    guardian, _, bridge, _, sid, steps = setup
    t0 = time.time()
    for i in range(3):  # 0, 1, 2 s above the 130 limit
        await guardian.on_event(ev(sid, "hr", bpm=140, t=t0 + i))
    await guardian.on_event(ev(sid, "hr", bpm=120, t=t0 + 3))  # back under: the clock restarts
    for i in range(4, 9):  # 4..8 s: only 4 s above
        await guardian.on_event(ev(sid, "hr", bpm=140, t=t0 + i))
    assert guardian.incidents == {} and steps == []
    await guardian.on_event(ev(sid, "hr", bpm=141, t=t0 + 9))  # 5 s above
    inc = guardian.incidents[sid]
    assert inc.kind == "hr" and inc.hr_now == 141
    assert bridge.injected == [{"type": "paused", "reason": "hr", "session_id": sid}]


async def test_pain_7_or_more_pauses_and_opens_an_incident(setup: Any) -> None:
    guardian, _, bridge, _, sid, _ = setup
    await guardian.on_event(ev(sid, "command", name="pain", payload={"level": 5}))
    assert guardian.incidents == {}
    await guardian.on_event(ev(sid, "command", name="pain", payload={"level": 8}))
    inc = guardian.incidents[sid]
    assert inc.kind == "pain" and inc.pain_level == 8
    assert bridge.injected[0]["type"] == "paused" and bridge.injected[0]["reason"] == "pain"


async def test_snapshot_uses_ring_buffer_and_cached_profile(setup: Any) -> None:
    guardian, _, bridge, notifier, sid, _ = setup
    rng = random.Random(1)
    now = time.time()
    for i in range(60):
        bridge.ring_buffer.append({"t": now + i / 30, **lying_frame_body(False, rng)})
    inc = await escalated(guardian, sid, notifier)
    assert inc.snapshot["posture"] == "lying" and inc.snapshot["posture_estimated"] is True
    assert inc.snapshot["first_name"] == "Maya"


async def test_hooks_get_every_step_and_a_failing_hook_is_isolated(setup: Any) -> None:
    guardian, _, _, _, sid, steps = setup
    seen: list[str] = []

    async def async_hook(inc: Incident, step: str, payload: dict[str, Any] | None) -> None:
        seen.append(step)

    def bad_hook(inc: Incident, step: str, payload: dict[str, Any] | None) -> None:
        raise RuntimeError("boom")

    guardian.alert_hooks[:0] = [bad_hook, async_hook]
    await guardian.on_event(fall(sid))
    await asyncio.sleep(0)
    assert seen == ["detected", "checked_in"] and step_names(steps) == ["detected", "checked_in"]


async def test_events_without_a_session_still_use_a_profile(setup: Any) -> None:
    guardian, hub, _, _, _, _ = setup
    guardian.sessions.current = None
    await guardian.on_event({"type": "fall", "stage": "confirmed", "t": time.time(), "session_id": None})
    assert guardian.incidents[""].first_name == "Maya"
    assert hub.of("alert")[-1]["patient_id"] == "maya"


async def test_failed_escalation_without_a_response_still_closes_after_the_hold(setup: Any) -> None:
    # Regression: with no call placed, nothing ever closed the incident, so later falls were ignored forever.
    guardian, _, bridge, notifier, sid, steps = setup
    guardian.no_call_hold_s = 0.1
    notifier.call = False
    inc = await escalated(guardian, sid, notifier)
    assert inc.state == "escalated"
    await until(lambda: sid not in guardian.incidents)
    assert inc.state == "closed" and step_names(steps)[-1] == "closed"
    assert bridge.to_engine == [{"type": "incident_closed"}]
    await guardian.on_event(fall(sid))
    assert guardian.incidents[sid] is not inc


async def test_a_response_during_a_failed_escalation_closes_right_away(setup: Any) -> None:
    guardian, _, _, notifier, sid, steps = setup
    notifier.call = False
    gate = asyncio.Event()
    original = notifier.escalate

    async def slow_escalate(inc: Incident) -> dict[str, Any]:
        await gate.wait()
        return await original(inc)

    notifier.escalate = slow_escalate  # type: ignore[method-assign]
    await guardian.on_event(fall(sid))
    inc = guardian.incidents[sid]
    await until(lambda: inc.state == "escalated")
    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))  # while the call is still being placed
    gate.set()
    await until(lambda: inc.state == "closed")
    assert step_names(steps)[-3:] == ["patient_responded", "escalated", "closed"]


async def test_a_hanging_update_never_holds_up_event_intake(setup: Any) -> None:
    # Regression: push_update (update_live over the network) used to be awaited inside on_event.
    guardian, _, _, notifier, sid, _ = setup
    inc = await escalated(guardian, sid, notifier)
    hang = asyncio.Event()

    async def hanging_push(inc: Incident, text: str, status: str) -> bool:
        await hang.wait()
        return True

    notifier.push_update = hanging_push  # type: ignore[method-assign]
    await asyncio.wait_for(guardian.on_event(ev(sid, "gesture", name="thumbs_up")), 0.5)
    await asyncio.wait_for(guardian.on_event(ev(sid, "hr", bpm=170)), 0.5)
    assert inc.hr_now == 170
    hang.set()


async def test_client_times_in_milliseconds_fall_back_to_the_server_clock(setup: Any) -> None:
    guardian, _, _, _, sid, _ = setup
    await guardian.on_event(ev(sid, "command", name="pain", payload={"level": 9}, t=time.time() * 1000))
    inc = guardian.incidents[sid]
    assert abs(inc.started_t - time.time()) < 5


async def test_an_estimated_posture_change_is_said_as_an_estimate(setup: Any) -> None:
    guardian, _, bridge, notifier, sid, _ = setup
    await escalated(guardian, sid, notifier)  # no frames: position unknown at escalation
    rng = random.Random(2)
    now = time.time()
    for i in range(30):
        bridge.ring_buffer.append({"t": now + i / 30, **lying_frame_body(False, rng)})
    await guardian.on_event(ev(sid, "fall_status", posture="unknown", moving=False, still_s=3.0, on_mat=True))
    await until(lambda: len(notifier.pushes) == 1)
    assert notifier.pushes[0][0] == "Maya appears to be lying down on the mat now."
