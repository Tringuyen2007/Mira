# Anatomical constraints for the 3D avatar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 3D avatar move like a body — hinge limits, no arms through the torso, feet that don't spin, confidence-weighted joints — and add `vision.avatar_eval` to measure it on recordings.

**Architecture:** Pure rule functions in a new `vision/constraints.py` (body frames, hinge poles, reach limits, thigh limit, torso push-out, foot yaw/pitch, angle smoothing). `BodyRig` gains a `constraints=True` posing path that strings them together (the old path stays as `constraints=False`), takes `conf` and `feet_locked`, and fixes posing on the measuring-complete frame. `vision/avatar_eval.py` replays recordings through the rig and prints the metrics. The debug view passes confidence and anchoring to the rig and adds the **c** key.

**Tech Stack:** Python 3.11, NumPy, OpenCV (drawing only), pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-vision-avatar-body-constraints-design.md` (including its "Addendum: tuned on the recordings").

Every code block below was run against a copy of the repo before this plan was written: the full suite passed (187 tests), every diff applies to the current branch, and `avatar_eval` on Eric's three recordings gave the addendum's numbers.

## Global Constraints

- Avatar only: `FrameResult.joints`, recordings and `checks` are unchanged.
- Hinges: elbows/knees 0–150° flexion, one way (knee forward of the hip–ankle line; elbow toward side·left − forward); thigh at most 30° behind the body.
- Torso box: shoulder half-width × torso length × 0.09 m half-depth, plus 0.04 m limb margin; elbow/wrist pushed to the nearest face, up to 3 rounds, then elbow swung sideways.
- Feet: yaw within ±35° of the smoothed body facing; uncertain heel/toe (conf < 0.5) holds the previous yaw; locked yaw while anchored; changes under 60° damped (min gain 0.1); max 240°/s; pitch −30°..+40°, 0 when anchored or ankle < 0.11 m.
- Body facing smoothing: changes under 30° damped (min gain 0.2).
- Confidence blend for limb ends: weight `clip((conf − 0.3) / 0.5, 0, 1)` toward the detection, the rest toward the previous pose.
- Bone lengths exactly fixed; the measuring-complete frame is posed.
- Run tests with `.venv/bin/python -m pytest` from the repo root.

## Review Focus

1. Live behaviour on the rig: the rules must not make the avatar lag visibly behind real movement (fast arm swings, quick turns); foot turns are allowed ~0.3 s of lag.
2. Arms crossed in front of the chest or hands together: pushing out of the torso must not yank the hands apart or make them jitter between front and side.
3. Single-camera frames (`single_cam_*`) and partially missing joints must still pose without NaNs or crashes.
4. Turning on the spot with anchored feet: locked foot yaw vs the ±35° limit when the body turns far — the foot must release cleanly when anchoring releases.
5. `avatar_eval` numbers must match what `debug_view` shows (same pipeline, `feet_locked` passed).

---

### Task 1: Body rules (`constraints.py`)

**Files:**
- Create: `vision/constraints.py`
- Create: `vision/tests/test_constraints.py`

**Interfaces:**
- Produces: `Frame(origin, left, up, fwd)` with `.local(p)`/`.world(q)`; `unit(v)`; `body_frame(origin, left_hint, up) -> Frame | None`; `min_reach(l1, l2, flex_max_deg=150)`; `hinge_pole(root, target, pole, allowed)`; `limit_thigh(direction, pelvis)`; `push_out_of_torso(p, torso, half_width, length, side, depth=0.09, margin=0.04) -> (p, moved)`; `foot_yaw(tracked_dir, body_fwd, prev_yaw, dt, locked_yaw, confident) -> radians`; `smooth_angle(prev, new, smooth_deg, min_gain)`; `foot_pitch(tracked_dir, flat)`; constants `FLEX_MAX_DEG, TORSO_DEPTH_M, LIMB_MARGIN_M, FOOT_YAW_LIMIT_DEG, FOOT_PITCH_DEG, FOOT_YAW_RATE_DPS, FOOT_YAW_SMOOTH_DEG, FOOT_YAW_MIN_GAIN, FLAT_HEIGHT_M, THIGH_BACK_DEG, BODY_YAW_SMOOTH_DEG, BODY_YAW_MIN_GAIN`.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_constraints.py`:

