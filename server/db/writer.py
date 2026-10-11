"""Batch frame writer: its own thread, COPY into pose_frames every 0.5 s, drop-oldest buffer, reconnect backoff.

`submit()` runs on the event loop for every frame and only appends to a bounded deque under a lock, so the
engine and the WebSockets never wait on the database (TRI.md §5.16, §10 H4).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg

from server.db.pool import CONNECT_KWARGS
from server.logging_setup import get_logger

log = get_logger("db.writer")

COLUMNS = ("time", "patient_id", "session_id", "exercise_id", "set_no", "knee_l", "knee_r", "hip_l", "hip_r",
           "trunk_lean", "trunk_side", "pelvis_rot", "knee_ratio", "com_x", "com_y", "com_z", "load_l_pct",
           "heel_l_pct", "heel_r_pct", "confidence", "landmarks")
COPY_SQL = f"COPY pose_frames ({', '.join(COLUMNS)}) FROM STDIN"
ANGLES = ("knee_l", "knee_r", "hip_l", "hip_r", "trunk_lean", "trunk_side", "pelvis_rot", "knee_ratio")

WRIST_COLUMNS = ("time", "patient_id", "session_id", "quat_w", "quat_x", "quat_y", "quat_z", "rotation_deg", "accel")
WRIST_COPY_SQL = f"COPY wrist_motion ({', '.join(WRIST_COLUMNS)}) FROM STDIN"
WRIST_MAX_QUEUE = 3000  # 5 minutes at 10 Hz

FLUSH_INTERVAL_S = 0.5
MAX_QUEUE = 9000  # 5 minutes at 30 Hz
RATE_WINDOW_S = 10.0
BACKOFF_MIN_S, BACKOFF_MAX_S = 0.5, 15.0


def frame_row(frame: dict[str, Any], patient_id: str) -> tuple[Any, ...]:
    """Map a §4.5 frame dict 1:1 onto a pose_frames row (joints flattened → landmarks[99])."""
    angles = frame.get("angles") or {}
    com = frame.get("com") or [None, None, None]
    joints = frame.get("joints")
    landmarks = [float(c) for j in joints for c in j] if joints and len(joints) == 33 else None
    return (
        datetime.fromtimestamp(float(frame["t"]), UTC), patient_id, frame["session_id"], frame.get("exercise_id"),
        frame.get("set_no"), *(angles.get(k) for k in ANGLES), com[0], com[1], com[2], frame.get("load_l_pct"),
        frame.get("heel_l_pct"), frame.get("heel_r_pct"), frame.get("confidence_avg"), landmarks,
    )


def wrist_row(msg: dict[str, Any], patient_id: str) -> tuple[Any, ...]:
    """A sanitised `wrist` event → a wrist_motion row (quat is [w, x, y, z])."""
    quat = msg.get("quat") or [None, None, None, None]
    return (datetime.fromtimestamp(float(msg["t"]), UTC), patient_id, msg["session_id"], quat[0], quat[1], quat[2],
            quat[3], msg.get("rotation_deg"), msg.get("accel"))


class FrameWriter:
    def __init__(self, dsn: str, *, patient_provider: Callable[[], str | None] | None = None,
                 max_queue: int = MAX_QUEUE, flush_interval_s: float = FLUSH_INTERVAL_S,
                 backoff_max_s: float = BACKOFF_MAX_S, copy_sql: str = COPY_SQL,
                 row_fn: Callable[[dict[str, Any], str], tuple[Any, ...]] = frame_row,
                 thread_name: str = "frame-writer") -> None:
        self._dsn = dsn
        self._patient_provider = patient_provider
        self._max_queue = max_queue
        self._interval = flush_interval_s
        self._queue: deque[tuple[dict[str, Any], str]] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: psycopg.Connection[Any] | None = None
        self._backoff = BACKOFF_MIN_S
        self._backoff_max = backoff_max_s
        self._retry_at = 0.0
        self._written: deque[tuple[float, int]] = deque()
        self._copy_sql = copy_sql
        self._row_fn = row_fn
        self._thread_name = thread_name
        self.written_total = 0
        self.dropped = 0
        self.skipped = 0  # frames without a session/patient (nothing to attach them to)
        self.errors = 0
        self.last_copy_ms: float | None = None

    # ---- loop side ---------------------------------------------------------------------------------------

    def submit(self, frame: dict[str, Any]) -> None:
        """Non-blocking. Drops the oldest buffered frame when the buffer is full."""
        patient_id = frame.get("patient_id") or (self._patient_provider() if self._patient_provider else None)
        if not patient_id or not frame.get("session_id") or frame.get("t") is None:
            self.skipped += 1
            return
        with self._lock:
            if len(self._queue) >= self._max_queue:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append((frame, patient_id))

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        recent = sum(n for t, n in list(self._written) if t >= now - RATE_WINDOW_S)
        return {
            "frames_per_s": round(recent / RATE_WINDOW_S, 1),
            "written_total": self.written_total,
            "dropped": self.dropped,
            "skipped": self.skipped,
            "queue_depth": len(self._queue),
            "connected": self._conn is not None and not self._conn.closed,
            "errors": self.errors,
            "last_copy_ms": self.last_copy_ms,
        }

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        """Stop the thread after one last flush attempt (bounded by timeout_s)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None

    # ---- writer thread ------------------------------------------------------------------------------------

    def _run(self) -> None:
        """Flush every interval until stopped. Retries forever (capped backoff); nothing ends the thread early."""
        while not self._stop.wait(self._interval):
            self._flush_guarded()
        self._retry_at = 0.0
        self._flush_guarded()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def _flush_guarded(self) -> None:
        try:
            self._flush()
        except Exception as exc:  # noqa: BLE001 - an unexpected bug must not kill the writer thread
            log.exception("frame writer: unexpected error, batch dropped", extra={"error": _short(exc)})
            self._failed(exc)

    def _take(self) -> list[tuple[dict[str, Any], str]]:
        with self._lock:
            batch = list(self._queue)
            self._queue.clear()
        return batch

    def _requeue(self, batch: list[tuple[dict[str, Any], str]]) -> None:
        with self._lock:
            combined = batch + list(self._queue)
            overflow = len(combined) - self._max_queue
            if overflow > 0:
                self.dropped += overflow
                combined = combined[overflow:]
            self._queue.clear()
            self._queue.extend(combined)

    def _flush(self) -> None:
        if time.monotonic() < self._retry_at:
            return
        batch = self._take()
        if not batch:
            return
        rows = []
        for frame, patient_id in batch:
            try:
                rows.append(self._row_fn(frame, patient_id))
            except (KeyError, TypeError, ValueError):
                self.skipped += 1
        try:
            conn = self._connection()
            started = time.perf_counter()
            with conn.cursor() as cur, cur.copy(self._copy_sql) as copy:
                for row in rows:
                    copy.write_row(row)
            self.last_copy_ms = round((time.perf_counter() - started) * 1000, 1)
        except psycopg.OperationalError as exc:
            self._failed(exc)
            self._requeue(batch)
            return
        except psycopg.Error as exc:  # bad data: retrying the same batch would fail forever
            self.errors += 1
            self.dropped += len(rows)
            log.warning("frame batch rejected, dropped", extra={"rows": len(rows), "error": _short(exc)})
            return
        self._backoff = BACKOFF_MIN_S
        self.written_total += len(rows)
        now = time.monotonic()
        self._written.append((now, len(rows)))
        while self._written and self._written[0][0] < now - RATE_WINDOW_S:
            self._written.popleft()

    def _connection(self) -> psycopg.Connection[Any]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True, application_name="mira-writer",
                                         **CONNECT_KWARGS)
            log.info("frame writer connected")
        return self._conn

    def _failed(self, exc: Exception) -> None:
        self.errors += 1
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        self._retry_at = time.monotonic() + self._backoff
        log.warning("frame writer: database unreachable, retrying", extra={"retry_in_s": self._backoff,
                                                                           "error": _short(exc)})
        self._backoff = min(self._backoff * 2, self._backoff_max)


def _short(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text.splitlines()[0] if text else ''}"
