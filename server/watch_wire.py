"""Validation of the watch's messages the phone relays on /ws/events (contract: `wrist`, `watch_sync`).
Malformed input is dropped or trimmed here, before the bridge, the agents or the database see it."""

from __future__ import annotations

import math
import time
from typing import Any

MAX_ROTATION_DEG = 360.0
MAX_SYNC_SAMPLES = 60      # ≤ 50 per 1 s window at 50 Hz, with slack
SYNC_DT_MS = (5.0, 100.0)
MAX_ACCEL_G = 16.0
MAX_CLOCK_SKEW_S = 3600.0  # a `t` further than this from the server clock is not trusted


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _vec(value: Any, n: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != n:
        return None
    nums = [_num(v) for v in value]
    return None if any(v is None for v in nums) else nums  # type: ignore[return-value]


def sanitize_wrist(msg: dict[str, Any]) -> dict[str, Any] | None:
    """`{type, t?, quat? [w,x,y,z], accel? [x,y,z], rotation_deg?}` with finite numbers only; None when nothing
    usable is left (a time alone is not a sample)."""
    out: dict[str, Any] = {"type": "wrist"}
    t = _num(msg.get("t"))
    if t is not None and abs(t - time.time()) <= MAX_CLOCK_SKEW_S:
        out["t"] = t
    quat, accel, deg = _vec(msg.get("quat"), 4), _vec(msg.get("accel"), 3), _num(msg.get("rotation_deg"))
    if quat is not None:
        out["quat"] = quat
    if accel is not None:
        out["accel"] = accel
    if deg is not None and abs(deg) <= MAX_ROTATION_DEG:
        out["rotation_deg"] = round(deg, 1)
    return out if set(out) - {"type", "t"} else None


def sanitize_watch_sync(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One sync-wave window: `t0` (watch clock of sample 0), `dt_ms` and 1..60 user-acceleration magnitudes in g."""
    t0, dt = _num(msg.get("t0")), _num(msg.get("dt_ms"))
    mags = msg.get("accel_mag")
    if t0 is None or dt is None or not SYNC_DT_MS[0] <= dt <= SYNC_DT_MS[1]:
        return None
    if not isinstance(mags, list) or not 1 <= len(mags) <= MAX_SYNC_SAMPLES:
        return None
    values = [_num(m) for m in mags]
    if any(v is None or v < 0 or v > MAX_ACCEL_G for v in values):
        return None
    return {"type": "watch_sync", "t0": t0, "dt_ms": dt, "accel_mag": values}
