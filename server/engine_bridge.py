"""Engine bridge: runs a source (mock | mock:fall | replay:<file> | live) in a thread or a spawned
process, and fans its §4.5 dicts out to the ring buffer, the hub and subscribers on the event loop.

Sources run off the loop and hand messages over with `loop.call_soon_threadsafe` (thread mode) or a
`multiprocessing.Queue` read by a bridge thread (process mode). Everything after that runs on the loop.
"""

from __future__ import annotations

import asyncio
import json
import math
import multiprocessing
import queue
import random
import statistics
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from server import exercises
from server.config import cfg
from server.logging_setup import get_logger

if TYPE_CHECKING:
    from server.hub import Hub

log = get_logger("bridge")

Message = dict[str, Any]
Emit = Callable[[Message], None]
FrameCallback = Callable[[Message], None]
EventCallback = Callable[[Message], Awaitable[None]]
EventEnricher = Callable[[Message], None]

FRAME_HZ = 30.0
LIVE_HZ = 10.0
RING_SECONDS = 60.0
FPS_WINDOW_S = 2.0
LAG_INTERVAL_S = 0.1
LAG_WINDOW = 600  # samples: 60 s at one sample per 0.1 s
LAG_WARN_P95_MS = 50.0
FALL_STATUS_EVERY_S = 2.0
MOCK_FALL_AT_S = 20.0
MOCK_ARRIVE_AT_S = 3.0  # simulated seconds after start when the mock patient arrives
MOCK_GREETING_HOLD_S = 3.0  # simulated seconds between arrived and awaiting_gesture (the LCD greeting shows)
MOCK_GESTURE_AFTER_S = 4.0  # simulated seconds between awaiting_gesture and the mock's thumbs-up
JOIN_TIMEOUT_S = 2.0

# Reverse-channel message types understood by the mock source (beyond incident_closed).
TRIGGER_FALL = "trigger_fall"
INCIDENT_CLOSED = "incident_closed"
SESSION_START = "session_start"  # a new live session started (REST or boot); the mock restarts its program


class Inbox(Protocol):
    def get_nowait(self) -> Any: ...


class StopFlag(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class Source(Protocol):
    """Runs off the event loop until `stop` is set, calling `emit` with §4.5 dicts."""

    def run(self, emit: Emit, inbox: Inbox, stop: StopFlag) -> None: ...


@dataclass(frozen=True)
class SourceSpec:
    """Picklable description of a source, so process mode can rebuild it in the child."""

    source: str
    speed: float = 1.0
    responds_at_s: float | None = None
    fall_at_s: float = MOCK_FALL_AT_S


# --------------------------------------------------------------------------------------------------
# Scripted fall (shared by the mock source and the loop-side injector used for replay/live)
# --------------------------------------------------------------------------------------------------


class FallScript:
    """Fall → fall_status every 2 s → optional thumbs_up `responds_at_s` seconds after the fall."""

    def __init__(self, responds_at_s: float | None, context: dict[str, Any]) -> None:
        self.responds_at_s = responds_at_s
        self.context = context
        self._started = False
        self._responded = False
        self._next_status_s = FALL_STATUS_EVERY_S

    @property
    def responded(self) -> bool:
        return self._responded

    def due(self, elapsed_s: float) -> list[Message]:
        """Events that are due `elapsed_s` seconds after the fall (each returned once)."""
        out: list[Message] = []
        if not self._started:
            self._started = True
            out.append({"type": "fall", "stage": "confirmed", "drop_m": 0.9, "peak_down_speed_mps": 2.1,
                        "posture": "unknown", "on_mat": True, **self.context})
        if self.responds_at_s is not None and not self._responded and elapsed_s >= self.responds_at_s:
            self._responded = True
            out.append({"type": "gesture", "name": "thumbs_up"})
        while elapsed_s >= self._next_status_s:
            still_s = 0.0 if self._responded else self._next_status_s
            out.append({"type": "fall_status", "posture": "unknown", "moving": self._responded,
                        "still_s": round(still_s, 1), "on_mat": True})
            self._next_status_s += FALL_STATUS_EVERY_S
        return out


# --------------------------------------------------------------------------------------------------
# Mock kinematics: 33 BlazePose landmarks, metres, y up from the floor, -z forward
# --------------------------------------------------------------------------------------------------

Vec = tuple[float, float, float]

_SHANK, _THIGH, _TORSO = 0.42, 0.43, 0.50
_ANKLE_HALF, _HIP_HALF, _SHOULDER_HALF = 0.13, 0.10, 0.19
_UPPER_ARM, _FOREARM = 0.27, 0.25


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mid(a: Vec, b: Vec) -> Vec:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)


def _angle_deg(a: Vec, vertex: Vec, b: Vec) -> float:
    u, v = _sub(a, vertex), _sub(b, vertex)
    dot = sum(x * y for x, y in zip(u, v, strict=True))
    norm = math.dist(a, vertex) * math.dist(b, vertex)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norm))))


def _flexion_deg(a: Vec, vertex: Vec, b: Vec) -> float:
    """Joint flexion: 180 minus the interior angle, so a straight joint is 0 and a 90 degree bend is 90."""
    return 180.0 - _angle_deg(a, vertex, b)


def _leg(side: float, depth: float, knee_in: float) -> tuple[Vec, Vec, Vec, Vec, Vec]:
    """Ankle, knee, hip, heel, foot index for one leg. side=+1 left, -1 right; depth 0..1."""
    shank_tilt = math.radians(35 * depth)
    knee_flex = 175 - 85 * depth
    thigh_tilt = math.radians(180 - knee_flex) - shank_tilt
    ankle: Vec = (side * _ANKLE_HALF, 0.08, 0.0)
    knee: Vec = (side * (_ANKLE_HALF - knee_in * depth), 0.08 + _SHANK * math.cos(shank_tilt),
                 -_SHANK * math.sin(shank_tilt))
    hip: Vec = (side * _HIP_HALF, knee[1] + _THIGH * math.cos(thigh_tilt), knee[2] + _THIGH * math.sin(thigh_tilt))
    heel = _add(ankle, (0.0, -0.05, 0.06))
    toe = _add(ankle, (0.0, -0.06, -0.15))
    return ankle, knee, hip, heel, toe


