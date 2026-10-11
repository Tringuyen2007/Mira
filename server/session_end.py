"""Ends a session from engine events (`stopped`, or the last `set_done`) and, in mock mode, starts the next demo
session after a pause so the LCD keeps looping — every loop is now a real session with a real analysis."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from server.logging_setup import get_logger
from server.state import LiveSession, SessionRegistry

log = get_logger("session_end")

INCIDENT_POLL_S = 2.0  # MockAutoRestart: how often to re-check an open incident before restarting


class SessionEnder:
    """EngineBridge event subscriber. `live` is the bridge's LiveState (for sets_target)."""

    def __init__(self, registry: SessionRegistry, live: Any, *, auto_stop_after_last_set: bool = True) -> None:
        self.registry = registry
        self.live = live
        self.auto_stop_after_last_set = auto_stop_after_last_set

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        sid = ev.get("session_id")
        current = self.registry.current
        if current is None or not isinstance(sid, str) or sid != current.session_id:
            return
        if kind == "stopped":
            log.info("engine stopped the session", extra={"session_id": sid, "reason": ev.get("reason")})
            await self.registry.stop(sid)
        elif kind == "set_done" and self.auto_stop_after_last_set:
            target = getattr(self.live, "sets_target", None)
            done = ev.get("set")
            if isinstance(target, int) and isinstance(done, int) and done >= target:
                log.info("last set done; ending the session", extra={"session_id": sid, "set": done})
                await self.registry.stop(sid)


class MockAutoRestart:
    """on_stop hook for mock mode: start a fresh demo session `delay_s` after one ends, unless something else
    (REST, boot) already started one. While `incident_open()` is True (a Safety Guardian incident is still being
    handled) it keeps waiting, polling every INCIDENT_POLL_S with no limit: a new session must never bury it."""

    def __init__(self, registry: SessionRegistry, patient_id: str, exercise_id: str, delay_s: float,
                 incident_open: Callable[[], bool] = lambda: False) -> None:
        self.registry = registry
        self.patient_id = patient_id
        self.exercise_id = exercise_id
        self.delay_s = delay_s
        self.incident_open = incident_open
        self._task: asyncio.Task[None] | None = None

    def on_stop(self, session: LiveSession) -> None:
        if self.delay_s <= 0:
            return
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.get_running_loop().create_task(self._restart())

    async def _restart(self) -> None:
        await asyncio.sleep(self.delay_s)
        while self.incident_open():
            await asyncio.sleep(INCIDENT_POLL_S)
        started = await self.registry.start_if_idle(self.patient_id, self.exercise_id)
        if started is None:
            return
        log.info("mock auto-restart", extra={"patient_id": self.patient_id})

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
