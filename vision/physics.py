"""Cautious camera-only estimates of body COM and foot loading.

Coordinates are the mat frame: x toward the patient's left, y up, z toward
Mira. These are kinematic estimates, not measurements of ground reaction force.
In particular, camera landmarks cannot resolve pressure within either foot.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from vision.types import FrameResult

G = 9.81
# Approximate adult Dempster segment mass fractions, normalized below.
# These are generic population values; body-specific COM needs measurement.
SEGMENTS = (
    (0.028, 11, 13, 0.436), (0.028, 12, 14, 0.436),
    (0.016, 13, 15, 0.430), (0.016, 14, 16, 0.430),
    (0.006, 15, 19, 0.5), (0.006, 16, 20, 0.5),
    (0.100, 23, 25, 0.433), (0.100, 24, 26, 0.433),
    (0.0465, 25, 27, 0.433), (0.0465, 26, 28, 0.433),
    (0.0145, 29, 31, 0.5), (0.0145, 30, 32, 0.5),
)
TOTAL_MASS = 0.081 + 0.497 + sum(segment[0] for segment in SEGMENTS)
FOOT = {"l": (27, 29, 31), "r": (28, 30, 32)}
MIN_JOINT_CONF = 0.45
MAX_GAP_S = 0.25


@dataclass(frozen=True)
class PhysicsResult:
    """Percentages: left of both feet; heel of *that* foot's estimated load."""

    com: tuple[float, float, float] | None = None
    cop: tuple[float, float] | None = None
    load_l_pct: float | None = None
    heel_l_pct: float | None = None
    heel_r_pct: float | None = None
    load_conf: float = 0.0
    contact_l: bool = False
    contact_r: bool = False
    stance: str = "unknown"  # double | left | right | none | floor | unknown


def _point(joints: np.ndarray, conf: np.ndarray, i: int) -> np.ndarray | None:
    return joints[i] if np.isfinite(joints[i]).all() and np.isfinite(conf[i]) and conf[i] >= MIN_JOINT_CONF else None


def _com(joints: np.ndarray, conf: np.ndarray) -> tuple[np.ndarray | None, float]:
    hip = [_point(joints, conf, i) for i in (23, 24)]
    shoulder = [_point(joints, conf, i) for i in (11, 12)]
    if any(p is None for p in (*hip, *shoulder)):
        return None, 0.0
    h, s = np.mean(hip, axis=0), np.mean(shoulder, axis=0)
    weighted = [(0.497, (h + s) / 2)]
    ears = [_point(joints, conf, i) for i in (7, 8)]
    if all(p is not None for p in ears):
        weighted.append((0.081, np.mean(ears, axis=0)))
    elif _point(joints, conf, 0) is not None:
        weighted.append((0.081, joints[0]))
    for mass, a, b, fraction in SEGMENTS:
        p, q = _point(joints, conf, a), _point(joints, conf, b)
        if p is not None and q is not None:
            weighted.append((mass, p + fraction * (q - p)))
    coverage = sum(m for m, _ in weighted) / TOTAL_MASS
    if coverage < 0.80:
        return None, coverage
    return sum((m * p for m, p in weighted), np.zeros(3)) / sum(m for m, _ in weighted), min(coverage, 1.0)


def _foot(joints: np.ndarray, conf: np.ndarray, side: str):
    ankle, heel, toe = (_point(joints, conf, i) for i in FOOT[side])
    if heel is None or toe is None or ankle is None:
        return None
    if max(heel[1], toe[1]) > 0.13 or min(heel[1], toe[1]) < -0.08:
        return None
    axis = (toe - heel)[[0, 2]]
    length = float(np.linalg.norm(axis))
    if not 0.10 <= length <= 0.40:
        return None
    center = (heel[[0, 2]] + toe[[0, 2]]) / 2
    return center, heel[[0, 2]], toe[[0, 2]], min(conf[list(FOOT[side])])