```python
import numpy as np

from vision import constraints as C

FRAME = C.body_frame(np.zeros(3), [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])  # left +x, up +y, forward +z


def test_body_frame_is_right_handed_and_follows_the_person():
    f = C.body_frame(np.zeros(3), [1.0, 0.2, 0.0], [0.0, 2.0, 0.0])
    np.testing.assert_allclose(f.up, [0, 1, 0])
    np.testing.assert_allclose(f.left, [1, 0, 0])
    np.testing.assert_allclose(f.fwd, [0, 0, 1])  # left x up = forward (toward Mira when facing it)
    p = np.array([0.3, 0.5, -0.2])
    np.testing.assert_allclose(f.world(f.local(p)), p)
    assert C.body_frame(np.zeros(3), [0, 1, 0], [0, 1, 0]) is None


def test_min_reach_matches_150_degree_flexion():
    d = C.min_reach(0.4, 0.4)
    assert abs(d - 2 * 0.4 * np.sin(np.radians(15))) < 1e-9  # interior angle 30°


def test_hinge_pole_mirrors_a_knee_bending_backwards():
    hip, ankle = np.zeros(3), np.array([0.0, -0.8, 0.0])
    backwards = np.array([0.0, -0.4, -0.1])  # knee behind the line
    pole = C.hinge_pole(hip, ankle, backwards, FRAME.fwd)
    assert pole[2] > 0 and abs(pole[1] + 0.4) < 1e-9
    forwards = np.array([0.0, -0.4, 0.1])
    np.testing.assert_allclose(C.hinge_pole(hip, ankle, forwards, FRAME.fwd), forwards)
    straight = C.hinge_pole(hip, ankle, None, FRAME.fwd)
    assert straight[2] > 0  # a straight or missing knee bends forward


def test_limit_thigh_stops_it_swinging_far_behind():
    back = C.unit(np.array([0.0, -0.5, -0.8]))  # ~58° behind
    out = C.limit_thigh(back, FRAME)
    angle = np.degrees(np.arctan2(-out[2], -out[1]))
    assert abs(angle - C.THIGH_BACK_DEG) < 1e-6 and abs(np.linalg.norm(out) - 1) < 1e-9
    fwd = C.unit(np.array([0.0, -0.5, 0.8]))
    np.testing.assert_allclose(C.limit_thigh(fwd, FRAME), fwd)


def test_push_out_of_torso_uses_the_nearest_face_and_side():
    hw, length = 0.18, 0.55
    inside_left = np.array([0.15, 0.3, 0.0])
    p, moved = C.push_out_of_torso(inside_left, FRAME, hw, length, side=1.0)
    assert moved and abs(p[0] - (hw + C.LIMB_MARGIN_M)) < 1e-9
    front = np.array([0.0, 0.3, 0.10])  # near the chest's front surface, at the midline
    p, moved = C.push_out_of_torso(front, FRAME, hw, length, side=-1.0)
    assert moved and abs(p[2] - (C.TORSO_DEPTH_M + C.LIMB_MARGIN_M)) < 1e-9
    outside = np.array([0.5, 0.3, 0.0])
    p, moved = C.push_out_of_torso(outside, FRAME, hw, length, side=1.0)
    assert not moved and np.allclose(p, outside)


def test_foot_yaw_is_limited_held_when_unsure_locked_and_rate_capped():
    fwd = np.array([0.0, 0.0, 1.0])
    out_60 = np.array([np.sin(np.radians(60)), 0.0, np.cos(np.radians(60))])
    y = C.foot_yaw(out_60, fwd, None, 1 / 30, None, True)
    assert abs(np.degrees(y) - C.FOOT_YAW_LIMIT_DEG) < 1e-9  # toe-out limited
    held = C.foot_yaw(None, fwd, np.radians(10), 1 / 30, None, False)
    assert abs(np.degrees(held) - 10) < 1e-9  # unsure: stays where it was
    locked = C.foot_yaw(out_60, fwd, np.radians(5), 1 / 30, np.radians(5), True)
    assert abs(np.degrees(locked) - 5) < 1e-9  # anchored: keeps its yaw
    step = C.foot_yaw(np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), 0.0, 1 / 30, None, True)
    assert abs(np.degrees(step)) <= C.FOOT_YAW_RATE_DPS / 30 + 1e-9  # a 90° jump is spread over frames


def test_foot_yaw_follows_a_real_turn_within_half_a_second():
    y = 0.0
    for k in range(40):
        a = np.radians(min(90, 12 * k))
        d = np.array([np.sin(a), 0.0, np.cos(a)])
        y = C.foot_yaw(d, d, y, 1 / 30, None, True)
    assert abs(np.degrees(y) - 90) < 5


def test_foot_pitch_range_and_flat():
    up = C.unit(np.array([0.0, 1.0, 0.3]))
    assert abs(np.degrees(C.foot_pitch(up, False)) - C.FOOT_PITCH_DEG[1]) < 1e-9
    assert C.foot_pitch(up, True) == 0.0 and C.foot_pitch(None, False) == 0.0


def test_smooth_angle_damps_wobble_and_passes_turns():
    assert C.smooth_angle(None, 1.0, 30, 0.2) == 1.0
    small = C.smooth_angle(0.0, np.radians(2), 30, 0.2)
    assert 0 < small < np.radians(2) * 0.25
    big = C.smooth_angle(0.0, np.radians(60), 30, 0.2)
    assert abs(big - np.radians(60)) < 1e-9
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_constraints.py -q`
Expected: FAIL at collection — `ImportError: cannot import name 'constraints' from 'vision'`.

- [ ] **Step 3: Implement**

Create `vision/constraints.py`:

```python
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
FOOT_YAW_LIMIT_DEG = 35.0  # toe-out / toe-in from the body's forward
FOOT_PITCH_DEG = (-30.0, 40.0)  # toe down .. toe up
FOOT_YAW_RATE_DPS = 240.0  # fastest a foot turns (8° per frame at 30 fps)
FOOT_YAW_SMOOTH_DEG = 60.0  # a yaw change this big passes at full speed; smaller ones are damped
FOOT_YAW_MIN_GAIN = 0.1  # fraction of a tiny yaw change applied per frame
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
                      depth: float = TORSO_DEPTH_M, margin: float = LIMB_MARGIN_M) -> tuple[np.ndarray, bool]:
    """Move a point that is inside the torso box (plus margin) out through the nearest side face.
    `side` is +1 for the left arm, -1 for the right (used when the point is near the midline)."""
    q = torso.local(p)
    w, d = half_width + margin, depth + margin
    if not (0.0 < q[1] < length and abs(q[0]) < w and abs(q[2]) < d):
        return np.asarray(p, float), False
    sign = np.sign(q[0]) if abs(q[0]) > 0.03 else side
    gaps = {"side": w - abs(q[0]), "front": d - q[2], "back": d + q[2]}
    face = min(gaps, key=gaps.get)
    if face == "side" or (sign != side and abs(q[0]) > 0.03):
        q[0] = sign * w
    elif face == "front":
        q[2] = d
    else:
        q[2] = -d
    return torso.world(q), True


def foot_yaw(tracked_dir, body_fwd, prev_yaw: float | None, dt: float, locked_yaw: float | None,
             confident: bool) -> float:
    """Yaw (radians, atan2(x, z) on the floor) for a foot: the tracked direction limited to
    FOOT_YAW_LIMIT_DEG from the body's forward; when not confident, the previous yaw (dragged along
    only if the body turns past the limit); the locked yaw while anchored; changes smoothed and
    turning no faster than FOOT_YAW_RATE_DPS."""
    ref = float(np.arctan2(body_fwd[0], body_fwd[2]))
    lim = np.radians(FOOT_YAW_LIMIT_DEG)

    def within_limit(yaw: float) -> float:
        return ref + float(np.clip((yaw - ref + np.pi) % (2 * np.pi) - np.pi, -lim, lim))

    if locked_yaw is not None:
        want = locked_yaw
    elif confident and tracked_dir is not None and np.hypot(tracked_dir[0], tracked_dir[2]) > 1e-6:
        want = within_limit(float(np.arctan2(tracked_dir[0], tracked_dir[2])))
    elif prev_yaw is not None:  # unsure: the foot stays put unless the body turns past the limit
        want = within_limit(prev_yaw)
    else:
        want = ref
    if prev_yaw is None:
        return want
    step = (want - prev_yaw + np.pi) % (2 * np.pi) - np.pi
    # adaptive smoothing: small (noisy) changes are damped, big (real) turns go through faster
    gain = float(np.clip(abs(step) / np.radians(FOOT_YAW_SMOOTH_DEG), FOOT_YAW_MIN_GAIN, 1.0))
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_constraints.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/constraints.py vision/tests/test_constraints.py
git commit -m "vision: body rules for the 3D avatar"
```

