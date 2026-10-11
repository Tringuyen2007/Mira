"""WebSocket hub: per-client bounded queues (drop oldest) and one sender task per client.

`publish()` never awaits a client, so a slow phone can't stall the vision loop.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from server.logging_setup import get_logger

log = get_logger("hub")

CHANNELS = ("pose", "live", "events", "notifications")
QUEUE_MAXSIZE = 50
POSE_HZ = 25.0
PING_INTERVAL_S = 10.0
SEND_TIMEOUT_S = 5.0
POSE_FIELDS = ("t", "joints", "confidence", "load_l_pct", "flags")


class WebSocketLike(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def receive_text(self) -> str: ...

    async def close(self, code: int = 1000) -> None: ...


@dataclass(eq=False)
class _Client:
    channel: str
    ws: WebSocketLike
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAXSIZE))
    dropped: int = 0
    accept: Callable[[dict[str, Any]], bool] | None = None  # per-client filter, evaluated in publish()


def _round(value: Any, ndigits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, list):
        return [_round(v, ndigits) for v in value]
    return value


def _finite(value: Any) -> Any:
    """Replace NaN/±Infinity (not valid JSON; browsers' JSON.parse rejects them) with None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def to_json(msg: dict[str, Any]) -> str:
    """Strict JSON for clients. The engine may emit NaN (e.g. an undefined angle); those become null."""
    try:
        return json.dumps(msg, separators=(",", ":"), allow_nan=False)
    except ValueError:
        return json.dumps(_finite(msg), separators=(",", ":"), allow_nan=False)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not JSON")


def pose_payload(frame: dict[str, Any]) -> dict[str, Any]:
    """The /ws/pose subset of a §4.5 frame, rounded to 3 decimals."""
    return {k: _round(frame[k]) for k in POSE_FIELDS if k in frame}


class RateLimiter:
    """Token bucket: `rate` messages per second with bursts up to `rate`. One per client connection."""

    def __init__(self, rate: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate
        self._tokens = rate
        self._clock = clock
        self._last = clock()
        self.dropped = 0

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        self.dropped += 1
        return False


class Hub:
    def __init__(self, *, ping_interval_s: float = PING_INTERVAL_S, pose_hz: float = POSE_HZ,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clients: dict[str, set[_Client]] = {ch: set() for ch in CHANNELS}
        self._dropped: dict[str, int] = dict.fromkeys(CHANNELS, 0)
        self._published: dict[str, int] = dict.fromkeys(CHANNELS, 0)
        self._slow_disconnects = 0
        self._pose_throttled = 0
        self._ping_interval_s = ping_interval_s
        self._pose_interval_s = 1.0 / pose_hz
        self._pose_next_due = 0.0
        self._clock = clock
        self.emit_observers: list[Callable[[dict[str, Any]], None]] = []

    async def connect(self, channel: str, ws: WebSocketLike,
                      on_message: Callable[[dict[str, Any]], None] | None = None,
                      on_connect: Callable[[], Awaitable[None]] | None = None,
                      accept: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """Serve an accepted WebSocket until it disconnects. Incoming JSON objects go to `on_message`.

        `on_connect` (optional, awaited) runs once the client is registered but before its sender task
        starts draining the queue — used for a reconnect replay (e.g. `/ws/notifications?since_id=`) that
        must be sent before any later live message, with no gap and no duplicate. `accept` (optional, sync,
        cheap) decides per published message whether this client gets it (e.g. therapist scoping).
        """
        if channel not in self._clients:
            raise ValueError(f"unknown channel {channel!r}")
        client = _Client(channel, ws, accept=accept)
        self._clients[channel].add(client)
        sender: asyncio.Task[None] | None = None
        try:
            if on_connect is not None:
                try:
                    await on_connect()
                except Exception:  # noqa: BLE001 - the socket went away mid-replay; treat as a disconnect
                    log.info("ws closed during on_connect", extra={"channel": channel})
                    return
            sender = asyncio.create_task(self._sender(client), name=f"ws-sender-{channel}")
            log.info("ws connected", extra={"channel": channel, "clients": len(self._clients[channel])})
            await self._receiver(client, on_message, sender)
        finally:
            if sender is not None:
                sender.cancel()
            self._clients[channel].discard(client)
            log.info("ws disconnected", extra={"channel": channel, "clients": len(self._clients[channel]),
                                               "dropped": client.dropped})

    def publish(self, channel: str, msg: dict[str, Any]) -> None:
        """Queue `msg` for every client of `channel`. Sync and non-blocking; call from the loop thread."""
        if channel == "pose":
            now = self._clock()
            if now < self._pose_next_due:
                self._pose_throttled += 1
                return
            # Schedule on a fixed grid so a 30 Hz source yields ~25 Hz, not 15 Hz.
            on_grid = now - self._pose_next_due < self._pose_interval_s
            self._pose_next_due = (self._pose_next_due if on_grid else now) + self._pose_interval_s
            msg = pose_payload(msg)
        clients = self._clients.get(channel)
        if clients is None:
            raise ValueError(f"unknown channel {channel!r}")
        self._published[channel] += 1
        if not clients:
            return
        text = to_json(msg)
        for client in clients:
            if client.accept is None or self._accepts(client, msg):
                self._enqueue(client, text)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an agent event (Voice, Coach, Guardian). Observers (the bridge's LiveState) see it first, so
        /ws/live reflects agent captions and alerts too; engine events go through EngineBridge._route instead."""
        msg = {"type": event_type, **payload}
        for observer in self.emit_observers:
            try:
                observer(msg)
            except Exception:  # noqa: BLE001 - an observer bug must not stop the event
                log.exception("emit observer failed", extra={"event_type": event_type})
        self.publish("events", msg)

    def stats(self) -> dict[str, Any]:
        return {
            "clients": {ch: len(c) for ch, c in self._clients.items()},
            "published": dict(self._published),
            "dropped": dict(self._dropped),
            "pose_throttled": self._pose_throttled,
            "slow_disconnects": self._slow_disconnects,
        }

    @staticmethod
    def _accepts(client: _Client, msg: dict[str, Any]) -> bool:
        try:
            return bool(client.accept(msg))  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - a broken filter must not break publish() for other clients
            log.exception("ws client filter failed", extra={"channel": client.channel})
            return False

    def _enqueue(self, client: _Client, text: str) -> None:
        if client.queue.full():
            client.queue.get_nowait()
            client.dropped += 1
            self._dropped[client.channel] += 1
        client.queue.put_nowait(text)

    async def _sender(self, client: _Client) -> None:
        ping = json.dumps({"type": "ping"})
        while True:
            try:
                text = await asyncio.wait_for(client.queue.get(), timeout=self._ping_interval_s)
            except TimeoutError:
                text = ping
            try:
                await asyncio.wait_for(client.ws.send_text(text), timeout=SEND_TIMEOUT_S)
            except TimeoutError:
                self._slow_disconnects += 1
                log.warning("ws client too slow, closing", extra={"channel": client.channel})
                await self._close_quietly(client.ws, 1013)
                return
            except Exception:  # noqa: BLE001 - socket gone; the receiver notices and cleans up
                return

    async def _receiver(self, client: _Client, on_message: Callable[[dict[str, Any]], None] | None,
                        sender: asyncio.Task[None]) -> None:
        receive = asyncio.ensure_future(client.ws.receive_text())
        try:
            while True:
                done, _ = await asyncio.wait({receive, sender}, return_when=asyncio.FIRST_COMPLETED)
                if sender in done:
                    return
                try:
                    text = receive.result()
                except Exception:  # noqa: BLE001 - disconnect or protocol error ends the connection
                    return
                if on_message is not None:
                    self._handle_incoming(client, text, on_message)
                receive = asyncio.ensure_future(client.ws.receive_text())
        finally:
            receive.cancel()

    @staticmethod
    def _handle_incoming(client: _Client, text: str, on_message: Callable[[dict[str, Any]], None]) -> None:
        try:
            msg = json.loads(text, parse_constant=_reject_constant)
        except ValueError:
            log.debug("ignoring non-JSON ws message", extra={"channel": client.channel})
            return
        if not isinstance(msg, dict):
            return
        try:
            on_message(msg)
        except Exception:  # noqa: BLE001 - a bad client message must not end the connection
            log.exception("ws message handler failed", extra={"channel": client.channel})

    @staticmethod
    async def _close_quietly(ws: WebSocketLike, code: int) -> None:
        try:
            await asyncio.wait_for(ws.close(code=code), timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
