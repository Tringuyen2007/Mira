"""ClockSync: line up the Apple Watch's clock with the cameras' (watch Setup screen 4, "Sync Wave").

The patient waves the watch arm. The phone sends one `watch_sync` window per second (≤ 50 user-acceleration
magnitudes at 50 Hz, `t0` = the watch clock of sample 0); the cameras see the same wrist in the pose frames. The camera
wrist position (watch arm from device_prefs.watch_arm) is smoothed and differentiated twice into an acceleration
magnitude, and the last 3 s of watch samples are cross-correlated (Pearson r) against it over lags of ±1 s in 20 ms
steps. Acceleration is compared with acceleration: comparing with speed would shift the peak by a quarter period.

progress = best r / 0.7 (capped below 1 until locked). Lock = r ≥ 0.7 in 2 consecutive windows whose lags agree within
100 ms (method "xcorr"); otherwise 8 s after the first window the receive-time estimate is used (method "rtt": median
of last-sample watch time minus server receive time, i.e. the offset minus the one-way relay delay). The offset
(`watch_clock − server_clock`, ms) is written to sessions.watch_clock_offset_ms and offered to the wrist recorder.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from server.db import writes
from server.logging_setup import get_logger
from server.watch_prefs import watch_arm

log = get_logger("clock_sync")

ARM_WRIST = {"left": 15, "right": 16}  # BlazePose wrist per side
MAX_LAG_MS = 1000
LAG_STEP_MS = 20
LOCK_CORR = 0.7
LOCK_WINDOWS = 2
LOCK_AGREE_MS = 100
FALLBACK_S = 8.0
WATCH_BUFFER = 150   # samples: 3 s at 50 Hz
CAMERA_BUFFER = 180  # frames: 6 s at 30 Hz
MIN_OVERLAP = 25
SMOOTH_FRAMES = 5
MAX_STATES = 4


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def camera_accel_series(samples: list[tuple[float, list[float]]]) -> tuple[np.ndarray, np.ndarray] | None:
    """(t, |acceleration|) of a wrist track: a centred moving average over SMOOTH_FRAMES, then two central
    differences. None when there are too few frames with increasing times."""
    clean: list[tuple[float, list[float]]] = []
    for t, p in samples:
        if not clean or t > clean[-1][0]:
            clean.append((t, p))
    if len(clean) < SMOOTH_FRAMES + 4:
        return None
    t = np.array([s[0] for s in clean], dtype=float)
    pos = np.array([s[1] for s in clean], dtype=float)
    kernel = np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES
    smooth = np.column_stack([np.convolve(pos[:, i], kernel, mode="same") for i in range(3)])
    h = SMOOTH_FRAMES // 2
    t, smooth = t[h:-h], smooth[h:-h]  # the edges of a "same" convolution are biased
    vel = np.gradient(smooth, t, axis=0)
    acc = np.gradient(vel, t, axis=0)
    return t, np.linalg.norm(acc, axis=1)


def estimate_offset(watch_t: np.ndarray, watch_mag: np.ndarray, cam_t: np.ndarray, cam_mag: np.ndarray, *,
                     max_lag_ms: int = MAX_LAG_MS, step_ms: int = LAG_STEP_MS) -> tuple[int, float] | None:
    """(offset_ms, r) maximising the Pearson correlation of the watch magnitudes with the camera series sampled at
    `watch_t - offset` (offset = watch clock − server clock). None when no lag has enough overlap or variance."""
    best: tuple[int, float] | None = None
    for lag_ms in range(-max_lag_ms, max_lag_ms + 1, step_ms):
        server_t = watch_t - lag_ms / 1000.0
        mask = (server_t >= cam_t[0]) & (server_t <= cam_t[-1])
        if int(mask.sum()) < MIN_OVERLAP:
            continue
        cam = np.interp(server_t[mask], cam_t, cam_mag)
        watch = watch_mag[mask]
        if cam.std() < 1e-9 or watch.std() < 1e-9:
            continue
        r = float(np.corrcoef(watch, cam)[0, 1])
        if best is None or r > best[1]:
            best = (lag_ms, r)
    return best


@dataclass
class _SyncState:
    arm: str
    frames: deque[tuple[float, list[float]]] = field(default_factory=lambda: deque(maxlen=CAMERA_BUFFER))
    watch: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=WATCH_BUFFER))
    rtt_offsets_ms: list[float] = field(default_factory=list)
    good: list[int] = field(default_factory=list)
    started: float | None = None
    offset_ms: int | None = None
    method: str | None = None


class ClockSync:
    def __init__(self, hub: Any, sessions: Any, db_provider: Callable[[], Any | None] = _default_db, *,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.hub = hub
        self.sessions = sessions
        self.db_provider = db_provider
        self.clock = clock
        self.wall = wall
        self._states: dict[str, _SyncState] = {}

    def _state(self, session_id: str) -> _SyncState:
        st = self._states.get(session_id)
        if st is None:
            while len(self._states) >= MAX_STATES:
                self._states.pop(next(iter(self._states)))
            session = self.sessions.get(session_id)
            st = self._states[session_id] = _SyncState(watch_arm(session.profile.device_prefs if session else None))
        return st

    def offset_s(self, session_id: str) -> float | None:
        st = self._states.get(session_id)
        return st.offset_ms / 1000.0 if st is not None and st.offset_ms is not None else None

    # ---- camera side (bridge.subscribe_frames; sync, cheap) ---------------------------------------------------

    def on_frame(self, frame: dict[str, Any]) -> None:
        current = self.sessions.current
        sid = frame.get("session_id")
        if current is None or sid != current.session_id:
            return
        st = self._state(sid)
        if st.offset_ms is not None:
            return
        try:
            p = frame["joints"][ARM_WRIST.get(st.arm, 16)]
            st.frames.append((float(frame["t"]), [float(p[0]), float(p[1]), float(p[2])]))
        except (KeyError, IndexError, TypeError, ValueError):
            return

    # ---- watch side (main._on_watch_sync) -----------------------------------------------------------------------

    def on_window(self, msg: dict[str, Any], session_id: str) -> None:
        if not session_id:
            return
        st = self._state(session_id)
        if st.offset_ms is not None:  # already locked: say so again (a late or re-opened watch screen)
            self._emit_locked(session_id, st)
            return
        now = self.clock()
        if st.started is None:
            st.started = now
        t0, dt = float(msg["t0"]), float(msg["dt_ms"]) / 1000.0
        mags = [float(m) for m in msg["accel_mag"]]
        for i, m in enumerate(mags):
            st.watch.append((t0 + i * dt, m))
        st.rtt_offsets_ms.append((t0 + (len(mags) - 1) * dt - self.wall()) * 1000.0)

        corr = 0.0
        est = None
        cam = camera_accel_series(list(st.frames))
        if cam is not None and len(st.watch) >= MIN_OVERLAP:
            watch_t = np.array([w[0] for w in st.watch])
            watch_mag = np.array([w[1] for w in st.watch])
            est = estimate_offset(watch_t, watch_mag, *cam)
        if est is not None and est[1] >= LOCK_CORR:
            corr = est[1]
            st.good.append(est[0])
        else:
            corr = max(0.0, est[1]) if est is not None else 0.0
            st.good.clear()
        if len(st.good) >= LOCK_WINDOWS and abs(st.good[-1] - st.good[-2]) <= LOCK_AGREE_MS:
            self._lock(session_id, st, round((st.good[-1] + st.good[-2]) / 2), "xcorr")
            return
        if now - st.started >= FALLBACK_S:
            self._lock(session_id, st, round(statistics.median(st.rtt_offsets_ms)), "rtt")
            return
        self.hub.emit("sync_progress", {"progress": round(min(0.95, corr / LOCK_CORR), 2), "locked": False,
                                         "session_id": session_id, "t": round(self.wall(), 3)})

    def _lock(self, session_id: str, st: _SyncState, offset_ms: int, method: str) -> None:
        st.offset_ms, st.method = int(offset_ms), method
        log.info("watch clock locked", extra={"session_id": session_id, "offset_ms": st.offset_ms, "method": method})
        self._emit_locked(session_id, st)
        db = self.db_provider()
        if db is not None:
            writes.fire(writes.set_watch_clock_offset(db, session_id, st.offset_ms))

    def _emit_locked(self, session_id: str, st: _SyncState) -> None:
        self.hub.emit("sync_progress", {"progress": 1.0, "locked": True, "offset_ms": st.offset_ms,
                                         "method": st.method, "session_id": session_id, "t": round(self.wall(), 3)})