def _upper_body(pelvis: Vec, lean_deg: float, arm_raise_deg: float) -> dict[int, Vec]:
    lean = math.radians(lean_deg)
    chest: Vec = _add(pelvis, (0.0, _TORSO * math.cos(lean), -_TORSO * math.sin(lean)))
    head = _add(chest, (0.0, 0.20 * math.cos(lean * 0.6), -0.20 * math.sin(lean * 0.6) - 0.05))
    pts: dict[int, Vec] = {0: head}
    face = {1: (0.02, 0.03, 0.0), 2: (0.035, 0.035, 0.0), 3: (0.05, 0.03, 0.0), 4: (-0.02, 0.03, 0.0),
            5: (-0.035, 0.035, 0.0), 6: (-0.05, 0.03, 0.0), 7: (0.08, 0.01, 0.06), 8: (-0.08, 0.01, 0.06),
            9: (0.02, -0.035, 0.0), 10: (-0.02, -0.035, 0.0)}
    for idx, off in face.items():
        pts[idx] = _add(head, off)
    arm = math.radians(arm_raise_deg)
    reach: Vec = (0.0, -math.cos(arm), -math.sin(arm))
    for side, (sh, el, wr, pinky, index, thumb) in ((1.0, (11, 13, 15, 17, 19, 21)), (-1.0, (12, 14, 16, 18, 20, 22))):
        shoulder = _add(chest, (side * _SHOULDER_HALF, 0.0, 0.0))
        elbow = _add(shoulder, (reach[0], _UPPER_ARM * reach[1], _UPPER_ARM * reach[2]))
        wrist = _add(elbow, (0.0, _FOREARM * reach[1], _FOREARM * reach[2]))
        pts[sh], pts[el], pts[wr] = shoulder, elbow, wrist
        pts[pinky] = _add(wrist, (side * 0.03, -0.06, -0.02))
        pts[index] = _add(wrist, (side * 0.01, -0.07, -0.03))
        pts[thumb] = _add(wrist, (-side * 0.02, -0.04, -0.03))
    return pts


def squat_frame_body(depth_l: float, depth_r: float, knee_in: float, rng: random.Random) -> Message:
    """Joints, confidence, angles, com, loads and flags for one squat pose."""
    legs = {side: _leg(side, d, knee_in) for side, d in ((1.0, depth_l), (-1.0, depth_r))}
    pelvis = _mid(legs[1.0][2], legs[-1.0][2])
    depth = (depth_l + depth_r) / 2
    pts = _upper_body(pelvis, lean_deg=8 + 32 * depth, arm_raise_deg=20 + 60 * depth)
    for side, (a, k, h, he, to) in ((1.0, (27, 25, 23, 29, 31)), (-1.0, (28, 26, 24, 30, 32))):
        pts[a], pts[k], pts[h], pts[he], pts[to] = legs[side]
    joints = [[round(c + rng.gauss(0, 0.002), 4) for c in pts[i]] for i in range(33)]
    chest = _mid(pts[11], pts[12])
    trunk = _sub(chest, pelvis)
    knee_ratio = abs(pts[25][0] - pts[26][0]) / abs(pts[27][0] - pts[28][0])
    angles = {
        # Flexion convention (README "Frame contract"): 0 = straight, larger = deeper bend.
        "knee_l": round(_flexion_deg(pts[23], pts[25], pts[27]), 1),
        "knee_r": round(_flexion_deg(pts[24], pts[26], pts[28]), 1),
        "hip_l": round(_flexion_deg(pts[11], pts[23], pts[25]), 1),
        "hip_r": round(_flexion_deg(pts[12], pts[24], pts[26]), 1),
        "trunk_lean": round(math.degrees(math.atan2(-trunk[2], trunk[1])), 1),
        "trunk_side": round(1.0 + rng.gauss(0, 0.3), 1),
        "pelvis_rot": round(2.0 + rng.gauss(0, 0.5), 1),
        "knee_ratio": round(knee_ratio, 2),
    }
    knee_mid = _mid(pts[25], pts[26])
    com = [round(0.75 * p + 0.15 * c + 0.10 * k, 3) for p, c, k in zip(pelvis, chest, knee_mid, strict=True)]
    confidence = [round(min(0.99, 0.95 + rng.gauss(0, 0.015)), 3) for _ in range(33)]
    # Right knee is the operated side: Maya shifts weight to the left as she goes down.
    load_l = 50 + 9 * depth + rng.gauss(0, 0.8)
    return {
        "joints": joints,
        "confidence": confidence,
        "angles": angles,
        "com": com,
        "load_l_pct": round(load_l, 1),
        "heel_l_pct": round(44 - 6 * depth + rng.gauss(0, 0.5), 1),
        "heel_r_pct": round(47 - 4 * depth + rng.gauss(0, 0.5), 1),
        "confidence_avg": round(sum(confidence) / 33, 3),
        "flags": ["knee_cave_l"] if knee_ratio < 0.85 else [],
    }