---

### Task 2: Constrained rig and the evaluation tool

**Files:**
- Modify (full replacement): `vision/rig.py`
- Modify: `vision/tests/test_rig.py`
- Create: `vision/avatar_eval.py`
- Create: `vision/tests/test_avatar_eval.py`

**Interfaces:**
- Consumes: `vision.constraints` (Task 1).
- Produces: `BodyRig(frames_needed=40, forget_s=5.0, constraints=True)`; `update(t, joints, tracking, conf=None, feet_locked=None)`; `avatar_eval.evaluate(outputs, rig) -> Report` with `.row(name)`, `HEADER`, `inside_torso(J, p)`, `hinge_violation(J, a, b, c, is_knee)`; CLI `python -m vision.avatar_eval FILE... [--plain]`.
- The two are one task because their tests use each other (`test_rig` uses `avatar_eval.inside_torso`; `test_avatar_eval` uses the constrained `BodyRig`).

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_rig.py`:

```diff
--- a/vision/tests/test_rig.py
+++ b/vision/tests/test_rig.py
@@ -134,3 +134,66 @@
     _measure(rig, X, t0=DT)
     P = rig.update(AFTER + DT, X + [0.0, 0.0, 0.01], "single_cam_B")
     assert P is not None and abs(_length(P, 25, 27) - rig.lengths["shin"]) < 1e-9
+
+
+def test_the_frame_that_completes_measuring_is_already_rigged():
+    rng = np.random.default_rng(3)
+    X = standing_skeleton()
+    rig = BodyRig()
+    out = None
+    for k in range(FRAMES_NEEDED):
+        J = X + rng.normal(0, 0.01, X.shape)
+        out = rig.update(k * DT, J, "3d")
+    assert rig.lengths is not None
+    assert abs(_length(out, 25, 27) - rig.lengths["shin"]) < 1e-9  # not the raw, wobbly frame
+
+
+def _rigged(rig, X, J):
+    _measure(rig, X)
+    return rig.update(AFTER, J, "3d")
+
+
+def test_constrained_knee_never_bends_backwards():
+    X = standing_skeleton()
+    J = X.copy()
+    J[23:25, 1] -= 0.15  # slight squat
+    J[25:27, 2] -= 0.12  # but the detected knees point backwards
+    P = _rigged(BodyRig(), X, J)
+    for hip, knee, ankle in ((23, 25, 27), (24, 26, 28)):
+        d = (P[ankle] - P[hip]) / np.linalg.norm(P[ankle] - P[hip])
+        off = (P[knee] - P[hip]) - ((P[knee] - P[hip]) @ d) * d
+        assert off[2] > 0  # knee in front of the hip-ankle line
+    plain = _rigged(BodyRig(constraints=False), X, J)
+    assert plain[25, 2] < P[25, 2]  # the plain rig follows the backwards knee
+
+
+def test_constrained_arm_is_kept_out_of_the_torso():
+    from vision.avatar_eval import inside_torso
+
+    X = standing_skeleton()
+    J = X.copy()
+    J[15] = (X[11] + X[23]) / 2 * [0.2, 1.0, 1.0]  # left wrist detected inside the belly
+    J[13] = (J[11] + J[15]) / 2 + [0.0, 0.0, -0.02]
+    rig = BodyRig()
+    P = _rigged(rig, X, J)
+    assert not inside_torso(P, P[15]) and not inside_torso(P, P[13])
+    assert abs(_length(P, 11, 13) - rig.lengths["upper_arm"]) < 1e-9
+    assert abs(_length(P, 13, 15) - rig.lengths["forearm"]) < 1e-9
+
+
+def test_constrained_feet_point_forward_and_stay_flat():
+    X = standing_skeleton()
+    J = X.copy()
+    J[31] = J[29] + [0.16, 0.0, 0.0]  # left toe detected pointing straight out sideways (90°)
+    P = _rigged(BodyRig(), X, J)
+    v = P[31] - P[29]
+    yaw = np.degrees(np.arctan2(v[0], v[2]))
+    assert yaw <= 35 + 1e-6 and abs(v[1]) < 1e-9  # within toe-out limit, flat on the floor
+
+
+def test_constrained_rig_follows_confident_tracking_closely():
+    X = standing_skeleton()
+    J = X.copy()
+    J[15] = X[15] + [0.05, 0.25, 0.25]  # left hand raised forward, reachable
+    P = _rigged(BodyRig(), X, J)
+    assert np.linalg.norm(P[15] - J[15]) < 0.02
```

Create `vision/tests/test_avatar_eval.py`:

```python
import numpy as np

from vision.avatar_eval import HEADER, evaluate, hinge_violation, inside_torso
from vision.pipeline import Pipeline
from vision.rig import BodyRig
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_inside_torso_detects_a_hand_in_the_belly_only():
    X = standing_skeleton()
    belly = (X[11] + X[12] + X[23] + X[24]) / 4
    assert inside_torso(X, belly)
    assert not inside_torso(X, X[15])  # hand hanging at the side
    assert not inside_torso(X, belly + [0.0, 0.0, 0.3])  # in front of the body


def test_hinge_violation_flags_a_backwards_knee_and_over_flexion():
    X = standing_skeleton()
    assert not hinge_violation(X, 23, 25, 27, True)
    back = X.copy()
    back[25] = back[25] + [0.0, 0.0, -0.08]
    assert hinge_violation(back, 23, 25, 27, True)
    folded = X.copy()
    folded[15] = folded[11] + [0.0, -0.01, 0.0]  # wrist almost back at the shoulder: elbow past 150°
    assert hinge_violation(folded, 11, 13, 15, False)


