"""How a body can move: the rules the 3D avatar's rig applies on top of tracking.

All rules are expressed in body frames (left, up, forward) built from the hips and spine, so they
follow the person as they turn. Each function is pure; BodyRig strings them together.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FLEX_MAX_DEG = 150.0  # elbows and knees bend from straight (0) to this, one way only
TORSO_DEPTH_M = 0.09  # half depth of the torso box
LIMB_MARGIN_M = 0.04  # an arm stays this far outside the torso surface
PUSH_HYSTERESIS_M = 0.02  # keep last frame's torso face unless another is this much shorter a push
FOOT_YAW_LIMIT_DEG = 35.0  # toe-out / toe-in from the body's forward
FOOT_PITCH_DEG = (-30.0, 40.0)  # toe down .. toe up
FOOT_YAW_RATE_DPS = 240.0  # fastest a foot turns (8° per frame at 30 fps)
FOOT_YAW_SMOOTH_DEG = 60.0  # a yaw change this big passes at full speed; smaller ones are damped
FOOT_YAW_MIN_GAIN = 0.1  # fraction of a tiny yaw change applied per frame
FOOT_UNSURE_EASE_PER_S = 0.5  # unsure heel/toe: fraction of the way back to the body's facing per second
FLAT_HEIGHT_M = 0.03  # a foot this close to the floor lies flat
THIGH_BACK_DEG = 30.0  # thighs swing back at most this far behind the body
BODY_YAW_SMOOTH_DEG = 30.0  # body facing: turns this big pass, smaller wobbles are damped
BODY_YAW_MIN_GAIN = 0.2


@dataclass
class Frame:
    origin: np.ndarray
    left: np.ndarray
    up: np.ndarray
    fwd: np.ndarray

    def local(self, p) -> np.ndarray:
        r = np.asarray(p, float) - self.origin
        return np.array([r @ self.left, r @ self.up, r @ self.fwd])

    def world(self, q) -> np.ndarray:
        return self.origin + q[0] * self.left + q[1] * self.up + q[2] * self.fwd


def unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def body_frame(origin, left_hint, up) -> Frame | None:
    up = unit(np.asarray(up, float))
    if up is None:
        return None
    left = unit(np.asarray(left_hint, float) - (np.asarray(left_hint, float) @ up) * up)
    if left is None:
        return None
    return Frame(np.asarray(origin, float), left, up, np.cross(left, up))


def min_reach(l1: float, l2: float, flex_max_deg: float = FLEX_MAX_DEG) -> float:
    """Shortest root-to-end distance a hinge allows (interior angle 180 - flex_max)."""
    interior = np.radians(180.0 - flex_max_deg)
    return float(np.sqrt(l1 * l1 + l2 * l2 - 2 * l1 * l2 * np.cos(interior)))


def hinge_pole(root, target, pole, allowed) -> np.ndarray:
    """A pole on the allowed side of the root-target line: a joint detected bending the wrong way is
    mirrored, a straight limb bends toward `allowed`."""
    root, target, allowed = (np.asarray(x, float) for x in (root, target, allowed))
    d = unit(target - root)
    if d is None:
        return root + allowed
    a = unit(allowed - (allowed @ d) * d)
    if a is None:
        return np.asarray(pole, float) if pole is not None else root + allowed
    p = (np.asarray(pole, float) - root) if pole is not None and np.isfinite(pole).all() else a * 0.1
    along = (p @ d) * d
    perp = p - along
    if np.linalg.norm(perp) < 1e-3:
        perp = a * 0.1
    elif perp @ a < 0:
        perp = perp - 2 * (perp @ a) * a
    return root + along + perp


def limit_thigh(direction, pelvis: Frame) -> np.ndarray:
    """Keep a thigh from swinging back further than THIGH_BACK_DEG behind the body."""
    q = np.array([direction @ pelvis.left, direction @ pelvis.up, direction @ pelvis.fwd])
    back = np.degrees(np.arctan2(-q[2], max(-q[1], 1e-9)))
    if q[1] < 0 and back > THIGH_BACK_DEG:
        s = np.hypot(q[1], q[2])
        a = np.radians(THIGH_BACK_DEG)
        q[1], q[2] = -s * np.cos(a), -s * np.sin(a)
        return unit(q[0] * pelvis.left + q[1] * pelvis.up + q[2] * pelvis.fwd)
    return direction


def push_out_of_torso(p, torso: Frame, half_width: float, length: float, side: float,
                      depth: float = TORSO_DEPTH_M, margin: float = LIMB_MARGIN_M,
                      prefer: str | None = None) -> tuple[np.ndarray, bool, str | None]:
    """Move a point that is inside the torso box (plus margin) out of it: through the front face when it
    is in front of the torso's mid-plane, otherwise through its own arm's side face (`side` is +1 for the
    left arm, -1 for the right), so a hand crossing the chest stays in front instead of jumping to the far
    side. `prefer` (the face used last frame) wins while its push is within PUSH_HYSTERESIS_M of the
    chosen face's, so a point near the boundary doesn't flicker between faces.
    Returns (point, moved, face) with face "front", "side" or None."""
    q = torso.local(p)
    w, d = half_width + margin, depth + margin
    if not (0.0 < q[1] < length and abs(q[0]) < w and abs(q[2]) < d):
        return np.asarray(p, float), False, None
    push = {"front": d - q[2], "side": abs(side * w - q[0])}
    face = "front" if q[2] >= 0 else "side"
    if prefer in push and push[prefer] <= push[face] + PUSH_HYSTERESIS_M:
        face = prefer
    if face == "front":
        q[2] = d
    else:
        q[0] = side * w
    return torso.world(q), True, face


def foot_yaw(tracked_dir, body_fwd, prev_yaw: float | None, dt: float, locked_yaw: float | None,
             confident: bool) -> float:
    """Yaw (radians, atan2(x, z) on the floor) for a foot: the tracked direction limited to
    FOOT_YAW_LIMIT_DEG from the body's forward; when not confident, the previous yaw easing back toward
    the body's facing (FOOT_UNSURE_EASE_PER_S); the locked yaw while anchored. The limit always holds, so
    a turning body drags the foot along. Changes are smoothed and turn no faster than FOOT_YAW_RATE_DPS."""
    ref = float(np.arctan2(body_fwd[0], body_fwd[2]))
    lim = np.radians(FOOT_YAW_LIMIT_DEG)

    def within_limit(yaw: float) -> float:
        return ref + float(np.clip((yaw - ref + np.pi) % (2 * np.pi) - np.pi, -lim, lim))

    easing = False
    if locked_yaw is not None:
        want = within_limit(locked_yaw)  # anchored, but dragged along if the body turns past the limit
    elif confident and tracked_dir is not None and np.hypot(tracked_dir[0], tracked_dir[2]) > 1e-6:
        want = within_limit(float(np.arctan2(tracked_dir[0], tracked_dir[2])))
    elif prev_yaw is not None:  # unsure: ease back toward the body's facing, dragged along past the limit
        diff = (ref - prev_yaw + np.pi) % (2 * np.pi) - np.pi
        want = within_limit(prev_yaw + diff * min(FOOT_UNSURE_EASE_PER_S * max(dt, 0.0), 1.0))
        easing = True
    else:
        want = ref
    if prev_yaw is None:
        return want
    step = (want - prev_yaw + np.pi) % (2 * np.pi) - np.pi
    # adaptive smoothing: small (noisy) changes are damped, big (real) turns go through faster;
    # the unsure ease is already gradual, so it is only rate-capped
    gain = 1.0 if easing else float(np.clip(abs(step) / np.radians(FOOT_YAW_SMOOTH_DEG), FOOT_YAW_MIN_GAIN, 1.0))
    cap = np.radians(FOOT_YAW_RATE_DPS) * max(dt, 1e-3)
    return prev_yaw + float(np.clip(gain * step, -cap, cap))


def smooth_angle(prev: float | None, new: float, smooth_deg: float, min_gain: float) -> float:
    """Adaptive smoothing of an angle (radians): changes smaller than smooth_deg are damped (down to
    min_gain of the change per frame), larger ones pass through."""
    if prev is None:
        return new
    step = (new - prev + np.pi) % (2 * np.pi) - np.pi
    return prev + float(np.clip(abs(step) / np.radians(smooth_deg), min_gain, 1.0)) * step


def foot_pitch(tracked_dir, flat: bool) -> float:
    """Pitch (radians, toe up positive) within ankle range; 0 when the foot is flat on the floor."""
    if flat or tracked_dir is None:
        return 0.0
    pitch = float(np.degrees(np.arctan2(tracked_dir[1], np.hypot(tracked_dir[0], tracked_dir[2]))))
    return float(np.radians(np.clip(pitch, *FOOT_PITCH_DEG)))
