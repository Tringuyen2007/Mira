"""One code path ends a session: REST stop, the engine's `stopped`, or the last set. Hooks fire exactly once."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server import session_end
from server.state import LiveSession, SessionRegistry


class Live:
    sets_target: int | None = 3


async def _registry_with_hook() -> tuple[SessionRegistry, list[str], list[str]]:
    reg = SessionRegistry()
    sync_calls: list[str] = []
    async_calls: list[str] = []

    async def async_hook(session: LiveSession) -> None:
        async_calls.append(session.session_id)

    reg.on_stop.append(lambda s: sync_calls.append(s.session_id))
    reg.on_stop.append(async_hook)
    return reg, sync_calls, async_calls


async def test_stop_runs_hooks_once_and_is_idempotent() -> None:
    reg, sync_calls, async_calls = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    await reg.stop(session.session_id)
    await reg.stop(session.session_id)  # no-op
    await asyncio.sleep(0)  # let the async hook task run
    assert sync_calls == [session.session_id] and async_calls == [session.session_id]
    assert reg.current is None and reg.is_ended(session.session_id)


async def test_hook_failure_does_not_break_stop() -> None:
    reg = SessionRegistry()
    reg.on_stop.append(lambda s: 1 / 0)
    session = await reg.start("maya", "squat")
    await reg.stop(session.session_id)  # must not raise
    assert reg.current is None


async def test_engine_stopped_event_ends_the_session() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "stopped", "session_id": session.session_id})
    assert sync_calls == [session.session_id] and reg.current is None


async def test_stopped_for_an_old_session_is_ignored() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    old = await reg.start("maya", "squat")
    await reg.stop(old.session_id)
    new = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "stopped", "session_id": old.session_id})
    assert reg.current is new and sync_calls == [old.session_id]


async def test_last_set_done_ends_the_session_when_enabled() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "set_done", "set": 2, "reps": 10, "session_id": session.session_id})
    assert reg.current is session  # not the last set
    await ender.on_event({"type": "set_done", "set": 3, "reps": 10, "session_id": session.session_id})
    assert sync_calls == [session.session_id]
    disabled = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=False)
    again = await reg.start("maya", "squat")
    await disabled.on_event({"type": "set_done", "set": 3, "reps": 10, "session_id": again.session_id})
    assert reg.current is again


async def test_mock_auto_restart_starts_a_new_session_after_delay() -> None:
    reg = SessionRegistry()
    restart = session_end.MockAutoRestart(reg, "maya", "squat", delay_s=0.05)
    reg.on_stop.append(restart.on_stop)
    first = await reg.start("maya", "squat")
    await reg.stop(first.session_id)
    await asyncio.sleep(0.2)
    assert reg.current is not None and reg.current.session_id != first.session_id
    # a session started by someone else in the meantime is left alone
    second = reg.current
    await reg.stop(second.session_id)
    manual = await reg.start("bob", "squat")
    await asyncio.sleep(0.2)
    assert reg.current is manual
    await restart.close()


def test_mock_program_emits_stopped_after_last_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import engine_bridge
    from server.config import cfg

    # cfg is a frozen dataclass; monkeypatch.setattr can't assign to it, so flip the flag directly and
    # restore it in a finally block (mirrors server.config.reload_config's own object.__setattr__ use).
    original_mock_loop = cfg.MOCK_LOOP
    object.__setattr__(cfg, "MOCK_LOOP", False)
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

    src = engine_bridge.MockSource(speed=1e9)
    src._arrive_due_s = 0.0  # arrive immediately
    monkeypatch.setattr(engine_bridge, "MOCK_GREETING_HOLD_S", 0.0)
    monkeypatch.setattr(engine_bridge, "MOCK_GESTURE_AFTER_S", 0.0)
    try:
        src.run(emitted.append, Inbox(), Stop())
    finally:
        object.__setattr__(cfg, "MOCK_LOOP", original_mock_loop)
    kinds = [e["type"] for e in emitted if e["type"] != "frame" and e["type"] != "hr"]
    assert kinds.count("set_done") == 3
    assert kinds.count("stopped") == 1 and kinds.index("stopped") > kinds.index("set_done")
    assert emitted[[e["type"] for e in emitted].index("stopped")]["reason"] == "program_complete"
