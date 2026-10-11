"""Wrist samples (the watch's motion relayed by the phone, or the mock's MOCK_WRIST stream) → wrist_motion through a
COPY-mode FrameWriter. At most one row per `min_interval_s` (0.1 s = 10 Hz) per patient. Once ClockSync has locked
the watch's clock offset for the session, sample times are moved onto the server clock."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

WRIST_MIN_INTERVAL_S = 0.1


def _no_offset(_session_id: str) -> float | None:
    return None


class WristRecorder:
    def __init__(self, writer: Any, patient_of: Callable[[str], str | None], *,
                 offset_s: Callable[[str], float | None] = _no_offset,
                 min_interval_s: float = WRIST_MIN_INTERVAL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.writer = writer
        self.patient_of = patient_of
        self.offset_s = offset_s  # assignable: main wires ClockSync.offset_s once it exists
        self.min_interval_s = min_interval_s
        self.clock = clock
        self._last: dict[str, float] = {}
        self.throttled = 0

    async def on_event(self, ev: dict[str, Any]) -> None:
        if ev.get("type") != "wrist":
            return
        sid, t = ev.get("session_id"), ev.get("t")
        if not isinstance(sid, str) or not isinstance(t, (int, float)) or isinstance(t, bool):
            return
        patient_id = self.patient_of(sid)
        if patient_id is None:
            return
        now = self.clock()
        last = self._last.get(patient_id)
        if last is not None and now - last < self.min_interval_s:
            self.throttled += 1
            return
        self._last[patient_id] = now
        offset = self.offset_s(sid)
        self.writer.submit({**ev, "patient_id": patient_id, "t": float(t) - offset if offset is not None else float(t)})
