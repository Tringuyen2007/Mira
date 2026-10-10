"""Pair frames from two unsynchronised cameras by nearest timestamp."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from vision.types import PoseResult


@dataclass
class Pair:
    t: float  # monotonic seconds of the anchor frame
    a: PoseResult | None  # camera "A"
    b: PoseResult | None  # camera "B"
    skew_s: float | None  # b.t - a.t when both present


class Pairer:
    """Each anchor result is emitted exactly once, paired with the nearest other-camera result
    within max_skew_s, or alone. It waits until a newer other-camera result has arrived (so the
    nearest is known) or wait_s has passed."""

    def __init__(self, anchor: str = "A", other: str | None = "B", max_skew_s: float = 0.033,
                 wait_s: float = 0.1, stale_s: float = 0.5):
        self.anchor, self.other = anchor, other
        self.max_skew_s, self.wait_s, self.stale_s = max_skew_s, wait_s, stale_s
        self._pending: deque[PoseResult] = deque()
        self._others: deque[PoseResult] = deque(maxlen=16)
        self._last_anchor_t = -np.inf
        self._latest_t = -np.inf

    @staticmethod
    def _pair(t, x: PoseResult | None, y: PoseResult | None, skew) -> Pair:
        by_cam = {p.cam: p for p in (x, y) if p is not None}
        return Pair(t, by_cam.get("A"), by_cam.get("B"), skew)

    def add(self, r: PoseResult) -> list[Pair]:
        self._latest_t = max(self._latest_t, r.t)
        if self.other is None:
            return [self._pair(r.t, r, None, None)] if r.cam == self.anchor else []
        out: list[Pair] = []
        if r.cam == self.anchor:
            self._pending.append(r)
            self._last_anchor_t = r.t
        elif r.cam == self.other:
            self._others.append(r)
            if not self._pending and r.t - self._last_anchor_t > self.stale_s:
                out.append(self._pair(r.t, None, r, None))
        out.extend(self._flush())
        return out

    def _flush(self) -> list[Pair]:
        out = []
        while self._pending:
            a = self._pending[0]
            newer = any(o.t >= a.t for o in self._others)
            if not newer and self._latest_t - a.t < self.wait_s:
                break
            self._pending.popleft()
            b = min(self._others, key=lambda o: abs(o.t - a.t), default=None)
            if b is not None and abs(b.t - a.t) <= self.max_skew_s:
                out.append(self._pair(a.t, a, b, b.t - a.t))
            else:
                out.append(self._pair(a.t, a, None, None))
        return out


class SkewStats:
    def __init__(self, n: int = 300):
        self._skews: deque[float] = deque(maxlen=n)
        self._paired: deque[bool] = deque(maxlen=n)

    def add(self, pair: Pair) -> None:
        self._paired.append(pair.a is not None and pair.b is not None)
        if pair.skew_s is not None:
            self._skews.append(abs(pair.skew_s))

    def summary(self) -> dict[str, float]:
        s = np.array(self._skews) * 1000
        return {
            "median_ms": float(np.median(s)) if s.size else float("nan"),
            "p95_ms": float(np.percentile(s, 95)) if s.size else float("nan"),
            "unpaired_pct": 100.0 * (1 - np.mean(self._paired)) if self._paired else float("nan"),
        }
