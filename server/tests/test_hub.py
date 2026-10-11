from __future__ import annotations

import asyncio
import json
import time

from server.hub import QUEUE_MAXSIZE, Hub, pose_payload


def _strict(name: str) -> None:
    raise AssertionError(f"server sent non-JSON constant {name}")


class FakeWS:
    """Minimal WebSocket stand-in: records sends, optionally slow, receives from an inbox."""

    def __init__(self, send_delay_s: float = 0.0) -> None:
        self.sent: list[dict] = []
        self.send_delay_s = send_delay_s
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed_with: int | None = None

    async def send_text(self, data: str) -> None:
        if self.send_delay_s:
            await asyncio.sleep(self.send_delay_s)
        self.sent.append(json.loads(data, parse_constant=_strict))  # like a browser's JSON.parse

    async def receive_text(self) -> str:
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("disconnected")
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def _wait_for(predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_slow_client_drops_oldest_and_never_blocks_publish() -> None:
    hub = Hub()
    slow, fast = FakeWS(send_delay_s=0.05), FakeWS()
    tasks = [asyncio.create_task(hub.connect("events", slow)), asyncio.create_task(hub.connect("events", fast))]
    await _wait_for(lambda: hub.stats()["clients"]["events"] == 2)

    publish_time = 0.0
    for i in range(500):
        start = time.perf_counter()
        hub.emit("rep", {"index": i})
        publish_time += time.perf_counter() - start
        if i % 10 == 9:
            await asyncio.sleep(0.002)  # let senders run, like a 30 Hz producer would
    assert publish_time < 0.1, f"publish took {publish_time:.3f}s for 500 messages"

    await _wait_for(lambda: len(fast.sent) == 500)
    await _wait_for(lambda: slow.sent and slow.sent[-1]["index"] == 499, timeout=5.0)
    received = [m["index"] for m in slow.sent]
    assert len(received) < 200
    assert received == sorted(received)
    assert received[-QUEUE_MAXSIZE:] == list(range(500 - QUEUE_MAXSIZE, 500))
    assert hub.stats()["dropped"]["events"] == 500 - len(received)

    for ws in (slow, fast):
        ws.inbox.put_nowait(None)
    await asyncio.gather(*tasks)
    assert hub.stats()["clients"]["events"] == 0


async def test_incoming_messages_reach_handler() -> None:
    hub = Hub()
    ws = FakeWS()
    got: list[dict] = []
    task = asyncio.create_task(hub.connect("events", ws, got.append))
    for text in ('{"type": "hr", "bpm": 90}', "not json", "[1, 2]", '{"type": "hr", "bpm": NaN}',
                 '{"type": "wrist", "accel": [0, 0, Infinity]}'):
        ws.inbox.put_nowait(text)
    await _wait_for(lambda: len(got) == 1)
    ws.inbox.put_nowait(None)
    await task
    assert got == [{"type": "hr", "bpm": 90}]


async def test_ping_when_idle() -> None:
    hub = Hub(ping_interval_s=0.05)
    ws = FakeWS()
    task = asyncio.create_task(hub.connect("live", ws))
    await _wait_for(lambda: any(m.get("type") == "ping" for m in ws.sent))
    ws.inbox.put_nowait(None)
    await task


def test_pose_payload_is_rounded_subset() -> None:
    frame = {"type": "frame", "seq": 1, "t": 1760112345.11749, "joints": [[0.123456, 1.0, -0.98765]] * 33,
             "confidence": [0.98765] * 33, "load_l_pct": 46.54321, "flags": ["knee_cave_l"],
             "angles": {"knee_l": 71.2}}
    out = pose_payload(frame)
    assert set(out) == {"t", "joints", "confidence", "load_l_pct", "flags"}
    assert out["joints"][0] == [0.123, 1.0, -0.988]
    assert out["confidence"][0] == 0.988
    assert out["load_l_pct"] == 46.543


async def test_pose_throttled_to_25hz() -> None:
    now = [1000.0]
    hub = Hub(clock=lambda: now[0])
    ws = FakeWS()
    task = asyncio.create_task(hub.connect("pose", ws))
    await _wait_for(lambda: hub.stats()["clients"]["pose"] == 1)
    for i in range(300):  # 10 s at 30 Hz
        now[0] = 1000.0 + i / 30
        hub.publish("pose", {"t": now[0], "joints": [], "confidence": [], "load_l_pct": 50.0, "flags": []})
    published = hub.stats()["published"]["pose"]
    assert 245 <= published <= 255
    ws.inbox.put_nowait(None)
    await task


async def test_non_finite_numbers_are_sent_as_null() -> None:
    hub = Hub()
    pose, events = FakeWS(), FakeWS()
    tasks = [asyncio.create_task(hub.connect("pose", pose)), asyncio.create_task(hub.connect("events", events))]
    await _wait_for(lambda: hub.stats()["clients"]["pose"] == hub.stats()["clients"]["events"] == 1)
    nan, inf = float("nan"), float("inf")
    hub.publish("pose", {"t": 1.0, "joints": [[nan, 0.5, -inf]], "confidence": [nan], "load_l_pct": nan, "flags": []})
    hub.emit("fall_status", {"still_s": inf, "detail": {"angles": [nan, 1.0]}})
    await _wait_for(lambda: pose.sent and events.sent)
    assert pose.sent[0]["joints"] == [[None, 0.5, None]]
    assert pose.sent[0]["load_l_pct"] is None
    assert events.sent[0] == {"type": "fall_status", "still_s": None, "detail": {"angles": [None, 1.0]}}
    for ws in (pose, events):
        ws.inbox.put_nowait(None)
    await asyncio.gather(*tasks)
