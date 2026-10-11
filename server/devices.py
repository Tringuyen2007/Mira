"""LCD kiosk presence: is a device-scoped client connected and reporting `device_health`?

Online = at least one device-token `/ws/events` socket and a `device_health` newer than DEVICE_STALE_S.
Every online/offline transition is emitted as `device_status` on /ws/events, so the iOS app and the agents
can show or react to "Mira's screen is offline".
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Protocol

from server.logging_setup import get_logger

log = get_logger("devices")

DEVICE_STALE_S = 15.0  # the page sends device_health every 5 s; three misses = offline
DEVICE_CHECK_S = 5.0
DEVICE_NAME = "lcd"
# What a device-scoped client may send (only device_health) and which of its keys survive.
DEVICE_HEALTH_KEYS = frozenset({"type", "t", "app", "screen", "version", "battery_pct"})
# Event types delivered to a device-scoped /ws/events client: what the LCD renders, nothing else
# (no proposals, coach_adapt, notifications or engine diagnostics).
DEVICE_EVENT_TYPES = frozenset({"arrived", "greeting", "awaiting_gesture", "gesture", "countdown", "cue", "rep",
                                "set_done", "paused", "resumed", "stopped", "fall", "alert", "summary_ready",
                                "session_summary", "hr", "device_status", "ping", "breather", "say"})


class Emitter(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


class DeviceRegistry:
    def __init__(self, hub: Emitter, *, stale_s: float = DEVICE_STALE_S,
                 clock: Callable[[], float] = time.time) -> None:
        self._hub = hub
        self._stale_s = stale_s
        self._clock = clock
        self._connections = 0
        self._last_seen: float | None = None
        self._last_health: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self.online = False

    def connected(self) -> None:
        self._connections += 1
        self.check()

    def disconnected(self) -> None:
        self._connections = max(0, self._connections - 1)
        self.check()

    def on_health(self, msg: dict[str, Any]) -> None:
        self._last_seen = self._clock()
        self._last_health = {k: v for k, v in msg.items() if k != "type"}
        self.check()

    def check(self) -> None:
        fresh = self._last_seen is not None and self._clock() - self._last_seen < self._stale_s
        online = self._connections > 0 and fresh
        if online == self.online:
            return
        self.online = online
        log.info("lcd online" if online else "lcd offline", extra={"connections": self._connections})
        self._hub.emit("device_status", {"device": DEVICE_NAME, "online": online, "t": round(self._clock(), 3),
                                          "last_health": self._last_health})

    def status(self) -> dict[str, Any]:
        return {"online": self.online, "connections": self._connections, "last_seen": self._last_seen,
                "last_health": self._last_health}

    async def run(self) -> None:
        while True:
            await asyncio.sleep(DEVICE_CHECK_S)
            self.check()

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="device-registry")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