def test_evaluate_reports_a_still_synthetic_person_as_clean():
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X, noise_px=1.0, rng=rng),
                         pose_from_points(cams["B"], X, noise_px=1.0, rng=rng)) for k in range(90)]
    r = evaluate(iter(outs), BodyRig())
    assert r.frames > 30 and r.arm_in_torso_pct == 0 and r.hinge_pct == 0
    assert r.bone_std_mm < 1e-6 and r.foot_yaw_max < 10 and r.truth_mean_cm < 5
    assert "arm∈torso" in HEADER and r.row("synthetic").startswith("synthetic")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_rig.py vision/tests/test_avatar_eval.py -q`
Expected: FAIL — `test_avatar_eval.py` at collection (`ModuleNotFoundError: No module named 'vision.avatar_eval'`); run `.venv/bin/python -m pytest vision/tests/test_rig.py -q` alone to see the new constrained tests fail with `TypeError: BodyRig.__init__() got an unexpected keyword argument 'constraints'` (and the measuring-frame test fail on the shin length).

- [ ] **Step 3: Implement**

Replace `vision/rig.py` with:

```python
"""A rigged body for the 3D view: bone lengths measured once, then only the joints rotate.

MediaPipe estimates every joint afresh each frame, so bone lengths wobble by centimetres. BodyRig
first measures the person over FRAMES_NEEDED frames of 3D tracking (median per bone, left and right
averaged), then poses a fixed-proportion skeleton each frame:

- hips follow the tracked hip midpoint and direction; the spine, shoulders and head take their
  direction from tracking at the measured lengths;
- arms and legs use two-bone IK: with fixed bone lengths, each limb reaches for the tracked wrist or
  ankle (so an anchored foot stays put), bending toward the tracked elbow or knee;
- a joint missing for a moment holds its last pose relative to its parent.

Only the 3D view uses this. FrameResult.joints, recordings and checks stay as measured.
"""
from __future__ import annotations

import numpy as np

from vision import constraints as C

FRAMES_NEEDED = 40
FORGET_S = 5.0  # nobody tracked for this long: assume someone new and measure again

# measured bone -> joint pairs (left and right pooled)
BONES = {
    "upper_arm": [(11, 13), (12, 14)], "forearm": [(13, 15), (14, 16)],
    "thigh": [(23, 25), (24, 26)], "shin": [(25, 27), (26, 28)],
    "heel": [(27, 29), (28, 30)], "foot": [(29, 31), (30, 32)],
    "shoulder_width": [(11, 12)], "hip_width": [(23, 24)],
}
SIDES = {"l": dict(shoulder=11, elbow=13, wrist=15, hand=(17, 19, 21), hip=23, knee=25, ankle=27, heel=29, toe=31),
         "r": dict(shoulder=12, elbow=14, wrist=16, hand=(18, 20, 22), hip=24, knee=26, ankle=28, heel=30, toe=32)}
HEAD = list(range(0, 11))
FORWARD = np.array([0.0, 0.0, 1.0])


def _ok(*pts) -> bool:
    return all(p is not None and np.isfinite(p).all() for p in pts)


def _unit(v, fallback=None):
    n = np.linalg.norm(v) if v is not None and np.isfinite(v).all() else 0.0
    return v / n if n > 1e-9 else fallback


def two_bone_ik(root, target, pole, l1: float, l2: float, fallback_bend=None):
    """(middle joint, end joint) of a limb with bone lengths l1, l2 reaching from root toward target,
    bending toward pole. Out of reach: straight toward the target."""
    root, target = np.asarray(root, float), np.asarray(target, float)
    d = _unit(target - root, np.array([0.0, -1.0, 0.0]))
    dist = float(np.clip(np.linalg.norm(target - root), abs(l1 - l2) + 1e-6, l1 + l2 - 1e-9))
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = float(np.sqrt(max(l1 * l1 - a * a, 0.0)))
    bend = None
    if _ok(pole):
        p = np.asarray(pole, float) - root
        bend = _unit(p - (p @ d) * d)
    if bend is None and fallback_bend is not None:
        bend = _unit(fallback_bend - (fallback_bend @ d) * d)
    if bend is None:
        bend = _unit(FORWARD - (FORWARD @ d) * d, _unit(np.cross(d, [1.0, 0.0, 0.0])))
    mid = root + d * a + bend * h
    return mid, root + d * dist


