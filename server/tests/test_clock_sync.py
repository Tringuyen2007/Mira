"""ClockSync: the watch's sync-wave accel magnitude vs the cameras' wrist acceleration, cross-correlated over ±1 s;
locks after 2 agreeing windows with r ≥ 0.7, else falls back to the receive-time ("rtt") estimate after 8 s."""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from server.agents import clock_sync as clock_sync_mod
from server.agents.clock_sync import ClockSync, camera_accel_series, estimate_offset
from server.state import SessionRegistry

T0 = 1_791_662_000.0


def x(t: Any) -> Any:  # the wrist's wave (non-periodic over the window, so one correlation peak)
    return 0.10 * np.sin(2 * np.pi * 1.3 * t) + 0.05 * np.sin(2 * np.pi * 2.9 * t + 1.0)


def xdd(t: Any) -> Any:
    return -(0.10 * (2 * np.pi * 1.3) ** 2 * np.sin(2 * np.pi * 1.3 * t)
             + 0.05 * (2 * np.pi * 2.9) ** 2 * np.sin(2 * np.pi * 2.9 * t + 1.0))


def _camera(wrist_index: int = 16) -> list[dict[str, Any]]:
    frames = []
    for t in T0 + np.arange(0, 6, 1 / 30):
        joints = [[0.0, 1.0, 0.0] for _ in range(33)]
        joints[wrist_index] = [float(x(t - T0)), 1.0, -0.3]
        frames.append({"type": "frame", "t": float(t), "joints": joints})
    return frames


def _window(k: int, offset_s: float) -> dict[str, Any]:
    server_t = T0 + 2.5 + k + np.arange(0, 1, 0.02)
    return {"type": "watch_sync", "t0": float(server_t[0] + offset_s), "dt_ms": 20.0,
            "accel_mag": [float(v) for v in np.abs(xdd(server_t - T0)) / 9.81]}


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


@pytest.mark.parametrize("offset_ms", [240, -420, 0, 900])
def test_estimate_offset_finds_the_lag(offset_ms: int) -> None:
    cam = camera_accel_series([(f["t"], f["joints"][16]) for f in _camera()])
    assert cam is not None
    st = T0 + 2.5 + np.arange(0, 3, 0.02)
    lag, r = estimate_offset(st + offset_ms / 1000, np.abs(xdd(st - T0)) / 9.81, *cam)  # type: ignore[misc]
    assert abs(lag - offset_ms) <= 20 and r > 0.9


def test_camera_series_needs_enough_distinct_frames() -> None:
    assert camera_accel_series([(1.0, [0.0, 0.0, 0.0])] * 20) is None
    assert camera_accel_series([(1.0 + i / 30, [0.0, 0.0, 0.0]) for i in range(5)]) is None


async def _sync(arm: str = "right", db: Any = None) -> tuple[ClockSync, FakeHub, str, list[float]]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    session.profile.device_prefs = {"watch_arm": arm}
    hub, now = FakeHub(), [0.0]
    sync = ClockSync(hub, sessions, db_provider=lambda: db, clock=lambda: now[0], wall=lambda: T0 + 6.0)
    return sync, hub, session.session_id, now


async def test_xcorr_lock_after_two_agreeing_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[Any, ...]] = []

    async def fake(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(clock_sync_mod.writes, "set_watch_clock_offset", fake)
    db = object()
    sync, hub, sid, now = await _sync(db=db)
    for frame in _camera():
        sync.on_frame({**frame, "session_id": sid})
    sync.on_window(_window(0, 0.24), sid)
    first = hub.events[-1]
    assert first["type"] == "sync_progress" and first["locked"] is False and 0.5 < first["progress"] < 1.0
    now[0] = 1.0
    sync.on_window(_window(1, 0.24), sid)
    locked = hub.events[-1]
    assert locked["locked"] is True and locked["progress"] == 1.0 and locked["method"] == "xcorr"
    assert abs(locked["offset_ms"] - 240) <= 20 and locked["session_id"] == sid
    assert sync.offset_s(sid) == pytest.approx(locked["offset_ms"] / 1000)
    await asyncio.sleep(0)
    assert written and written[0][0] is db and written[0][1] == sid and abs(written[0][2] - 240) <= 20


async def test_left_arm_uses_the_left_wrist() -> None:
    sync, hub, sid, now = await _sync(arm="left")
    for frame in _camera(wrist_index=15):
        sync.on_frame({**frame, "session_id": sid})
    sync.on_window(_window(0, -0.1), sid)
    now[0] = 1.0
    sync.on_window(_window(1, -0.1), sid)
    assert hub.events[-1]["locked"] is True and abs(hub.events[-1]["offset_ms"] + 100) <= 20


async def test_rtt_fallback_after_8_seconds() -> None:
    sync, hub, sid, now = await _sync()  # no frames: nothing to correlate with
    for k in range(9):
        now[0] = float(k)
        window = {"type": "watch_sync", "t0": T0 + 6.0 - 0.15 - 0.98, "dt_ms": 20.0, "accel_mag": [0.5] * 50}
        sync.on_window(window, sid)  # last sample = wall - 150 ms
    progress = [e for e in hub.events if not e["locked"]]
    assert len(progress) == 8 and all(e["progress"] == 0.0 for e in progress)
    locked = hub.events[-1]
    assert locked["locked"] is True and locked["method"] == "rtt" and locked["offset_ms"] == -150


async def test_frames_of_other_sessions_are_ignored_and_lock_is_sticky() -> None:
    sync, hub, sid, now = await _sync()
    for frame in _camera():
        sync.on_frame({**frame, "session_id": "someone-else"})
    sync.on_window(_window(0, 0.24), sid)
    assert hub.events[-1]["progress"] == 0.0  # no camera data for this session
    for frame in _camera():
        sync.on_frame({**frame, "session_id": sid})
    for k in (1, 2):
        now[0] = float(k)
        sync.on_window(_window(k, 0.24), sid)
    offset = hub.events[-1]["offset_ms"]
    now[0] = 3.0
    sync.on_window(_window(0, 0.9), sid)  # after the lock: re-announced, unchanged
    assert hub.events[-1]["locked"] is True and hub.events[-1]["offset_ms"] == offset
