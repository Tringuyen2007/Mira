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


async def test_mock_auto_restart_waits_while_an_incident_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """An open Safety Guardian incident must survive the session ending: no fresh demo session until it closes."""
    monkeypatch.setattr(session_end, "INCIDENT_POLL_S", 0.02)
    reg = SessionRegistry()
    incident = {"open": True}
    restart = session_end.MockAutoRestart(reg, "maya", "squat", delay_s=0.02, incident_open=lambda: incident["open"])
    reg.on_stop.append(restart.on_stop)
    first = await reg.start("maya", "squat")
    await reg.stop(first.session_id)
    await asyncio.sleep(0.2)
    assert reg.current is None  # still waiting on the incident
    incident["open"] = False
    await asyncio.sleep(0.1)
    assert reg.current is not None and reg.current.session_id != first.session_id
    assert reg.current.origin == "auto"
    await restart.close()


async def test_session_origin_is_recorded() -> None:
    reg = SessionRegistry()
    assert (await reg.start("maya", "squat")).origin == "rest"
    assert (await reg.start("maya", "squat", origin="boot")).origin == "boot"
    await reg.stop(reg.current.session_id)
    started = await reg.start_if_idle("maya", "squat")
    assert started is not None and started.origin == "auto"


async def test_start_if_idle_lets_exactly_one_concurrent_caller_win() -> None:
    reg = SessionRegistry()
    results = await asyncio.gather(*(reg.start_if_idle("maya", "squat") for _ in range(5)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert reg.current is winners[0]


async def test_start_if_idle_returns_none_when_a_session_is_already_current() -> None:
    reg = SessionRegistry()
    session = await reg.start("maya", "squat")
    assert await reg.start_if_idle("maya", "squat") is None
    assert reg.current is session


async def test_stop_prunes_old_ended_sessions() -> None:
    from server.state import MAX_ENDED_SESSIONS

    reg = SessionRegistry()
    first_id = None
    for i in range(MAX_ENDED_SESSIONS + 5):
        session = await reg.start("maya", "squat")
        if i == 0:
            first_id = session.session_id
        await reg.stop(session.session_id)
    assert first_id is not None
    assert len(reg.ended) == MAX_ENDED_SESSIONS
    assert first_id not in reg.ended
    assert reg.get(first_id) is None  # pruned out of _sessions too


def test_mock_loop_disables_last_set_auto_stop_and_auto_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """MOCK_LOOP=1 restores the pre-Plan-2 behaviour: the mock program's own `stopped` never fires, so
    neither SessionEnder's last-set check nor MockAutoRestart should race it."""
    from server.config import cfg
    from server.main import _last_set_enabled, _mock_loops

    original_source, original_loop = cfg.MIRA_SOURCE, cfg.MOCK_LOOP
    try:
        object.__setattr__(cfg, "MIRA_SOURCE", "mock")
        object.__setattr__(cfg, "MOCK_LOOP", True)
        assert _mock_loops(cfg) is True
        assert _last_set_enabled(cfg) is False

        object.__setattr__(cfg, "MOCK_LOOP", False)
        assert _mock_loops(cfg) is False
        assert _last_set_enabled(cfg) is True
    finally:
        object.__setattr__(cfg, "MIRA_SOURCE", original_source)
        object.__setattr__(cfg, "MOCK_LOOP", original_loop)


async def test_set_3_done_does_not_end_the_session_when_last_set_disabled_for_mock_loop() -> None:
    """The actual SessionEnder, constructed the way main.py's lifespan would when MOCK_LOOP=1: a set 3
    `set_done` must not end the session."""
    from server.config import cfg
    from server.main import _last_set_enabled

    original_source, original_loop = cfg.MIRA_SOURCE, cfg.MOCK_LOOP
    try:
        object.__setattr__(cfg, "MIRA_SOURCE", "mock")
        object.__setattr__(cfg, "MOCK_LOOP", True)
        reg = SessionRegistry()
        session = await reg.start("maya", "squat")
        ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=_last_set_enabled(cfg))
        await ender.on_event({"type": "set_done", "set": 3, "reps": 10, "session_id": session.session_id})
        assert reg.current is session
    finally:
        object.__setattr__(cfg, "MIRA_SOURCE", original_source)
        object.__setattr__(cfg, "MOCK_LOOP", original_loop)


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


def test_mock_auto_restart_defaults_to_sixty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.config import cfg, reload_config

    monkeypatch.delenv("MOCK_AUTO_RESTART_S", raising=False)
    reload_config()
    assert cfg.MOCK_AUTO_RESTART_S == 60.0