def lying_frame_body(moving: bool, rng: random.Random) -> Message:
    """Lying on the back on the mat, head forward (-z). `moving` adds limb motion."""
    wobble = 0.03 if moving else 0.003
    base: dict[int, Vec] = {0: (0.0, 0.14, -0.78)}
    for i, off in enumerate([(0.02, 0.17, -0.76), (0.035, 0.17, -0.76), (0.05, 0.16, -0.76), (-0.02, 0.17, -0.76),
                             (-0.035, 0.17, -0.76), (-0.05, 0.16, -0.76), (0.08, 0.12, -0.74), (-0.08, 0.12, -0.74),
                             (0.02, 0.15, -0.81), (-0.02, 0.15, -0.81)], start=1):
        base[i] = off
    for side, (sh, el, wr, pi, ix, th, hp, kn, an, he, to) in (
        (1.0, (11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31)),
        (-1.0, (12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32)),
    ):
        base[sh] = (side * 0.19, 0.12, -0.55)
        base[el] = (side * 0.26, 0.08, -0.30)
        base[wr] = (side * 0.28, 0.07, -0.06)
        base[pi] = (side * 0.30, 0.06, 0.0)
        base[ix] = (side * 0.29, 0.06, 0.01)
        base[th] = (side * 0.26, 0.07, -0.01)
        base[hp] = (side * 0.10, 0.11, -0.05)
        base[kn] = (side * 0.12, 0.12, 0.38)
        base[an] = (side * 0.13, 0.09, 0.80)
        base[he] = (side * 0.13, 0.06, 0.84)
        base[to] = (side * 0.14, 0.12, 0.90)
    joints = [[round(c + rng.gauss(0, wobble), 4) for c in base[i]] for i in range(33)]
    confidence = [round(min(0.95, 0.80 + rng.gauss(0, 0.04)), 3) for _ in range(33)]
    return {
        "joints": joints,
        "confidence": confidence,
        "angles": {"knee_l": 8.0, "knee_r": 9.0, "hip_l": 5.0, "hip_r": 6.0, "trunk_lean": 88.0,
                   "trunk_side": 2.0, "pelvis_rot": 4.0, "knee_ratio": 0.92},
        "com": [0.0, 0.12, -0.15],
        "load_l_pct": 0.0,
        "heel_l_pct": 0.0,
        "heel_r_pct": 0.0,
        "confidence_avg": round(sum(confidence) / 33, 3),
        "flags": [],
    }


# --------------------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------------------

_COUNTDOWN_S = 3.0
_REP_S = 3.0
_REPS = 10
_SETS = 3
_REST_S = 10.0
_SET_S = _COUNTDOWN_S + _REPS * _REP_S + _REST_S
_PROGRAM_S = _SETS * _SET_S
_KNEE_CAVE_REP = 5
_EXERCISE = "squat"


def _program_schedule() -> list[tuple[float, Message]]:
    """Events of one loop of the mock squat program, as (seconds from loop start, payload)."""
    events: list[tuple[float, Message]] = []
    for set_no in range(1, _SETS + 1):
        base = (set_no - 1) * _SET_S
        for i, n in enumerate((3, 2, 1)):
            events.append((base + i, {"type": "countdown", "n": n, "set": set_no, "reps_target": _REPS,
                                      "sets_target": _SETS, "exercise_id": _EXERCISE}))
        start = base + _COUNTDOWN_S
        events.append((start, {"type": "cue", "id": "set_start", "text": "Nice and slow. Let's begin.",
                               "audio_url": "/audio/set_start.mp3"}))
        for rep in range(1, _REPS + 1):
            caved = rep == _KNEE_CAVE_REP
            if caved:
                events.append((start + (rep - 0.5) * _REP_S,
                               {"type": "cue", "id": "knees_out", "text": "Push your knees out over your toes.",
                                "audio_url": "/audio/knees_out.mp3"}))
            events.append((start + rep * _REP_S, {
                "type": "rep", "index": rep, "set": set_no, "exercise_id": _EXERCISE,
                "quality": "fix_form" if caved else "good", "flags": ["knee_cave_l"] if caved else []}))
        events.append((start + _REPS * _REP_S, {"type": "set_done", "set": set_no, "reps": _REPS,
                                                "exercise_id": _EXERCISE}))
    return sorted(events, key=lambda e: e[0])


@dataclass
class _ProgramPose:
    set_no: int
    rep: int  # reps completed in this set
    depth: float
    knee_in: float
    active: bool


