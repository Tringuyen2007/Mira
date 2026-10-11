"""RecoveryAgent: peak HR over a set (countdown → set_done), then 60 s later — or at the next set's countdown,
whichever comes first — the latest reading: one hr_recovery event and one hr_recovery row per set."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server.agents import recovery as recovery_mod
from server.agents.recovery import RecoveryAgent
from server.state import SessionRegistry


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


async def _setup(recovery_s: float, db: Any = None) -> tuple[RecoveryAgent, FakeHub, str]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    hub = FakeHub()
    return RecoveryAgent(hub, sessions, db_provider=lambda: db, recovery_s=recovery_s), hub, session.session_id


def ev(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": sid, "t": 1.0, **fields}


async def _set(agent: RecoveryAgent, sid: str, set_no: int, bpms: list[int]) -> None:
    for n in (3, 2, 1):
        await agent.on_event(ev(sid, "countdown", n=n, set=set_no))
    for bpm in bpms:
        await agent.on_event(ev(sid, "hr", bpm=bpm))
    await agent.on_event(ev(sid, "set_done", set=set_no))


async def test_recovery_after_the_delay_uses_the_latest_reading() -> None:
    agent, hub, sid = await _setup(0.05)
    try:
        await agent.on_event(ev(sid, "hr", bpm=150))  # before the set: not part of the peak
        await _set(agent, sid, 1, [110, 126, 120])
        await agent.on_event(ev(sid, "hr", bpm=112))
        await agent.on_event(ev(sid, "hr", bpm=104))
        assert hub.of("hr_recovery") == []
        await asyncio.sleep(0.15)
        [rec] = hub.of("hr_recovery")
        assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (1, 126, 104, 22)
        assert rec["session_id"] == sid
    finally:
        await agent.close()


async def test_next_set_start_comes_first() -> None:
    agent, hub, sid = await _setup(60.0)
    try:
        await _set(agent, sid, 1, [118, 124])
        await agent.on_event(ev(sid, "hr", bpm=108))
        await agent.on_event(ev(sid, "countdown", n=3, set=2))
        [rec] = hub.of("hr_recovery")
        assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (1, 124, 108, 16)
        await agent.on_event(ev(sid, "hr", bpm=130))  # set 2 is now running
        await agent.on_event(ev(sid, "set_done", set=2))
        await agent.on_event(ev(sid, "countdown", n=3, set=3))
        assert [r["set_index"] for r in hub.of("hr_recovery")] == [1, 2]
        assert hub.of("hr_recovery")[1]["peak_bpm"] == 130
    finally:
        await agent.close()


async def test_no_heart_rate_during_the_set_means_no_recovery() -> None:
    agent, hub, sid = await _setup(0.01)
    try:
        await _set(agent, sid, 1, [])
        await agent.on_event(ev(sid, "hr", bpm=100))
        await asyncio.sleep(0.05)
        assert hub.of("hr_recovery") == []
    finally:
        await agent.close()


async def test_recovery_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[Any, ...]] = []

    async def fake_upsert(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(recovery_mod.writes, "upsert_hr_recovery", fake_upsert)
    db = object()
    agent, hub, sid = await _setup(60.0, db=db)
    try:
        await _set(agent, sid, 1, [126])
        await agent.on_event(ev(sid, "hr", bpm=104))
        await agent.on_event(ev(sid, "countdown", n=3, set=2))
        for _ in range(3):
            await asyncio.sleep(0)
        assert written == [(db, sid, "maya", 1, 126, 104, 22)]
    finally:
        await agent.close()


async def test_close_cancels_the_pending_timer() -> None:
    agent, hub, sid = await _setup(0.05)
    await _set(agent, sid, 1, [120])
    await agent.close()
    await asyncio.sleep(0.1)
    assert hub.of("hr_recovery") == []