class BodyRig:
    def __init__(self, frames_needed: int = FRAMES_NEEDED, forget_s: float = FORGET_S, constraints: bool = True):
        self.frames_needed, self.forget_s, self.constraints = frames_needed, forget_s, constraints
        self.reset()

    def reset(self) -> None:
        self.lengths: dict[str, float] | None = None
        self._samples: dict[str, list[float]] = {k: [] for k in [*BONES, "torso", "neck"]}
        self._frames = 0
        self._last_seen: float | None = None
        self._last_t: float | None = None
        self._last_out: np.ndarray | None = None
        self._held: dict[str, np.ndarray] = {}  # last good offsets for joints that go missing
        self._foot_yaw: dict[str, float] = {}
        self._locked_yaw: dict[str, float | None] = {"l": None, "r": None}
        self._prev_pose_t: float | None = None
        self._body_yaw: float | None = None

    @property
    def progress(self) -> float:
        return 1.0 if self.lengths is not None else min(self._frames / self.frames_needed, 1.0)

    def update(self, t: float, joints, tracking: str, conf=None, feet_locked: dict[str, bool] | None = None
               ) -> np.ndarray | None:
        """Rigged joints (33, 3) for this frame; the raw joints while measuring; None without a body."""
        if self._last_t is not None and t == self._last_t:  # the same frame drawn again
            return self._last_out
        self._last_t = t
        if self._last_seen is not None and t - self._last_seen > self.forget_s:
            self.reset()
            self._last_t = t
        if joints is None or tracking == "lost":
            self._last_out = None
            return None
        J = np.asarray(joints, float)
        self._last_seen = t
        if self.lengths is None:  # measure only from two-camera frames; pose in single-camera mode too
            if tracking == "3d":
                self._measure(J)
            if self.lengths is None:
                self._last_out = J.copy()
                return self._last_out
        if self.constraints:
            self._last_out = self._pose_constrained(J, conf, t, feet_locked or {})
        else:
            self._last_out = self._pose(J)
        self._prev_pose_t = t
        return self._last_out

    def _measure(self, J: np.ndarray) -> None:
        if not _ok(J[11], J[12], J[23], J[24]):
            return
        for name, pairs in BONES.items():
            self._samples[name] += [float(np.linalg.norm(J[a] - J[b])) for a, b in pairs if _ok(J[a], J[b])]
        hip_mid, sh_mid = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2
        self._samples["torso"].append(float(np.linalg.norm(sh_mid - hip_mid)))
        if _ok(J[7], J[8]):
            self._samples["neck"].append(float(np.linalg.norm((J[7] + J[8]) / 2 - sh_mid)))
        self._frames += 1
        if self._frames >= self.frames_needed:
            self.lengths = {k: float(np.median(v)) for k, v in self._samples.items() if v}

    def _hold(self, key: str, value):
        """Remember a good offset, or return the remembered one when value is missing."""
        if _ok(value):
            self._held[key] = np.asarray(value, float)
            return self._held[key]
        return self._held.get(key)

    def _pose_trunk(self, J: np.ndarray) -> np.ndarray | None:
        """Hips, spine, shoulders and head at the measured lengths (shared by both posing paths)."""
        L = self.lengths
        if not _ok(J[23], J[24]):
            return None
        P = J.copy()
        hip_mid = (J[23] + J[24]) / 2
        across_h = self._hold("hip_dir", _unit(J[23] - J[24]))
        P[23], P[24] = hip_mid + across_h * L["hip_width"] / 2, hip_mid - across_h * L["hip_width"] / 2
        tracked_sh = (J[11] + J[12]) / 2 if _ok(J[11], J[12]) else None
        spine = self._hold("spine", _unit(tracked_sh - hip_mid) if tracked_sh is not None else None)
        sh_mid = hip_mid + spine * L["torso"]
        across_s = _unit(J[11] - J[12]) if _ok(J[11], J[12]) else None
        if across_s is not None:
            across_s = _unit(across_s - (across_s @ spine) * spine)
        across_s = self._hold("shoulder_dir", across_s)
        P[11], P[12] = sh_mid + across_s * L["shoulder_width"] / 2, sh_mid - across_s * L["shoulder_width"] / 2

        ears = (J[7] + J[8]) / 2 if _ok(J[7], J[8]) else None
        if ears is not None and tracked_sh is not None and "neck" in L:
            neck = self._hold("neck_dir", _unit(ears - tracked_sh))
            P[HEAD] = J[HEAD] + (sh_mid + neck * L["neck"] - ears)
        elif tracked_sh is not None:
            P[HEAD] = J[HEAD] + (sh_mid - tracked_sh)
        return P

    def _pose(self, J: np.ndarray) -> np.ndarray | None:
        """Fixed lengths, limbs by plain two-bone IK, no anatomical rules (constraints=False)."""
        P = self._pose_trunk(J)
        if P is None:
            return None
        L = self.lengths
        for side, s in SIDES.items():
            for root, mid, end, l1, l2 in ((s["shoulder"], s["elbow"], s["wrist"], "upper_arm", "forearm"),
                                           (s["hip"], s["knee"], s["ankle"], "thigh", "shin")):
                target = J[end] if _ok(J[end]) else None
                if target is None:  # hold the last pose; a limb never seen yet hangs straight down
                    held = self._held.get(f"{side}{end}")
                    target = P[root] + (held if held is not None else np.array([0.0, -(L[l1] + L[l2]), 0.0]))
                m, e = two_bone_ik(P[root], target, J[mid], L[l1], L[l2], self._held.get(f"{side}{mid}bend"))
                self._held[f"{side}{end}"] = e - P[root]
                self._held[f"{side}{mid}bend"] = m - P[root]
                P[mid], P[end] = m, e
            for k in s["hand"]:  # hands move rigidly with the wrist
                off = self._hold(f"{k}", J[k] - J[s["wrist"]] if _ok(J[k], J[s["wrist"]]) else None)
                if off is not None:
                    P[k] = P[s["wrist"]] + off
            if "heel" in L and "foot" in L:
                to_heel = self._hold(f"{side}heel", _unit(J[s["heel"]] - J[s["ankle"]])
                                     if _ok(J[s["heel"]], J[s["ankle"]]) else None)
                to_toe = self._hold(f"{side}toe", _unit(J[s["toe"]] - J[s["heel"]])
                                    if _ok(J[s["toe"]], J[s["heel"]]) else None)
                if to_heel is not None and to_toe is not None:
                    P[s["heel"]] = P[s["ankle"]] + to_heel * L["heel"]
                    P[s["toe"]] = P[s["heel"]] + to_toe * L["foot"]
        return P

    def _target(self, J, conf, key: str, end: int, root_pos, hang) -> np.ndarray:
        """Where a limb end should go: the detection, blended toward the predicted position (last
        pose carried with its parent) as confidence drops; the prediction when the detection is missing."""
        held = self._held.get(key)
        pred = root_pos + (held if held is not None else hang)
        if not _ok(J[end]):
            return pred
        c = 1.0 if conf is None or not np.isfinite(conf[end]) else float(conf[end])
        w = float(np.clip((c - 0.3) / 0.5, 0.0, 1.0)) if held is not None else 1.0
        return w * J[end] + (1 - w) * pred

    def _pose_constrained(self, J: np.ndarray, conf, t: float, feet_locked: dict) -> np.ndarray | None:
        """_pose plus the anatomical rules in vision.constraints."""
        P = self._pose_trunk(J)
        if P is None:
            return None
        L = self.lengths
        hip_mid = (P[23] + P[24]) / 2
        spine = self._held["spine"]
        pelvis = C.body_frame(hip_mid, P[23] - P[24], spine)
        chest = C.body_frame(hip_mid, P[11] - P[12], spine)
        if pelvis is None or chest is None:
            return self._pose(J)
        dt = (t - self._prev_pose_t) if self._prev_pose_t is not None else 1 / 30
        raw_yaw = float(np.arctan2(pelvis.fwd[0], pelvis.fwd[2]))
        self._body_yaw = C.smooth_angle(self._body_yaw, raw_yaw, C.BODY_YAW_SMOOTH_DEG, C.BODY_YAW_MIN_GAIN)
        floor_fwd = np.array([np.sin(self._body_yaw), 0.0, np.cos(self._body_yaw)])  # smoothed body facing
        for side, s in SIDES.items():
            sign = 1.0 if side == "l" else -1.0
            # legs: knee bends forward only, at most FLEX_MAX_DEG; thigh not far behind the body
            hip = P[s["hip"]]
            l1, l2 = L["thigh"], L["shin"]
            target = self._target(J, conf, f"{side}{s['ankle']}", s["ankle"], hip, np.array([0.0, -(l1 + l2), 0.0]))
            target = self._min_reach(hip, target, l1, l2)
            pole = C.hinge_pole(hip, target, J[s["knee"]] if _ok(J[s["knee"]]) else None, pelvis.fwd)
            knee, ankle = two_bone_ik(hip, target, pole, l1, l2)
            thigh = C.limit_thigh(C.unit(knee - hip), pelvis)
            if not np.allclose(thigh, C.unit(knee - hip)):
                knee = hip + thigh * l1
                ankle = knee + C.unit(target - knee) * l2
            self._held[f"{side}{s['ankle']}"] = ankle - hip
            P[s["knee"]], P[s["ankle"]] = knee, ankle
            # arms: elbow bends one way (points back/out), never inside the torso
            sh = P[s["shoulder"]]
            l1, l2 = L["upper_arm"], L["forearm"]
            target = self._target(J, conf, f"{side}{s['wrist']}", s["wrist"], sh, np.array([0.0, -(l1 + l2), 0.0]))
            target = self._min_reach(sh, target, l1, l2)
            allowed = C.unit(sign * chest.left - chest.fwd)
            pole = C.hinge_pole(sh, target, J[s["elbow"]] if _ok(J[s["elbow"]]) else None, allowed)
            elbow, wrist = two_bone_ik(sh, target, pole, l1, l2)
            length = float(np.linalg.norm((P[11] + P[12]) / 2 - hip_mid))
            hw = L["shoulder_width"] / 2
            for _ in range(3):
                e2, moved_e = C.push_out_of_torso(elbow, chest, hw, length, sign)
                w2, moved_w = C.push_out_of_torso(wrist, chest, hw, length, sign)
                if not (moved_e or moved_w):
                    break
                elbow, wrist = two_bone_ik(sh, w2 if moved_w else wrist, e2 if moved_e else elbow, l1, l2)
            if C.push_out_of_torso(elbow, chest, hw, length, sign)[1]:  # still in: swing the elbow out sideways
                side_pole = sh + sign * chest.left - 0.3 * chest.fwd
                elbow, wrist = two_bone_ik(sh, wrist, side_pole, l1, l2)
                w2, moved_w = C.push_out_of_torso(wrist, chest, hw, length, sign)
                if moved_w:
                    elbow, wrist = two_bone_ik(sh, w2, side_pole, l1, l2)
            self._held[f"{side}{s['wrist']}"] = wrist - sh
            P[s["elbow"]], P[s["wrist"]] = elbow, wrist
            for k in s["hand"]:
                off = self._hold(f"{k}", J[k] - J[s["wrist"]] if _ok(J[k], J[s["wrist"]]) else None)
                if off is not None:
                    P[k] = wrist + off
            # feet: point within FOOT_YAW_LIMIT_DEG of the body's forward, turn at a capped rate,
            # lie flat near the floor, keep their yaw while anchored
            if "heel" in L and "foot" in L:
                heel_j, toe_j = s["heel"], s["toe"]
                tracked = C.unit(J[toe_j] - J[heel_j]) if _ok(J[toe_j], J[heel_j]) else None
                confident = conf is None or (np.isfinite(conf[[heel_j, toe_j]]).all() and min(conf[heel_j], conf[toe_j]) >= 0.5)
                locked = bool(feet_locked.get(side))
                if locked and self._locked_yaw[side] is None:
                    self._locked_yaw[side] = self._foot_yaw.get(side)
                elif not locked:
                    self._locked_yaw[side] = None
                yaw = C.foot_yaw(tracked, floor_fwd, self._foot_yaw.get(side), dt, self._locked_yaw[side], confident)
                self._foot_yaw[side] = yaw
                flat = locked or P[s["ankle"]][1] < 0.08 + C.FLAT_HEIGHT_M
                pitch = C.foot_pitch(tracked, flat)
                fdir = np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), np.cos(yaw) * np.cos(pitch)])
                flat_dir = np.array([np.sin(yaw), 0.0, np.cos(yaw)])
                heel = ankle + C.unit(-0.7 * flat_dir + np.array([0.0, -0.7, 0.0])) * L["heel"]
                P[heel_j], P[toe_j] = heel, heel + fdir * L["foot"]
        return P

    @staticmethod
    def _min_reach(root, target, l1, l2) -> np.ndarray:
        d = target - root
        n = float(np.linalg.norm(d))
        mn = C.min_reach(l1, l2)
        if n < mn:
            u = C.unit(d) if n > 1e-9 else np.array([0.0, -1.0, 0.0])
            return root + u * mn
        return target