def _program_pose(p: float) -> _ProgramPose:
    local = p % _PROGRAM_S
    set_no = int(local // _SET_S) + 1
    q = local % _SET_S - _COUNTDOWN_S
    if q < 0:
        return _ProgramPose(set_no, 0, 0.0, 0.0, False)
    if q >= _REPS * _REP_S:
        return _ProgramPose(set_no, _REPS, 0.0, 0.0, False)
    r = q / _REP_S
    done = int(r)
    depth = (1 - math.cos(2 * math.pi * (r - done))) / 2
    return _ProgramPose(set_no, done, depth, 0.03 if done + 1 == _KNEE_CAVE_REP else 0.0, True)


class MockSource:
    """Looping squat program at 30 Hz; `fall_at_s` scripts a fall (mock:fall); reverse queue handled."""

    def __init__(self, *, speed: float = 1.0, fall_at_s: float | None = None,
                 responds_at_s: float | None = None, seed: int = 7) -> None:
        self.speed = speed
        self.fall_at_s = fall_at_s
        self.responds_at_s = responds_at_s
        self._rng = random.Random(seed)
        self._schedule = _program_schedule()
        self._seq = 0
        self._t0 = 0.0
        self._sim_s = 0.0
        self._program_s = 0.0
        self._prev_program_s = -1e-9
        self._fall: FallScript | None = None
        self._fall_started_s = 0.0
        self._auto_fall_done = False
        self._arrived = False
        self._arrive_due_s = MOCK_ARRIVE_AT_S
        self._awaiting = False
        self._awaiting_due_s = 0.0
        self._ready = False
        self._gesture_due_s = 0.0
        self._patient_id = cfg.MOCK_PATIENT_ID
        self._paused = False  # user `pause`/`start` (§ reverse channel); independent of a Guardian `paused`
        self._stopped = False  # user `stop`: the program is over, no more reps for this session

    def run(self, emit: Emit, inbox: Inbox, stop: StopFlag) -> None:
        dt = 1.0 / FRAME_HZ
        self._t0 = time.time()
        next_tick = time.monotonic()
        while not stop.is_set():
            self._drain(emit, inbox)
            # The mock patient steps in front of the device once per run, a few seconds after start (so clients
            # that connect at boot see the greeting); the squat program starts on arrival. A new live session
            # (REST or boot) restarts this via `_restart_session`, so later sessions get their own arrival too.
            if not self._arrived and self._sim_s >= self._arrive_due_s:
                self._arrived = True
                # The plan rides on `arrived` so the greeting can show "3 sets · 10 reps" before the first countdown.
                self._emit(emit, {"type": "arrived", "patient_id": self._patient_id, "exercise_id": _EXERCISE,
                                  "reps_target": _REPS, "sets_target": _SETS})
                self._awaiting_due_s = self._sim_s + MOCK_GREETING_HOLD_S
            # Like the vision engine: after the greeting has been on screen a moment, ask for a thumbs-up, and
            # start the program once it is seen.
            if self._arrived and not self._awaiting and self._fall is None and self._sim_s >= self._awaiting_due_s:
                self._awaiting = True
                self._emit(emit, {"type": "awaiting_gesture", "gesture": "thumbs_up"})
                self._gesture_due_s = self._sim_s + MOCK_GESTURE_AFTER_S
            if self._awaiting and not self._ready and self._fall is None and self._sim_s >= self._gesture_due_s:
                self._ready = True
                self._emit(emit, {"type": "gesture", "name": "thumbs_up"})
            if self.fall_at_s is not None and not self._auto_fall_done and self._sim_s >= self.fall_at_s:
                self._auto_fall_done = True
                self._start_fall(self.responds_at_s)
            if self._fall is not None:
                body = lying_frame_body(self._fall.responded, self._rng)
                pose = _program_pose(self._program_s)
                events = self._fall.due(self._sim_s - self._fall_started_s)
            elif self._stopped or self._paused:
                # Standing still: a user `pause` or `stop` freezes the program but the camera keeps streaming.
                pose = _program_pose(self._program_s)
                body = squat_frame_body(0.0, 0.0, 0.0, self._rng)
                events = []
            else:
                pose = _program_pose(self._program_s)
                body = squat_frame_body(pose.depth, pose.depth * 0.9, pose.knee_in, self._rng)
                events = list(self._events_between(self._prev_program_s, self._program_s)) if self._ready else []
                if self._ready:
                    self._prev_program_s = self._program_s
                if self._ready and not cfg.MOCK_LOOP and self._program_s >= _PROGRAM_S:
                    # The program is over: one `stopped`, then stand still (like a user stop) until the next
                    # session.
                    self._stopped = True
                    events.append({"type": "stopped", "reason": "program_complete"})
            self._emit(emit, {"type": "frame", "exercise_id": _EXERCISE, "set_no": pose.set_no, **body})
            for ev in events:
                self._emit(emit, ev)
            if int(self._sim_s) != int(self._sim_s + dt):
                self._emit(emit, {"type": "hr", "bpm": self._heart_rate(pose)})

            self._sim_s += dt
            if self._fall is None and self._ready and not self._paused and not self._stopped:
                self._program_s += dt
            next_tick += dt / self.speed
            delay = next_tick - time.monotonic()
            if delay < -1.0:  # fell far behind (laptop sleep, debugger): resync instead of bursting
                next_tick = time.monotonic()
            elif delay > 0:
                stop.wait(delay)

    def _drain(self, emit: Emit, inbox: Inbox) -> None:
        while True:
            try:
                msg = inbox.get_nowait()
            except queue.Empty:
                return
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == TRIGGER_FALL and self._fall is None:
                self._start_fall(msg.get("responds_at_s"))
            elif kind == INCIDENT_CLOSED and self._fall is not None:
                self._fall = None
                self._program_s = math.ceil(self._program_s / _SET_S) * _SET_S  # resume at the next set
            elif kind == SESSION_START:
                self._restart_session(msg.get("patient_id"))
            elif kind == "command":
                self._on_command(emit, msg.get("name"))

    def _on_command(self, emit: Emit, name: str | None) -> None:
        """A client session command (start/pause/skip/stop), forwarded by `main._forward_session_command`.
        Ignored while a scripted fall is in progress: the Guardian already owns the frames then."""
        if self._fall is not None:
            return
        if name == "pause":
            if not self._paused and not self._stopped:
                self._paused = True
                self._emit(emit, {"type": "paused", "reason": "user"})
        elif name == "start":
            if self._paused and not self._stopped:
                self._paused = False
                self._emit(emit, {"type": "resumed"})
        elif name == "skip":
            if not self._stopped:
                self._skip_set(emit)
        elif name == "stop":
            if not self._stopped:
                self._stopped = True
                self._paused = False
                self._emit(emit, {"type": "stopped"})

    def _skip_set(self, emit: Emit) -> None:
        """End the current set now (a partial `set_done`) and jump straight to the next set's countdown,
        without replaying the rest of this set's schedule (mirrors how `incident_closed` resumes)."""
        pose = _program_pose(self._program_s)
        self._emit(emit, {"type": "set_done", "set": pose.set_no, "reps": pose.rep, "exercise_id": _EXERCISE})
        self._program_s = math.ceil((self._program_s + 1e-6) / _SET_S) * _SET_S
        self._prev_program_s = self._program_s - 1e-9

    def _restart_session(self, patient_id: str | None) -> None:
        """A new live session started: fresh rep/set counters and a fresh `arrived` MOCK_ARRIVE_AT_S seconds
        from now, so `server.ai.session_voice` greets this session too. The ring buffer and any in-progress
        fall script (mock:fall) are left alone."""
        self._patient_id = patient_id or cfg.MOCK_PATIENT_ID
        self._arrived = False
        self._arrive_due_s = self._sim_s + MOCK_ARRIVE_AT_S
        self._awaiting = False
        self._ready = False
        self._program_s = 0.0
        self._prev_program_s = -1e-9
        self._paused = False
        self._stopped = False

    def _start_fall(self, responds_at_s: float | None) -> None:
        pose = _program_pose(self._program_s)
        self._fall_started_s = self._sim_s
        self._fall = FallScript(responds_at_s, {"exercise_id": _EXERCISE, "set": pose.set_no, "rep": pose.rep + 1})

    def _events_between(self, prev: float, cur: float) -> Iterator[Message]:
        for n in range(int(prev // _PROGRAM_S), int(cur // _PROGRAM_S) + 1):
            for at, payload in self._schedule:
                if prev < n * _PROGRAM_S + at <= cur:
                    yield dict(payload)

    def _heart_rate(self, pose: _ProgramPose) -> int:
        if self._fall is not None:
            return 112 + self._rng.randint(-2, 2)
        effort = pose.rep / _REPS if pose.active else 0.2
        return int(92 + 14 * effort + self._rng.randint(-2, 2))

    def _emit(self, emit: Emit, msg: Message) -> None:
        self._seq += 1
        msg["seq"] = self._seq
        msg["t"] = round(self._t0 + self._sim_s, 3)
        emit(msg)


class ReplaySource:
    """Plays a recorded jsonl (one §4.5 dict per line) at recorded speed, looping, re-stamped to now."""

    def __init__(self, path: Path, *, speed: float = 1.0) -> None:
        self.path = path
        self.speed = speed

    def run(self, emit: Emit, inbox: Inbox, stop: StopFlag) -> None:
        if not self.path.is_file():
            log.error("replay file not found", extra={"path": str(self.path)})
            return
        seq = 0
        while not stop.is_set():
            wall0, rec0 = time.time(), None
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    if stop.is_set():
                        return
                    _drain_ignore(inbox)
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    rec_t = float(msg.get("t", 0.0))
                    rec0 = rec_t if rec0 is None else rec0
                    target = wall0 + (rec_t - rec0) / self.speed
                    delay = target - time.time()
                    if delay > 0 and stop.wait(delay):
                        return
                    seq += 1
                    msg.pop("session_id", None)
                    msg["seq"] = seq
                    msg["t"] = round(wall0 + (rec_t - rec0), 3)
                    emit(msg)
            if rec0 is None:
                log.error("replay file is empty", extra={"path": str(self.path)})
                return


class LiveSource:
    """Eric's engine. Imported lazily so the server runs without vision dependencies."""

    def run(self, emit: Emit, inbox: Inbox, stop: StopFlag) -> None:
        try:
            from vision import Engine  # type: ignore[import-not-found]
        except ImportError as exc:
            log.error("MIRA_SOURCE=live but vision.Engine is not importable; no frames will flow. "
                      "Install the vision package or use MIRA_SOURCE=mock", extra={"error": repr(exc)})
            return
        Engine(emit=emit, inbox=inbox).run(stop)


class RemoteSource:
    """A remote vision agent delivers frames through ``/ws/vision`` instead of being imported here."""

    def run(self, emit: Emit, inbox: Inbox, stop: StopFlag) -> None:  # noqa: ARG002 - bridge owns ingress
        stop.wait()


def _drain_ignore(inbox: Inbox) -> None:
    while True:
        try:
            inbox.get_nowait()
        except queue.Empty:
            return


def make_source(spec: SourceSpec) -> Source:
    name = spec.source
    if name == "mock":
        return MockSource(speed=spec.speed)
    if name == "mock:fall":
        return MockSource(speed=spec.speed, fall_at_s=spec.fall_at_s, responds_at_s=spec.responds_at_s)
    if name.startswith("replay:"):
        return ReplaySource(Path(name.removeprefix("replay:")), speed=spec.speed)
    if name == "live":
        return LiveSource()
    if name == "remote":
        return RemoteSource()
    raise ValueError(f"unknown MIRA_SOURCE {name!r} (mock | mock:fall | replay:<file> | live | remote)")


def _process_main(spec: SourceSpec, out_q: Any, in_q: Any, stop: Any) -> None:
    """Child-process entry point (spawn). Drops messages when the parent falls behind; seq shows the gap."""

    def emit(msg: Message) -> None:
        try:
            out_q.put_nowait(msg)
        except queue.Full:
            pass

    make_source(spec).run(emit, in_q, stop)


# --------------------------------------------------------------------------------------------------
# Loop-side helpers
# --------------------------------------------------------------------------------------------------


class RingBuffer:
    """The last `seconds` of frames by engine time, stored unchanged."""

    def __init__(self, seconds: float = RING_SECONDS) -> None:
        self.seconds = seconds
        self._frames: deque[Message] = deque()

    def append(self, frame: Message) -> None:
        self._frames.append(frame)
        horizon = frame["t"] - self.seconds
        while self._frames and self._frames[0]["t"] < horizon:
            self._frames.popleft()

    def frames_since(self, t: float) -> list[Message]:
        out: list[Message] = []
        for frame in reversed(self._frames):
            if frame["t"] < t:
                break
            out.append(frame)
        out.reverse()
        return out

    def latest(self) -> Message | None:
        return self._frames[-1] if self._frames else None

    def __len__(self) -> int:
        return len(self._frames)

    def span_s(self) -> float:
        return self._frames[-1]["t"] - self._frames[0]["t"] if len(self._frames) > 1 else 0.0


class LagMonitor:
    """Sleeps 0.1 s repeatedly and records how late the loop wakes up (ms)."""

    def __init__(self, interval_s: float = LAG_INTERVAL_S, window: int = LAG_WINDOW) -> None:
        self.interval_s = interval_s
        self._samples: deque[float] = deque(maxlen=window)
        self._count = 0

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(self.interval_s)
            self._samples.append(max(0.0, (loop.time() - start - self.interval_s) * 1000))
            self._count += 1
            if self._count % LAG_WINDOW == 0:
                self._log_summary()

    def stats(self) -> dict[str, float | int | None]:
        if not self._samples:
            return {"p50": None, "p95": None, "max": None, "samples": 0}
        ordered = sorted(self._samples)
        p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
        return {"p50": round(statistics.median(ordered), 2), "p95": round(p95, 2),
                "max": round(ordered[-1], 2), "samples": len(ordered)}

    def _log_summary(self) -> None:
        stats = self.stats()
        if (stats["p95"] or 0) > LAG_WARN_P95_MS:
            log.warning("event loop lag high; consider ENGINE_MODE=process", extra={"event_loop_lag_ms": stats})
        else:
            log.info("event loop lag", extra={"event_loop_lag_ms": stats})


# Display names for the LCD and the app ("REPS · MINI SQUATS"); unknown ids are title-cased.
EXERCISE_NAMES = {e.exercise_id: e.exercise_name for e in exercises.EXERCISES.values()}  # kept for callers
CAPTION_SOURCES = {"greeting": "text", "cue": "text", "summary_ready": "spoken", "say": "text"}
CLOSED_ALERT_STEPS = frozenset({"resolved", "closed", "resolved_by_therapist"})


@dataclass
class LiveState:
    """The /ws/live payload, maintained from frames and events."""

    phase: str = "idle"  # idle | countdown | active | rest | paused | incident | done
    exercise_id: str | None = None
    rep: int = 0
    reps_target: int | None = None
    set: int | None = None
    sets_target: int | None = None
    countdown: int | None = None
    main_angle: float | None = None
    load_l_pct: float | None = None
    load_conf: float | None = None
    hr: int | None = None
    flags: list[str] = field(default_factory=list)
    cue_id: str | None = None
    paused: bool = False
    caption: dict[str, Any] | None = None  # {text, source, t}: what Mira last said (late joiners repaint it)
    first_name: str | None = None
    exercise_name: str | None = None
    incident: bool = False
    device_prefs: dict[str, Any] = field(default_factory=dict)  # captions/volume/slower_speech (Plan 3 §6.5)

    def on_session_start(self, first_name: str, exercise_id: str, device_prefs: dict[str, Any] | None = None) -> None:
        self.first_name = first_name
        self.exercise_id = exercise_id
        self.exercise_name = exercises.display_name(exercise_id)
        self.device_prefs = device_prefs or {}
        self.caption = None
        self.incident = False
        self.phase = "idle"
        self.rep = 0
        self.paused = False
        self.countdown = None
        self.cue_id = None
        self.set = None
        self.reps_target = None
        self.sets_target = None
        self.hr = None
        self.load_l_pct = None
        self.load_conf = None
        self.main_angle = None
        self.flags = []

    def on_agent_event(self, ev: Message) -> None:
        """Caption and incident only. Agent events (hub.emit) come here directly; they must not move phase,
        rep or cue_id (a Coach focus cue during the countdown would otherwise end the countdown)."""
        kind = ev.get("type")
        text_key = CAPTION_SOURCES.get(str(kind))
        text = ev.get(text_key) if text_key else None
        if isinstance(text, str) and text.strip():
            self.caption = {"text": text.strip(), "source": kind, "t": ev.get("t", round(time.time(), 3))}
        if (kind == "fall" and ev.get("stage") == "confirmed") or (kind == "cue" and ev.get("id") == "check_in"):
            self.incident = True
        elif kind == "alert":
            # Only `detected` opens and only a closing step ends it: steps that arrive after the close (Retell
            # call_*/webhook_*, a late checked_in/escalated) leave it as it is.
            step = ev.get("step")
            if step == "detected":
                self.incident = True
            elif step in CLOSED_ALERT_STEPS:
                self.incident = False

    def on_frame(self, frame: Message) -> None:
        angles = frame.get("angles") or {}
        knees = [a for a in (angles.get("knee_l"), angles.get("knee_r")) if isinstance(a, (int, float))]
        self.main_angle = round(max(knees), 1) if knees else None  # deepest bend (flexion)
        self.load_l_pct = frame.get("load_l_pct")
        self.load_conf = frame.get("confidence_avg")
        self.flags = list(frame.get("flags") or [])
        self.exercise_id = frame.get("exercise_id", self.exercise_id)
        self.set = frame.get("set_no", self.set)

    def on_event(self, ev: Message) -> None:
        self.on_agent_event(ev)
        kind = ev.get("type")
        for key in ("reps_target", "sets_target", "exercise_id"):
            if key in ev:
                setattr(self, key, ev[key])
        if kind == "countdown":
            self.phase, self.countdown, self.rep = "countdown", ev.get("n"), 0
            self.set = ev.get("set", self.set)
        elif kind == "cue":
            self.cue_id = ev.get("id")
            if self.phase == "countdown":
                self.phase, self.countdown = "active", None
        elif kind == "rep":
            self.phase, self.rep = "active", int(ev.get("index", self.rep))
        elif kind == "set_done":
            self.phase = "rest"
        elif kind == "paused":
            self.phase, self.paused = "paused", True
        elif kind == "resumed":
            self.phase, self.paused = "active", False
        elif kind == "stopped":
            self.phase = "done"
        elif kind == "fall":
            self.phase = "incident"
        elif kind == INCIDENT_CLOSED:
            self.phase, self.incident = "idle", False
        elif kind == "hr" and isinstance(ev.get("bpm"), (int, float)):
            self.hr = int(ev["bpm"])

    def payload(self) -> Message:
        return {"t": round(time.time(), 3), "phase": self.phase, "exercise_id": self.exercise_id, "rep": self.rep,
                "reps_target": self.reps_target, "set": self.set, "sets_target": self.sets_target,
                "countdown": self.countdown, "main_angle": self.main_angle, "load_l_pct": self.load_l_pct,
                "load_conf": self.load_conf, "hr": self.hr, "flags": self.flags, "cue_id": self.cue_id,
                "paused": self.paused, "caption": self.caption, "first_name": self.first_name,
                "exercise_name": self.exercise_name, "incident": self.incident,
                "device_prefs": self.device_prefs}


@dataclass(eq=False)
class _EventSubscriber:
    callback: EventCallback
    queue: asyncio.Queue[Message] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None


# --------------------------------------------------------------------------------------------------
# The bridge
# --------------------------------------------------------------------------------------------------


class EngineBridge:
    def __init__(self, hub: Hub, loop: asyncio.AbstractEventLoop, *, source: str | None = None,
                 mode: str | None = None, responds_at_s: float | None = None, speed: float = 1.0,
                 fall_at_s: float = MOCK_FALL_AT_S,
                 session_provider: Callable[[], str | None] | None = None) -> None:
        self.hub = hub
        self.loop = loop
        self.source = source if source is not None else cfg.MIRA_SOURCE
        self.mode = mode if mode is not None else cfg.ENGINE_MODE
        if self.mode not in ("thread", "process"):
            raise ValueError(f"ENGINE_MODE must be thread or process, got {self.mode!r}")
        self.speed = speed
        self.spec = SourceSpec(self.source, speed, responds_at_s if responds_at_s is not None
                               else cfg.MOCK_FALL_RESPONDS_AT, fall_at_s)
        self.session_provider = session_provider
        self.ring_buffer = RingBuffer()
        self.live = LiveState()
        hub.emit_observers.append(self.live.on_agent_event)
        self.lag = LagMonitor()

        self._frame_subs: list[FrameCallback] = []
        self._event_subs: list[_EventSubscriber] = []
        self._enrichers: list[EventEnricher] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._stop: Any = None
        self._inbox: Any = None
        self._worker: threading.Thread | None = None
        self._process: Any = None
        self._reader: threading.Thread | None = None
        self._fall_injector: asyncio.Task[None] | None = None
        self._fall_injector_closed: asyncio.Event | None = None
        self._remote_sender: Callable[[Message], Awaitable[None]] | None = None
        # A connected camera is deliberately inert until the patient's phone starts its session.
        self._remote_active_session_id: str | None = None

        self._last_seq: int | None = None
        self._inject_seq = 0
        self._seq_gaps = 0
        self._seq_missing = 0
        self._engine_restarts = 0
        self._frames_total = 0
        self._events_total = 0
        self._frame_times: deque[float] = deque()
        self._subscriber_errors = 0

    # ---- lifecycle -------------------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        make_source(self.spec)  # validate the source name before spawning anything
        for sub in self._event_subs:
            self._start_subscriber(sub)
        self._tasks += [asyncio.create_task(self.lag.run(), name="lag-monitor"),
                        asyncio.create_task(self._publish_live(), name="live-publisher")]
        if self.mode == "process":
            self._start_process()
        else:
            self._start_thread()
        log.info("engine bridge started", extra={"source": self.source, "mode": self.mode})

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stop is not None:
            self._stop.set()
        for th in (self._worker, self._reader):
            if th is not None:
                await asyncio.to_thread(th.join, JOIN_TIMEOUT_S)
        if self._process is not None:
            await asyncio.to_thread(self._process.join, JOIN_TIMEOUT_S)
            if self._process.is_alive():
                self._process.terminate()
            self._inbox.close()
            self._inbox.cancel_join_thread()  # don't block interpreter exit on unread reverse messages
        tasks = [*self._tasks, *(s.task for s in self._event_subs if s.task), *([self._fall_injector]
                 if self._fall_injector else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("engine bridge stopped")

    def _start_thread(self) -> None:
        self._stop = threading.Event()
        self._inbox = queue.Queue()
        source = make_source(self.spec)
        self._worker = threading.Thread(target=self._run_source, args=(source,), name="engine-source", daemon=True)
        self._worker.start()

    def _run_source(self, source: Source) -> None:
        def emit(msg: Message) -> None:
            self.loop.call_soon_threadsafe(self._on_engine_msg, msg)

        try:
            source.run(emit, self._inbox, self._stop)
        except RuntimeError as exc:  # loop closed during shutdown
            if self._running:
                log.error("engine source crashed", extra={"error": repr(exc)})
        except Exception:  # noqa: BLE001 - keep the server alive; frames stop and /stats shows it
            log.exception("engine source crashed")

    def _start_process(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        self._stop = ctx.Event()
        self._inbox = ctx.Queue()
        out_q = ctx.Queue(maxsize=int(FRAME_HZ * 10))
        self._process = ctx.Process(target=_process_main, args=(self.spec, out_q, self._inbox, self._stop),
                                    name="engine-process", daemon=True)
        self._process.start()
        self._reader = threading.Thread(target=self._read_process, args=(out_q,), name="engine-reader", daemon=True)
        self._reader.start()

    def _read_process(self, out_q: Any) -> None:
        while not self._stop.is_set():
            try:
                msg = out_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.loop.call_soon_threadsafe(self._on_engine_msg, msg)
            except RuntimeError:
                return

    # ---- public API ------------------------------------------------------------------------------

    def subscribe_frames(self, cb: FrameCallback) -> None:
        """`cb(frame)` runs on the loop for every frame; it must not block (e.g. FrameWriter.submit)."""
        self._frame_subs.append(cb)

    def subscribe_events(self, cb: EventCallback) -> None:
        """`await cb(event)` for every event, in order, from a dedicated task per subscriber."""
        sub = _EventSubscriber(cb)
        self._event_subs.append(sub)
        if self._running:
            self._start_subscriber(sub)

    def add_event_enricher(self, fn: EventEnricher) -> None:
        """`fn(event)` may add fields to every event before the hub and subscribers see it. Runs on the loop
        for each event, so it must be fast and must not block (in-memory lookups only)."""
        self._enrichers.append(fn)

    def inject(self, msg: Message) -> None:
        """Route a non-engine message (WS client, demo tools) like an engine event. Call on the loop."""
        msg = dict(msg)
        msg.setdefault("t", round(time.time(), 3))
        if "seq" not in msg:
            self._inject_seq += 1
            msg["seq"] = self._inject_seq
        self._route(msg)

    def ingest_remote(self, msg: Message) -> None:
        """Route one authenticated remote-agent message through the normal engine pipeline.

        Remote frames are ignored while idle. This prevents a camera that is merely connected from collecting or
        displaying movement before the patient starts a session in the iOS app.
        """
        if self.source == "remote":
            active = self._remote_active_session_id
            current = self.session_provider() if self.session_provider else None
            if active is None or current != active:
                return
            supplied = msg.get("session_id")
            if supplied is not None and supplied != active:
                return
        self._on_engine_msg(dict(msg))
        if self.source == "remote" and msg.get("type") == "stopped":
            self._remote_active_session_id = None

    def set_remote_sender(self, sender: Callable[[Message], Awaitable[None]] | None) -> None:
        """Attach the currently connected remote agent's reverse channel (one agent at a time)."""
        self._remote_sender = sender

    def clear_remote_sender(self, sender: Callable[[Message], Awaitable[None]]) -> None:
        """Detach only if this connection is still the active reverse channel."""
        if self._remote_sender is sender:
            self._remote_sender = None

    def send_to_engine(self, msg: Message) -> None:
        """Reverse channel to the engine, e.g. {"type": "incident_closed"}."""
        if msg.get("type") == INCIDENT_CLOSED:
            self.live.on_event(msg)
            if self._fall_injector_closed is not None:
                self._fall_injector_closed.set()
        if self.source == "remote" and msg.get("type") == "command" and msg.get("name") == "start":
            session_id = msg.get("session_id")
            if isinstance(session_id, str):
                self._remote_active_session_id = session_id
        if self._remote_sender is not None:
            self.loop.create_task(self._remote_sender(dict(msg)), name="remote-vision-command")
        elif self._inbox is not None and self._running:
            self._inbox.put_nowait(msg)

    def notify_session_start(self, patient_id: str) -> None:
        """A new live session started (SessionRegistry.on_start hook). In mock modes, restart the mock
        program so it emits a fresh `arrived` (and the squat program) for this session too; live/replay
        sources are unaffected."""
        if self.source.startswith("mock"):
            self.send_to_engine({"type": SESSION_START, "patient_id": patient_id})

    async def trigger_fall(self, responds_at_s: float | None) -> None:
        """Inject the scripted mock:fall sequence into the current session."""
        if self.source.startswith("mock"):
            self.send_to_engine({"type": TRIGGER_FALL, "responds_at_s": responds_at_s})
            return
        if self._fall_injector is not None and not self._fall_injector.done():
            return
        self._fall_injector_closed = asyncio.Event()
        self._fall_injector = asyncio.create_task(self._inject_fall(responds_at_s, self._fall_injector_closed))

    def stats(self) -> dict[str, Any]:
        self._trim_frame_times()
        return {
            "source": self.source,
            "mode": self.mode,
            "running": self._running,
            "fps": round(len(self._frame_times) / FPS_WINDOW_S, 1),
            "frames_total": self._frames_total,
            "events_total": self._events_total,
            "seq_gaps": self._seq_gaps,
            "dropped": self._seq_missing,
            "engine_restarts": self._engine_restarts,
            "ring_frames": len(self.ring_buffer),
            "ring_span_s": round(self.ring_buffer.span_s(), 2),
            "subscriber_errors": self._subscriber_errors,
            "event_loop_lag_ms": self.lag.stats(),
        }

    # ---- routing (loop thread) -------------------------------------------------------------------

    def _on_engine_msg(self, msg: Message) -> None:
        seq = msg.get("seq")
        if isinstance(seq, int):
            if self._last_seq is not None:
                if seq > self._last_seq + 1:
                    self._seq_gaps += 1
                    self._seq_missing += seq - self._last_seq - 1
                elif seq <= self._last_seq:
                    self._engine_restarts += 1
            self._last_seq = seq
        self._route(msg)

    def _route(self, msg: Message) -> None:
        if msg.get("session_id") is None:
            msg["session_id"] = self.session_provider() if self.session_provider else None
        if msg.get("type") == "frame":
            self._frames_total += 1
            self._frame_times.append(time.monotonic())
            self._trim_frame_times()
            self.ring_buffer.append(msg)
            self.live.on_frame(msg)
            self.hub.publish("pose", msg)
            for cb in self._frame_subs:
                try:
                    cb(msg)
                except Exception:  # noqa: BLE001 - a subscriber bug must not stop the stream
                    self._subscriber_failed("frame subscriber failed")
            return
        self._events_total += 1
        for fn in self._enrichers:
            try:
                fn(msg)
            except Exception:  # noqa: BLE001 - an enricher bug must not stop the stream
                self._subscriber_failed("event enricher failed")
        self.live.on_event(msg)
        self.hub.publish("events", msg)
        for sub in self._event_subs:
            sub.queue.put_nowait(msg)

    def _trim_frame_times(self) -> None:
        horizon = time.monotonic() - FPS_WINDOW_S
        while self._frame_times and self._frame_times[0] < horizon:
            self._frame_times.popleft()

    def _subscriber_failed(self, what: str) -> None:
        self._subscriber_errors += 1
        if self._subscriber_errors == 1 or self._subscriber_errors % 300 == 0:
            log.exception(what, extra={"subscriber_errors": self._subscriber_errors})

    def _start_subscriber(self, sub: _EventSubscriber) -> None:
        sub.task = asyncio.create_task(self._deliver(sub), name="event-subscriber")

    async def _deliver(self, sub: _EventSubscriber) -> None:
        while True:
            msg = await sub.queue.get()
            try:
                await sub.callback(msg)
            except Exception:  # noqa: BLE001
                self._subscriber_failed("event subscriber failed")

    async def _publish_live(self) -> None:
        while True:
            self.hub.publish("live", self.live.payload())
            await asyncio.sleep(1.0 / LIVE_HZ)

    async def _inject_fall(self, responds_at_s: float | None, closed: asyncio.Event) -> None:
        latest = self.ring_buffer.latest() or {}
        script = FallScript(responds_at_s, {"exercise_id": latest.get("exercise_id"),
                                            "set": latest.get("set_no"), "rep": self.live.rep + 1})
        start = self.loop.time()
        while not closed.is_set():
            for ev in script.due((self.loop.time() - start) * self.speed):
                self.inject(ev)
            await asyncio.sleep(0.1 / self.speed)
