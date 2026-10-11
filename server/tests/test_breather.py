"""Breather = the HR incident seen from the watch: start (pause + haptic + therapist note), auto-resume when HR stays
under limit - 5 for 10 s before escalation, no auto-resume after escalation, end on stop."""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import engine_bridge
from server.agents.safety_guardian import BREATHER_RESUME_S, SafetyGuardian
from server.state import SessionRegistry, get_state
from server.tests.conftest import TOKEN
from server.tests.test_guardian import FakeBridge, FakeHub, FakeNotifier, ev, until
from server.tests.test_ws_safety import read_until


async def _guardian(timeout_s: float = 60.0) -> tuple[SafetyGuardian, FakeHub, FakeBridge, FakeNotifier, str,
                                                        list[str]]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")  # default profile: hr_limit 130, therapist okafor
    hub, bridge, notifier = FakeHub(), FakeBridge(), FakeNotifier()
    steps: list[str] = []
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, timeout_s,
                              alert_hooks=[lambda inc, step, payload: steps.append(step)])
    return guardian, hub, bridge, notifier, session.session_id, steps


async def _hr(guardian: SafetyGuardian, sid: str, t0: float, seconds: int, bpm: int) -> float:
    """One reading per second for `seconds` seconds starting at t0; returns the next free time."""
    for i in range(seconds):
        await guardian.on_event(ev(sid, "hr", bpm=bpm, t=t0 + i))
    return t0 + seconds


async def _open(guardian: SafetyGuardian, sid: str) -> float:
    return await _hr(guardian, sid, time.time(), 6, 140)  # 0..5 s above 130 → incident at the 6th reading


def _commands(bridge: FakeBridge) -> list[str]:
    return [m["name"] for m in bridge.to_engine if m.get("type") == "command"]


async def test_hr_incident_starts_a_breather() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        await _open(guardian, sid)
        [start] = hub.of("breather")
        assert (start["state"], start["bpm"], start["limit"], start["session_id"]) == ("start", 140, 130, sid)
        assert "breather" in [h["pattern"] for h in hub.of("haptic")]
        assert _commands(bridge) == ["pause"] and bridge.to_engine[0]["payload"] == {"source": "guardian"}
        assert guardian.holding(sid) and steps[:2] == ["detected", "checked_in"]
    finally:
        await guardian.stop()


async def test_recovery_for_10s_resumes_and_resolves_the_incident() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        t = await _open(guardian, sid)
        inc = guardian.incidents[sid]
        await _hr(guardian, sid, t, int(BREATHER_RESUME_S) + 1, 120)  # 120 < 130 - 5 for 10 s
        assert sid not in guardian.incidents and sid not in guardian.breathers and not guardian.holding(sid)
        assert inc.state == "resolved" and "resolved" in steps
        assert _commands(bridge) == ["pause", "start"]
        end = hub.of("breather")[-1]
        assert (end["state"], end["resolution"], end["bpm"], end["limit"]) == ("end", "hr_recovered", 120, 130)
        await until(lambda: inc.timer is not None and inc.timer.done())
    finally:
        await guardian.stop()


async def test_only_a_sustained_drop_below_limit_minus_5_counts() -> None:
    guardian, hub, bridge, _, sid, _ = await _guardian()
    try:
        t = await _open(guardian, sid)
        t = await _hr(guardian, sid, t, 15, 125)  # exactly limit - 5: not below
        t = await _hr(guardian, sid, t, 9, 110)   # 9 s below …
        t = await _hr(guardian, sid, t, 1, 128)   # … interrupted: the clock restarts
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
        await _hr(guardian, sid, t, 11, 110)
        assert sid not in guardian.breathers and _commands(bridge) == ["pause", "start"]
        assert [b["state"] for b in hub.of("breather")] == ["start", "end"]
    finally:
        await guardian.stop()


async def test_after_escalation_there_is_no_auto_resume_until_the_incident_closes() -> None:
    guardian, hub, bridge, notifier, sid, _ = await _guardian(timeout_s=0.05)
    try:
        t = await _open(guardian, sid)
        inc = guardian.incidents[sid]
        await until(lambda: inc.state == "escalated" and inc.escalation is not None)
        await _hr(guardian, sid, t, 15, 100)
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
        await guardian.close(inc)
        end = hub.of("breather")[-1]
        assert (end["state"], end["resolution"]) == ("end", "escalated")
        assert _commands(bridge) == ["pause"]  # the patient resumes herself (watch Resume) once it is closed
        assert not guardian.holding(sid) and len(notifier.escalations) == 1
    finally:
        await guardian.stop()


async def test_im_ok_resolves_the_alert_but_the_breather_holds_until_hr_recovers() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        t = await _open(guardian, sid)
        await guardian.on_event(ev(sid, "command", name="im_ok", payload={}, t=t))
        assert sid not in guardian.incidents and steps[-1] == "resolved"
        assert guardian.holding(sid) and _commands(bridge) == ["pause"]
        await _hr(guardian, sid, t + 1, 11, 110)
        assert not guardian.holding(sid) and _commands(bridge) == ["pause", "start"]
        assert hub.of("breather")[-1]["resolution"] == "hr_recovered"
    finally:
        await guardian.stop()


async def test_hr_recovery_never_resolves_a_different_incident() -> None:
    guardian, _, bridge, _, sid, _ = await _guardian()
    try:
        t = await _open(guardian, sid)
        await guardian.on_event(ev(sid, "command", name="im_ok", payload={}, t=t))
        await guardian.on_event(ev(sid, "command", name="pain", payload={"level": 8}, t=t))
        pain = guardian.incidents[sid]
        await _hr(guardian, sid, t + 1, 12, 110)
        assert guardian.incidents.get(sid) is pain and pain.state == "checking_in"
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
    finally:
        await guardian.stop()


async def test_stopped_event_and_session_stop_end_the_breather() -> None:
    guardian, hub, _, _, sid, _ = await _guardian()
    try:
        await _open(guardian, sid)
        await guardian.on_event(ev(sid, "stopped"))
        assert hub.of("breather")[-1]["resolution"] == "stopped" and sid not in guardian.breathers
        guardian.incidents.clear()
        guardian.hr_over_since.clear()
        await _open(guardian, sid)
        guardian.on_session_stop(guardian.sessions.get(sid))
        assert hub.of("breather")[-1]["resolution"] == "stopped" and sid not in guardian.breathers
    finally:
        await guardian.stop()


async def test_breather_notifies_the_therapist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def create(*args: Any) -> int:
        calls.append(args)
        return 1

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    guardian, _, _, _, sid, _ = await _guardian()
    try:
        await _open(guardian, sid)
        await until(lambda: bool(calls))
        kind, severity, therapist_id, patient_id, title, body, *_rest, dedupe = calls[0]
        assert (kind, severity, therapist_id, patient_id) == ("heart_rate", "high", "okafor", "maya")
        assert "140" in body and "130" in body and dedupe.startswith("breather:")
    finally:
        await guardian.stop()


def test_whole_app_breather_blocks_watch_resume(make_client: Callable[..., TestClient],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_bridge.MockSource, "_heart_rate", lambda self, pose: 150)  # the mock agrees
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    now = time.time()
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        for i in range(6):
            ws.send_json({"type": "hr", "bpm": 150, "t": now + i})
        start = read_until(ws, lambda m: m.get("type") == "breather")
        assert start["state"] == "start" and start["limit"] == 130
        sent: list[dict[str, Any]] = []
        monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert sent == []