```

Create `vision/avatar_eval.py`:

```python
"""Measure how body-like the 3D avatar is on recordings.

    python -m vision.avatar_eval data/recordings/<file>.jsonl [...] [--plain]

For each recording, replays it through the pipeline (current calibration) and the avatar rig, and
prints: arm (elbow/wrist) inside the torso %, elbow/knee hinge violations %, foot yaw change per frame
(median/p95/max), bone-length std, how far confidently tracked wrists/ankles end up from tracking,
and rig time per frame. --plain evaluates the rig without anatomical constraints, for comparison.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

ARM_POINTS = (13, 14, 15, 16)
LIMBS = {"knee_l": (23, 25, 27), "knee_r": (24, 26, 28), "elbow_l": (11, 13, 15), "elbow_r": (12, 14, 16)}
FEET = {"l": (29, 31), "r": (30, 32)}
ENDS = (15, 16, 27, 28)
BONES = ((11, 13), (13, 15), (23, 25), (25, 27), (11, 12), (23, 24))
NEEDED = (11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)


@dataclass
class Report:
    frames: int
    arm_in_torso_pct: float
    hinge_pct: float
    foot_yaw_p50: float
    foot_yaw_p95: float
    foot_yaw_max: float
    bone_std_mm: float
    truth_mean_cm: float
    truth_p95_cm: float
    ms_per_frame: float

    def row(self, name: str) -> str:
        return (f"{name:32s} {self.frames:6d}  {self.arm_in_torso_pct:5.1f}%  {self.hinge_pct:5.1f}%  "
                f"{self.foot_yaw_p50:4.0f}/{self.foot_yaw_p95:4.0f}/{self.foot_yaw_max:4.0f}°  "
                f"{self.bone_std_mm:6.2f}  {self.truth_mean_cm:5.1f}/{self.truth_p95_cm:5.1f}  {self.ms_per_frame:5.2f}")


HEADER = (f"{'recording':32s} {'frames':>6s}  {'arm∈torso':>6s} {'hinge':>6s}  {'foot yaw p50/p95/max':>18s}  "
          f"{'bone mm':>6s}  {'truth cm':>11s}  {'ms':>5s}")


def torso_axes(J):
    hip, sh = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2
    up = sh - hip
    up /= np.linalg.norm(up)
    left = J[23] - J[24]
    left -= (left @ up) * up
    left /= np.linalg.norm(left)
    return hip, sh, up, left, np.cross(left, up)


def inside_torso(J, p) -> bool:
    hip, sh, up, left, fwd = torso_axes(J)
    r = p - hip
    h = r @ up
    return (0.05 < h < np.linalg.norm(sh - hip) - 0.03 and abs(r @ left) < 0.85 * np.linalg.norm(J[11] - J[12]) / 2
            and abs(r @ fwd) < 0.09)


def hinge_violation(J, a, b, c, is_knee: bool) -> bool:
    u, v = J[a] - J[b], J[c] - J[b]
    interior = np.degrees(np.arccos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)), -1, 1)))
    if interior < 30 - 1e-6:  # flexed past 150°
        return True
    if not is_knee:
        return False
    *_, fwd = torso_axes(J)
    d = (J[c] - J[a]) / np.linalg.norm(J[c] - J[a])
    off = (J[b] - J[a]) - ((J[b] - J[a]) @ d) * d
    return off @ fwd < -0.02  # knee behind the hip-ankle line: bending backwards


def foot_yaw(J, heel, toe) -> float:
    v = J[toe] - J[heel]
    return float(np.degrees(np.arctan2(v[0], v[2])))


def evaluate(outputs, rig) -> Report:
    n = inside = hinge = 0
    yaw_steps, bones, truth = [], [], []
    prev_yaw = None
    spent = 0.0
    for o in outputs:
        t0 = time.perf_counter()
        P = rig.update(o.frame.t, o.frame.joints, o.frame.tracking, o.frame.conf, o.feet_locked)  # as debug_view does
        spent += time.perf_counter() - t0
        if o.frame.tracking != "3d" or rig.lengths is None or P is None or not np.isfinite(P[list(NEEDED)]).all():
            prev_yaw = None
            continue
        n += 1
        inside += any(inside_torso(P, P[k]) for k in ARM_POINTS)
        hinge += any(hinge_violation(P, *idx, name.startswith("knee")) for name, idx in LIMBS.items())
        y = np.array([foot_yaw(P, *FEET[s]) for s in FEET])
        if prev_yaw is not None:
            yaw_steps.append(float(np.abs((y - prev_yaw + 180) % 360 - 180).max()))
        prev_yaw = y
        bones.append([np.linalg.norm(P[a] - P[b]) for a, b in BONES])
        J, conf = o.frame.joints, o.frame.conf
        truth += [float(np.linalg.norm(P[k] - J[k])) for k in ENDS if conf[k] >= 0.8 and np.isfinite(J[k]).all()]
    yaw = np.array(yaw_steps) if yaw_steps else np.zeros(1)
    tr = np.array(truth) * 100 if truth else np.zeros(1)
    return Report(n, 100 * inside / max(n, 1), 100 * hinge / max(n, 1), float(np.median(yaw)),
                  float(np.percentile(yaw, 95)), float(yaw.max()),
                  float(np.array(bones).std(axis=0).max() * 1000) if bones else 0.0,
                  float(tr.mean()), float(np.percentile(tr, 95)), 1000 * spent / max(n, 1))


def main() -> int:
    from vision.config import load_cameras
    from vision.replay import replay_outputs
    from vision.rig import BodyRig

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    p.add_argument("--plain", action="store_true", help="rig without anatomical constraints")
    args = p.parse_args()
    cams = load_cameras()
    print(HEADER)
    for path in args.paths:
        outs = replay_outputs(path, retriangulate=True, cams=cams, realtime=False)
        print(evaluate(outs, BodyRig(constraints=not args.plain)).row(path.split("/")[-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_rig.py vision/tests/test_avatar_eval.py -q`
Expected: PASS (19 passed).

- [ ] **Step 5: Measure on the recordings (if present)**

Run: `.venv/bin/python -m vision.avatar_eval data/recordings/*.jsonl` and `... --plain data/recordings/*.jsonl`
Expected (Eric's machine): constrained arm∈torso ≤ 1%, hinge 0%, foot yaw p95 ≤ 9°, bone mm 0.00; plain much worse (see the spec addendum). Skip if `data/recordings/` is empty.

- [ ] **Step 6: Commit**

```bash
git add vision/rig.py vision/tests/test_rig.py vision/avatar_eval.py vision/tests/test_avatar_eval.py
git commit -m "vision: constrained rig and avatar_eval"
```

---

### Task 3: Debug view: confidence, anchoring and the c key

**Files:**
- Modify: `vision/avatar.py`
- Modify: `vision/debug_view.py`
- Modify: `vision/tests/test_avatar.py`
- Modify: `vision/tests/test_debug_view.py`

**Interfaces:**
- Consumes: `BodyRig.update(..., conf, feet_locked)` and `.constraints` (Task 2).
- Produces: `AvatarView.handle_key(ord('c'))` toggles `rig.constraints`; legend mentions "c: body rules"; `compose_view` passes `frame.conf` and `out.feet_locked` to the rig and shows "body rules off (c to turn on)".

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_avatar.py`:

```diff
--- a/vision/tests/test_avatar.py
+++ b/vision/tests/test_avatar.py
@@ -81,3 +81,11 @@
         v.rig.update(k / 30, X, "3d")
     assert v.rig.progress > 0
     assert v.handle_key(ord("b")) and v.rig.progress == 0
+
+
+def test_c_key_turns_the_body_rules_off_and_on():
+    v = AvatarView()
+    assert v.rig.constraints
+    assert v.handle_key(ord("c")) and not v.rig.constraints
+    assert v.handle_key(ord("c")) and v.rig.constraints
+    assert "c: body rules" in " ".join(legend_lines())
```

Apply to `vision/tests/test_debug_view.py`:

```diff
--- a/vision/tests/test_debug_view.py
+++ b/vision/tests/test_debug_view.py
@@ -100,3 +100,24 @@
     out = pipe.process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
     compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
     assert 0 < view.rig.progress < 1
+
+
+def test_3d_view_passes_confidence_and_anchoring_and_says_when_rules_are_off():
+    from vision.avatar import AvatarView
+
+    cams, X = target_cams(), standing_skeleton()
+    view = AvatarView()
+    seen = {}
+    real = view.rig.update
+
+    def spy(t, joints, tracking, conf=None, feet_locked=None):
+        seen.update(conf=conf, feet=feet_locked)
+        return real(t, joints, tracking, conf, feet_locked)
+
+    view.rig.update = spy
+    out = Pipeline(cams).process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
+    on = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
+    assert seen["conf"] is out.frame.conf and seen["feet"] is out.feet_locked
+    view.handle_key(ord("c"))
+    off = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
+    assert (on[360:, 640:] != off[360:, 640:]).any()  # "body rules off" shown
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_avatar.py vision/tests/test_debug_view.py -q`
Expected: 2 FAIL — the c-key test (`assert v.handle_key(ord('c'))` is False) and the debug-view test (`seen['conf'] is None`).

- [ ] **Step 3: Implement**

Apply to `vision/avatar.py`:

```diff
--- a/vision/avatar.py
+++ b/vision/avatar.py
@@ -196,6 +196,8 @@
             self.show_3d = not self.show_3d
         elif c == "b":
             self.rig.reset()
+        elif c == "c":
+            self.rig.constraints = not self.rig.constraints
         else:
             return False
         self._clamp()
@@ -203,7 +205,7 @@
 
 
 def legend_lines() -> list[str]:
-    return ["b: re-measure body   v: 3D <-> front view",
+    return ["b: re-measure body   c: body rules on/off   v: 3D <-> front view",
             "drag / j/l: orbit   i/k: tilt   +/-: zoom   0: reset view"]
 
 
```

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -7,7 +7,8 @@
 Top: camera feeds with MediaPipe 2D (green) and the 3D result projected back (magenta).
 Bottom: top-down view of the mat and a low-poly 3D person (v switches to a flat front view);
 orange = person's left, blue = right. Drag in the 3D view to orbit.
-Keys: r record on/off, m markers on/off, q quit; 3D view: j/l orbit, i/k tilt, +/- zoom, 0 reset, v toggle.
+Keys: r record on/off, m markers on/off, q quit; 3D view: j/l orbit, i/k tilt, +/- zoom, 0 reset, v toggle,
+b re-measure the body, c body rules (anatomical constraints) on/off.
 """
 from __future__ import annotations
 
