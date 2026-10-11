"""A small in-memory per-key sliding-window limiter for patient actions (one instance per app, on AppState)."""

from __future__ import annotations

import time
from collections import deque

from fastapi import HTTPException

PATIENT_ACTIONS_PER_MIN = 10  # POST /me/messages, POST /me/test-alert, patient-scope POST /sessions (each)
WINDOW_S = 60.0


class PerKeyLimiter:
    """At most `max_per_min` allowed calls per key in any 60 s window. Every call to `allow` prunes keys whose
    newest hit has left the window, so the dict never grows past the keys active in the last minute."""

    def __init__(self, max_per_min: int) -> None:
        self.max_per_min = max_per_min
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        for stale in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= WINDOW_S]:
            del self._hits[stale]
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= self.max_per_min:
            return False
        hits.append(now)
        return True


def check_patient(limiter: PerKeyLimiter | None, scope: str, action: str, patient_id: str) -> None:
    """429 when a patient-scope caller exceeds the limit for `action`; the admin token is never limited."""
    if limiter is None or scope != "patient":
        return
    if not limiter.allow(f"{action}:{patient_id}"):
        raise HTTPException(status_code=429, detail="too many requests; try again in a minute")
