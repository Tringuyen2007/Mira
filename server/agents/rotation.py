"""RotationCoach: the Apple Watch rotation exercises (forearm turns, shoulder turns, wrist bends).

Active only for a session whose exercise is a watch exercise. While a set runs (from the countdown's "1" or the
`set_start` cue, until `set_done`; not while paused) it counts reps from the watch's `wrist.rotation_deg` stream:
a rep is an excursion of at least MIN_GOAL_FRACTION of the goal that comes back within ZERO_ZONE_DEG of zero. Each rep
→ a `rotation_rep` event and a `rep` event injected for everyone else (persistence, Coach, Voice, LiveState,
HapticCoach: `source: "watch"`, quality "good" when the goal was reached, else "partial"). `set_done` → a
`rotation_set` event and a rotation_sets row.

Elbow drift (pose frames): the watch-arm elbow more than DRIFT_M (horizontally) from the same-side shoulder for more
than DRIFT_HOLD_S → a `cue` "Keep your elbow at your side" + `haptic fix_form`, once until the elbow is back
(< DRIFT_REARM_M); the next counted rep carries the `elbow_drift` flag (a form_events row via persistence).
The goal is the prescription's `targets.rotation_deg` (read once per session as copilot_ro), default 80°.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from server import exercises
from server.db import queries, writes
from server.logging_setup import get_logger
from server.state import SessionRegistry
from server.watch_prefs import watch_arm

log = get_logger("rotation")

ZERO_ZONE_DEG = 15.0
MIN_GOAL_FRACTION = 0.6
DRIFT_M = 0.08
DRIFT_HOLD_S = 0.5
DRIFT_REARM_M = 0.06
PRESCRIPTION_TIMEOUT_S = 1.5
ELBOW_CUE_ID = "elbow_drift"
ELBOW_CUE_TEXT = "Keep your elbow at your side"
ARM_JOINTS = {"left": (11, 13), "right": (12, 14)}  # BlazePose (shoulder, elbow) per side
MAX_SKIPPED = 200


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


class RepCounter:
    """Hysteresis over |rotation_deg|: leaving the zero zone starts an excursion, coming back ends it; the excursion
    is a rep when its peak reached MIN_GOAL_FRACTION of the goal."""

    def __init__(self, goal_deg: float) -> None:
        self.goal_deg = goal_deg
        self._out = False
        self._peak = 0.0

    def update(self, deg: float) -> float | None:
        a = abs(deg)
        if not self._out:
            if a > ZERO_ZONE_DEG:
                self._out, self._peak = True, a
            return None
        self._peak = max(self._peak, a)
        if a > ZERO_ZONE_DEG:
            return None
        peak, self._out, self._peak = self._peak, False, 0.0
        return peak if peak >= MIN_GOAL_FRACTION * self.goal_deg else None


def elbow_drift_m(joints: Any, arm: str) -> float | None:
    """Horizontal (x/z) distance in metres between the watch-arm elbow and the same-side shoulder."""
    shoulder_i, elbow_i = ARM_JOINTS.get(arm, ARM_JOINTS["right"])
    try:
        s, e = joints[shoulder_i], joints[elbow_i]
        return math.hypot(float(e[0]) - float(s[0]), float(e[2]) - float(s[2]))
    except (IndexError, TypeError, ValueError, KeyError):
        return None


@dataclass
class _RotState:
    session_id: str
    patient_id: str
    exercise_id: str
    goal_deg: float
    arm: str
    reps_target: int | None = None
    set_index: int = 0
    in_set: bool = False
    paused: bool = False
    rep: int = 0
    angles: list[float] = field(default_factory=list)
    flags_next: list[str] = field(default_factory=list)
    drift_since: float | None = None
    drift_armed: bool = True
    counter: RepCounter = field(init=False)

    def __post_init__(self) -> None:
        self.counter = RepCounter(self.goal_deg)

    @property
    def counting(self) -> bool:
        return self.in_set and not self.paused

    def new_set(self, set_index: int) -> None:
        self.set_index, self.rep, self.angles, self.flags_next = set_index, 0, [], []
        self.counter = RepCounter(self.goal_deg)
        self.in_set = False


class RotationCoach:
    def __init__(self, hub: Any, bridge: Any, sessions: SessionRegistry,
                 db_provider: Callable[[], Any | None] = _default_db) -> None:
        self.hub = hub
        self.bridge = bridge
        self.sessions = sessions
        self.db_provider = db_provider
        self._states: dict[str, _RotState] = {}
        self._skip: set[str] = set()

    # ---- events (bridge.subscribe_events) -------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        sid = ev.get("session_id")
        kind = ev.get("type")
        if not isinstance(sid, str) or sid in self._skip or (kind == "rep" and ev.get("source") == "watch"):
            return
        try:
            st = self._states.get(sid) or await self._open(sid, ev)
            if st is None:
                return
            if kind == "countdown":
                self._on_countdown(st, ev)
            elif kind == "cue" and ev.get("id") == "set_start":
                st.in_set = True
            elif kind == "paused":
                st.paused = True
            elif kind == "resumed":
                st.paused = False
            elif kind == "wrist":
                self._on_wrist(st, ev)
            elif kind == "set_done":
                self._on_set_done(st, ev)
            elif kind == "stopped":
                st.in_set = False
        except Exception:  # noqa: BLE001 - one bad event must never stop the coach
            log.exception("rotation coach failed on event", extra={"event_type": kind, "session_id": sid})

    async def _open(self, sid: str, ev: dict[str, Any]) -> _RotState | None:
        session = self.sessions.get(sid)
        exercise_id = session.exercise_id if session is not None else ev.get("exercise_id")
        if session is None:
            return None  # not a session we know (yet)
        if not exercises.is_watch_exercise(exercise_id):
            if len(self._skip) >= MAX_SKIPPED:
                self._skip.clear()
            self._skip.add(sid)
            return None
        goal, reps = await self._prescription(session.patient_id, str(exercise_id))
        self._states.clear()  # one live session at a time
        st = self._states[sid] = _RotState(sid, session.patient_id, str(exercise_id), goal,
                                           watch_arm(session.profile.device_prefs), reps_target=reps)
        return st

    async def _prescription(self, patient_id: str, exercise_id: str) -> tuple[float, int | None]:
        db = self.db_provider()
        if db is None:
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        try:
            rows = await asyncio.wait_for(queries.ro_prescriptions(db, patient_id, PRESCRIPTION_TIMEOUT_S),
                                          PRESCRIPTION_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a missing/slow prescription → the default goal
            log.info("rotation: default goal", extra={"patient_id": patient_id, "error": repr(exc)})
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        row = next((r for r in rows if r.get("exercise_id") == exercise_id), None)
        if row is None:
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        reps = row.get("reps")
        return exercises.rotation_goal_deg(row.get("targets")), int(reps) if isinstance(reps, int) else None

    def _on_countdown(self, st: _RotState, ev: dict[str, Any]) -> None:
        set_no = ev.get("set")
        set_index = set_no if isinstance(set_no, int) else max(st.set_index, 1)
        if set_index != st.set_index:
            st.new_set(set_index)
        if isinstance(ev.get("reps_target"), int):
            st.reps_target = ev["reps_target"]
        if ev.get("n") == 1:
            st.in_set = True

    def _on_wrist(self, st: _RotState, ev: dict[str, Any]) -> None:
        deg = _number(ev.get("rotation_deg"))
        if deg is None or not st.counting:
            return
        peak = st.counter.update(deg)
        if peak is not None:
            self._rep(st, peak, ev.get("t"))

    def _rep(self, st: _RotState, peak: float, t: Any) -> None:
        st.rep += 1
        angle = round(peak, 1)
        reached = angle >= st.goal_deg
        st.angles.append(angle)
        when = _number(t) or round(time.time(), 3)
        self.hub.emit("rotation_rep", {"exercise_id": st.exercise_id, "set_index": st.set_index, "rep": st.rep,
                                       "reps_target": st.reps_target, "angle_deg": angle, "goal_deg": st.goal_deg,
                                       "reached": reached, "session_id": st.session_id, "t": when})
        flags, st.flags_next = st.flags_next, []
        self.bridge.inject({"type": "rep", "index": st.rep, "set": st.set_index, "exercise_id": st.exercise_id,
                            "quality": "good" if reached else "partial", "flags": flags, "peak_angle": angle,
                            "rom": angle, "source": "watch", "session_id": st.session_id, "t": when})

    def _on_set_done(self, st: _RotState, ev: dict[str, Any]) -> None:
        st.in_set = False
        set_no = ev.get("set") if isinstance(ev.get("set"), int) else st.set_index
        if st.angles:
            angles = list(st.angles)
            best = max(angles)
            avg = round(sum(angles) / len(angles), 1)
            self.hub.emit("rotation_set", {"exercise_id": st.exercise_id, "set_index": set_no, "angles": angles,
                                           "best_deg": best, "avg_deg": avg, "goal_deg": st.goal_deg,
                                           "session_id": st.session_id, "t": _number(ev.get("t")) or time.time()})
            db = self.db_provider()
            if db is not None:
                writes.fire(writes.upsert_rotation_set(db, st.session_id, st.patient_id, st.exercise_id, set_no,
                                                       angles, best, avg, st.goal_deg))
        st.new_set(set_no)

    # ---- frames (bridge.subscribe_frames; sync, cheap) ------------------------------------------------------

    def on_frame(self, frame: dict[str, Any]) -> None:
        sid = frame.get("session_id")
        st = self._states.get(sid) if isinstance(sid, str) else None
        if st is None:
            return
        t = _number(frame.get("t"))
        drift = elbow_drift_m(frame.get("joints"), st.arm)
        if not st.counting or t is None or drift is None:
            st.drift_since = None
            return
        if drift <= DRIFT_M:
            st.drift_since = None
            if drift < DRIFT_REARM_M:
                st.drift_armed = True
            return
        if st.drift_since is None:
            st.drift_since = t
        elif st.drift_armed and t - st.drift_since >= DRIFT_HOLD_S:
            st.drift_armed = False
            self._drift(st, t)

    def _drift(self, st: _RotState, t: float) -> None:
        self.bridge.inject({"type": "cue", "id": ELBOW_CUE_ID, "text": ELBOW_CUE_TEXT, "session_id": st.session_id,
                            "t": t})
        self.hub.emit("haptic", {"pattern": "fix_form", "session_id": st.session_id, "t": t})
        if ELBOW_CUE_ID not in st.flags_next:
            st.flags_next.append(ELBOW_CUE_ID)