@@ -216,9 +217,12 @@
     hud.append("keys: r record   m markers   q quit")
     feet = out.feet_locked if out is not None else None
     if view is not None and view.show_3d:
-        body = view.rig.update(frame.t, frame.joints, frame.tracking) if frame is not None else None
+        body = (view.rig.update(frame.t, frame.joints, frame.tracking, frame.conf, feet)
+                if frame is not None else None)
         if view.rig.progress < 1:
             hud = hud + [f"measuring body... {view.rig.progress * 100:.0f}% (stand on the mat)"]
+        if not view.rig.constraints:
+            hud = hud + ["body rules off (c to turn on)"]
         right = render_avatar(body, mat, view, feet_locked=feet, hud=hud)
     else:
         right = render_front(frame, hud)
```

- [ ] **Step 4: Run the whole suite and import the tool**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -c "import vision.debug_view, vision.avatar_eval"`
Expected: PASS (187 passed), no import error.

- [ ] **Step 5: Commit**

```bash
git add vision/avatar.py vision/debug_view.py vision/tests/test_avatar.py vision/tests/test_debug_view.py
git commit -m "vision: 3D view uses confidence and anchoring; c toggles body rules"
```

---

### Task 4: README

**Files:**
- Modify: `vision/README.md`

**Interfaces:**
- Consumes: Tasks 1–3.