class PhysicsEstimator:
    """Stateful per-person estimator. Call once per frame in timestamp order.

    ``estimate`` is pure with respect to the frame. ``apply`` also writes its
    six existing physics fields. Reset between people, sessions, or camera moves.
    The dynamic term uses smoothed COM velocity; it is suppressed at startup,
    after gaps, and during motion too fast for a one-camera pressure estimate.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._t: float | None = None
        self._com_xz: np.ndarray | None = None
        self._velocity: np.ndarray | None = None
        self._foot_history = {side: deque() for side in FOOT}

    def _contact(self, side: str, foot, t: float):
        history = self._foot_history[side]
        if foot is None:
            history.clear()
            return None
        center = foot[0]
        if history and (t <= history[-1][0] or t - history[-1][0] > MAX_GAP_S):
            history.clear()
        history.append((t, center.copy()))
        while len(history) > 2 and t - history[1][0] >= 0.18:
            history.popleft()
        span = t - history[0][0]
        if span >= 0.12 and np.linalg.norm(center - history[0][1]) / span > 0.15:
            return None
        return foot

    def estimate(self, frame: FrameResult) -> PhysicsResult:
        if (frame.tracking == "lost" or frame.body != "ready" or
                any(flag in frame.flags for flag in ("pose_held", "camera_moved"))):
            self.reset()
            return PhysicsResult()
        joints, conf = np.asarray(frame.joints, float), np.asarray(frame.conf, float)
        if joints.shape != (33, 3) or conf.shape != (33,):
            self.reset()
            return PhysicsResult()
        com, coverage = _com(joints, conf)
        if com is None:
            self.reset()
            return PhysicsResult()
        com_out = tuple(float(x) for x in com)
        if self._t is not None and (frame.t <= self._t or frame.t - self._t > MAX_GAP_S
                                    or np.linalg.norm(com[[0, 2]] - self._com_xz) > 0.40):
            self.reset()  # restart, replay seek, or a different person/pose explanation
        feet = {side: self._contact(side, _foot(joints, conf, side), frame.t) for side in FOOT}
        contacts = {side: feet[side] is not None for side in FOOT}
        hip_y = float((joints[23, 1] + joints[24, 1]) / 2)
        shoulder_y = float((joints[11, 1] + joints[12, 1]) / 2)
        floor = hip_y < 0.65 and (shoulder_y < 0.55 or shoulder_y < hip_y + 0.15)
        if floor:
            self.reset()
            return PhysicsResult(com=com_out, stance="floor")
        if not any(contacts.values()):
            self.reset()
            return PhysicsResult(com=com_out, stance="none")

        stance = "double" if all(contacts.values()) else "left" if contacts["l"] else "right"
        xz = com[[0, 2]]
        velocity = acceleration = None
        dt = frame.t - self._t if self._t is not None else None
        if dt is not None and 0.01 <= dt <= MAX_GAP_S and self._com_xz is not None:
            raw_velocity = (xz - self._com_xz) / dt
            alpha = 1 - np.exp(-dt / 0.18)
            velocity = raw_velocity if self._velocity is None else self._velocity + alpha * (raw_velocity - self._velocity)
            if self._velocity is not None:
                acceleration = (velocity - self._velocity) / dt
        self._t, self._com_xz, self._velocity = frame.t, xz.copy(), velocity
        speed = float(np.linalg.norm(velocity)) if velocity is not None else 0.0
        accel = float(np.linalg.norm(acceleration)) if acceleration is not None else 0.0
        if speed > 0.65 or accel > 2.5:
            return PhysicsResult(com=com_out, contact_l=contacts["l"], contact_r=contacts["r"], stance=stance)

        # Linear inverted-pendulum approximation. Bound its effect because
        # camera depth noise can create spurious acceleration.
        correction = np.zeros(2) if acceleration is None else np.clip(com[1] / G * acceleration, -0.08, 0.08)
        cop = xz - correction
        foot_quality = min(float(feet[s][3]) for s in FOOT if contacts[s])
        confidence = min(0.65, 0.65 * coverage * foot_quality) * max(0.15, 1 - speed / 0.8) * max(0.2, 1 - accel / 3)
        if frame.tracking.startswith("single_cam_"):
            confidence *= 0.8
        if dt is None or dt > MAX_GAP_S:
            confidence *= 0.75

        if stance == "double":
            left, right = feet["l"][0], feet["r"][0]
            between = left - right
            separation = float(np.linalg.norm(between))
            if separation < 0.12:
                return PhysicsResult(com=com_out, contact_l=True, contact_r=True, stance=stance)
            fraction = float(np.dot(cop - right, between) / separation**2)
            # Outside the support span may be a real lean or a depth error.
            if fraction < -0.2 or fraction > 1.2:
                return PhysicsResult(com=com_out, contact_l=True, contact_r=True, stance=stance)
            confidence *= max(0.4, 1 - max(-fraction, fraction - 1, 0) * 2)
            load = 100 * float(np.clip(fraction, 0, 1))
        else:
            load = 100.0 if stance == "left" else 0.0
            if np.linalg.norm(cop - feet[stance[0]][0]) > 0.28:
                confidence *= 0.35

        heels = {}
        for side in FOOT:
            if feet[side] is None:
                heels[side] = None
                continue
            if stance == "double" and ((side == "l" and load < 10) or (side == "r" and load > 90)):
                heels[side] = None  # little inferred load: its heel fraction has no meaning
                continue
            _, heel, toe, _ = feet[side]
            axis = toe - heel
            fraction = float(np.dot(cop - heel, axis) / np.dot(axis, axis))
            heels[side] = 100 * float(np.clip(1 - fraction, 0, 1))
            if fraction < -0.5 or fraction > 1.5:
                heels[side] = None
                confidence *= 0.6
        return PhysicsResult(com_out, tuple(float(v) for v in cop), load, heels["l"], heels["r"],
                             float(np.clip(confidence, 0, 1)), contacts["l"], contacts["r"], stance)

    def apply(self, frame: FrameResult) -> PhysicsResult:
        result = self.estimate(frame)
        frame.com, frame.cop = result.com, result.cop
        frame.load_l_pct, frame.heel_l_pct, frame.heel_r_pct = result.load_l_pct, result.heel_l_pct, result.heel_r_pct
        frame.load_conf = result.load_conf
        return result
