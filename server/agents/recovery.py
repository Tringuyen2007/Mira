"""RecoveryAgent: heart-rate recovery after each set (the watch's Recovery screen: "Down 22 in 1 min").

The peak is the highest `hr` between a set's first `countdown` and its `set_done`. RECOVERY_S after `set_done` — or at
the next set's first `countdown`, whichever comes first — the latest reading is the recovery value: one `hr_recovery`
event `{set_index, peak_bpm, bpm_60s, drop_bpm}` and one `hr_recovery` row (fire-and-forget). No reading during the set
means no recovery for it. Nothing here waits on the database.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from server.db import writes
from server.logging_setup import get_logger
from server.state import SessionRegistry

log = get_logger("recovery")

RECOVERY_S = 60.0
MAX_TRACKED_SESSIONS = 8


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


@dataclass(eq=False)
class _Pending:
    set_index: int
    peak: int
    task: asyncio.Task[None] | None = None


@dataclass
class _Track:
    in_set: bool = False
    peak: int | None = None
    last_bpm: int | None = None
    pending: _Pending | None = None


def _bpm(ev: dict[str, Any]) -> int | None:
    value = ev.get("bpm")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class RecoveryAgent:
    def __init__(self, hub: Any, sessions: SessionRegistry, db_provider: Callable[[], Any | None] = _default_db, *,
                 recovery_s: float = RECOVERY_S) -> None:
        self.hub = hub
        self.sessions = sessions
        self.db_provider = db_provider
        self.recovery_s = recovery_s
        self._tracks: dict[str, _Track] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    async def on_event(self, ev: dict[str, Any]) -> None:
        sid = ev.get("session_id")
        if not isinstance(sid, str):
            return
        kind = ev.get("type")
        try:
            if kind == "hr":
                bpm = _bpm(ev)
                if bpm is None:
                    return
                track = self._track(sid)
                track.last_bpm = bpm
                if track.in_set:
                    track.peak = bpm if track.peak is None else max(track.peak, bpm)
            elif kind == "countdown":
                track = self._track(sid)
                if not track.in_set:  # the first number of the next set's 3-2-1
                    self._complete(sid, track)
                    track.in_set, track.peak = True, None
            elif kind == "set_done":
                track = self._tracks.get(sid)
                if track is None or not track.in_set:
                    return
                track.in_set = False
                if track.peak is None:
                    return
                self._complete(sid, track)  # never two pending sets
                set_index = ev.get("set")
                pending = _Pending(int(set_index) if isinstance(set_index, int) else 0, track.peak)
                track.pending = pending
                pending.task = self._spawn(self._after_delay(sid, track, pending))
        except Exception:  # noqa: BLE001 - one bad event must never stop the agent
            log.exception("recovery failed on event", extra={"event_type": kind, "session_id": sid})

    def _track(self, sid: str) -> _Track:
        track = self._tracks.get(sid)
        if track is None:
            while len(self._tracks) >= MAX_TRACKED_SESSIONS:
                oldest = next(k for k in self._tracks)
                self._complete(oldest, self._tracks.pop(oldest))
            track = self._tracks[sid] = _Track()
        return track

    async def _after_delay(self, sid: str, track: _Track, pending: _Pending) -> None:
        await asyncio.sleep(self.recovery_s)
        if track.pending is pending:
            self._complete(sid, track)

    def _complete(self, sid: str, track: _Track) -> None:
        pending = track.pending
        if pending is None:
            return
        track.pending = None
        if pending.task is not None and pending.task is not asyncio.current_task():
            pending.task.cancel()
        bpm = track.last_bpm
        if bpm is None:
            return
        drop = pending.peak - bpm
        self.hub.emit("hr_recovery", {"set_index": pending.set_index, "peak_bpm": pending.peak, "bpm_60s": bpm,
                                      "drop_bpm": drop, "session_id": sid, "t": round(time.time(), 3)})
        db = self.db_provider()
        session = self.sessions.get(sid)
        if db is not None and session is not None:
            writes.fire(writes.upsert_hr_recovery(db, sid, session.patient_id, pending.set_index, pending.peak, bpm,
                                                  drop))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for track in self._tracks.values():
            track.pending = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