- [ ] **Step 1: Document the body rules and avatar_eval**

Apply to `vision/README.md`:

~~~diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -74,6 +74,22 @@
 bending toward the tracked elbows/knees. It re-measures after 5 s with nobody tracked, or on b. Only
 the 3D view is rigged; FrameResult.joints, recordings and checks stay as measured.
 
+### Body rules (3D view)
+
+On top of the fixed proportions, the 3D person follows simple anatomy (`vision/constraints.py`):
+knees and elbows bend one way only (0–150°), thighs don't swing far behind the body, elbows and
+hands are pushed out of the torso, and feet point within 35° of the body's facing, stay put when
+MediaPipe is unsure of the heel/toe, lock while anchored, lie flat near the floor and turn at most
+240°/s. Joints MediaPipe is unsure about lean on the previous frame. Press **c** in the 3D view to
+compare with the rules off. Measure any recording with:
+
+```
+python -m vision.avatar_eval data/recordings/<file>.jsonl [--plain]
+```
+
+It prints arm-in-torso %, hinge violations %, foot yaw change per frame (median/p95/max), bone-length
+std, how far confidently tracked wrists/ankles are from the detections, and ms per frame.
+
 ### Who gets tracked
 
 Only the person on the mat or within 1 m of it. Each camera's pose detection searches only the area
~~~

- [ ] **Step 2: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (187 passed).

- [ ] **Step 3: Commit**

```bash
git add vision/README.md
git commit -m "vision: README: body rules and avatar_eval"
```

---

