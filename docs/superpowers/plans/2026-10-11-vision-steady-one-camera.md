# One camera: steady depth and lean correction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the one-camera 3D body shaking while the person is still, without adding lag to real movement, and take out MediaPipe's lean toward the camera robustly.

**Architecture:** In one-camera mode, `ModelTracker` smooths the fitted pose numbers (One Euro, as before) and then gates them: a pose number may only change as fast as the landmarks it moves are moving in the camera's 2D view (`_gate`), since only depth is noisy and real movement shows in 2D. `mono.upright_rotation` learns the lean from roughly-standing frames (knees > 150°, at least 15 of them) and is capped at 20°.

**Tech Stack:** Python 3.11, NumPy, pytest.

**Spec:** agreed in conversation (2026-10-11): diagnosis of recording `20261011-054226` — camera B 0.83 m high, 3.95 m away, looking down at the feet at 12°, floor-position wobble 2.8/7.5 cm per frame (median/p90) while still, MediaPipe 3D shape 1.7/4.5 cm, output 1.6/3.8 cm; lean 15–16° toward B and the previous correction never firing. Builds on `docs/superpowers/specs/2026-10-11-vision-single-camera-design.md`.

Measured on a prototype (B-only replays), still jitter median/p90: `054226` 1.57/3.80 → 0.65/1.67 cm, `022822` 1.16/3.55 → 0.41/1.28, `032843` 0.52/1.66 → 0.29/0.98; lean correction now active on all three (`032843` tilt 16.2° → 4.1°). Known cost: knees look ~3–12° more bent (136–147° median vs 149–156°).

## Global Constraints

- Two-camera mode unchanged (One Euro only).
- Gate: `GATE_FULL_PX = 8.0`, `GATE_MIN_GAIN = 0.08`, per pose number using the median 2D motion of the landmarks it moves (`DEPENDS`); applied after One Euro (`SINGLE_SMOOTH_TOO = True`); a recovery resets it.
- Lean: `UPRIGHT_KNEE_DEG = 150.0`, `UPRIGHT_MIN_FRAMES = 15`, `UPRIGHT_MAX_DEG = 20.0`, tilt only.
- Run tests with `.venv/bin/python -m pytest` from the repo root (228 pass before; 231 after).

## Review Focus

1. Lag on real movement toward/away from the camera (small 2D motion but real depth change).
2. A person who stands still then moves suddenly: no snap or delay longer than a few frames.
3. Lean correction with someone bending or squatting during the first ~40 frames: capped and gated, must not tip the body badly.
4. Recovery path and resets clear the gate state.

---

### Task 1: Steady one-camera depth and a sturdier lean correction

**Files:**
- Modify: `vision/tracker.py`, `vision/mono.py`, `vision/tests/test_mono.py`

**Interfaces:**
- Produces: `tracker.GATE_FULL_PX`, `GATE_MIN_GAIN`, `SINGLE_SMOOTH_TOO`, `ModelTracker._gate(x, ob)`; `mono.UPRIGHT_KNEE_DEG`, `UPRIGHT_MIN_FRAMES`, `UPRIGHT_MAX_DEG`, `upright_rotation(shapes, min_frames=UPRIGHT_MIN_FRAMES, max_deg=UPRIGHT_MAX_DEG)`.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_mono.py`:

```diff
--- a/vision/tests/test_mono.py
+++ b/vision/tests/test_mono.py
@@ -184,3 +184,71 @@
     assert pipe._model.upright is not None
     pipe._model.reset()
     assert pipe._model.upright is None
+
+
+def _bent_lean(deg_knee_off=25.0, lean_deg=8.0):
+    """A standing body with slightly bent knees (as MediaPipe's world shape looks), tipped lean_deg toward +z."""
+    X = standing_skeleton()
+    for knee, ankle in ((25, 27), (26, 28)):
+        X[[ankle, ankle + 2, ankle + 4]] += [0.0, 0.0, -0.18 * np.sin(np.radians(deg_knee_off))]
+    a = np.radians(lean_deg)
+    R = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
+    return X @ R.T
+
+
+def test_upright_rotation_needs_enough_standing_frames_and_is_capped():
+    from vision.mono import UPRIGHT_MAX_DEG, UPRIGHT_MIN_FRAMES, upright_rotation
+
+    lean = _bent_lean()  # knees ~155°, like MediaPipe: still counts as standing
+    assert upright_rotation([lean] * (UPRIGHT_MIN_FRAMES - 1)) is None  # too few frames: no correction
+    U = upright_rotation([lean] * UPRIGHT_MIN_FRAMES)
+    assert U is not None
+    v = U @ ((lean[11] + lean[12]) / 2 - (lean[27] + lean[28]) / 2)
+    assert np.degrees(np.arctan2(np.hypot(v[0], v[2]), v[1])) < 1.0  # straightened
+    big = _bent_lean(lean_deg=40.0)
+
+    def tilt(v):
+        return np.degrees(np.arctan2(np.hypot(v[0], v[2]), v[1]))
+
+    line = (big[11] + big[12]) / 2 - (big[27] + big[28]) / 2
+    left = tilt(upright_rotation([big] * 20) @ line)
+    assert abs(left - (tilt(line) - UPRIGHT_MAX_DEG)) < 0.5  # only UPRIGHT_MAX_DEG taken out
+
+
+def test_one_camera_depth_shake_is_held_when_the_person_is_still_in_the_picture():
+    import vision.tracker as T
+
+    cam, X = _cam_b(), standing_skeleton()
+    h = stature(X - (X[23] + X[24]) / 2)
+    rng = np.random.default_rng(3)
+
+    def run():
+        pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
+        outs = []
+        for k in range(150):
+            v = pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng)
+            v.world = v.world + rng.normal(0, 0.03, v.world.shape)  # MediaPipe's depth wobble
+            outs.append(pipe.process(k / 30, None, v).frame.joints)
+        J = np.array(outs[80:])
+        return np.nanmedian(np.linalg.norm(np.diff(J, axis=0), axis=2))
+
+    gated = run()
+    full, T.GATE_MIN_GAIN = T.GATE_MIN_GAIN, 1.0  # gate off
+    try:
+        ungated = run()
+    finally:
+        T.GATE_MIN_GAIN = full
+    assert gated < 0.6 * ungated
+
+
+def test_one_camera_real_movement_still_comes_through():
+    cam, X0 = _cam_b(), standing_skeleton()
+    h = stature(X0 - (X0[23] + X0[24]) / 2)
+    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
+    errs = []
+    for k in range(150):
+        X = X0 + [0.01 * max(k - 80, 0), 0.0, 0.0]  # walks sideways 30 cm/s after measuring
+        out = pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30))
+        if k > 100:
+            errs.append(np.linalg.norm((out.frame.joints[23] + out.frame.joints[24]) / 2 - (X[23] + X[24]) / 2))
+    assert np.median(errs) < 0.05
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_mono.py -q`
Expected: 2 FAIL — `ImportError: cannot import name 'UPRIGHT_MAX_DEG' from 'vision.mono'` and `AttributeError: module 'vision.tracker' has no attribute 'GATE_MIN_GAIN'` (the movement test passes before and after: it guards against lag).

- [ ] **Step 3: Implement**

Apply to `vision/mono.py`:

```diff
--- a/vision/mono.py
+++ b/vision/mono.py
@@ -18,7 +18,9 @@
 FEET = [27, 28, 29, 30, 31, 32]
 EYES = (2, 5)
 EYE_HEIGHT_RATIO = 0.936  # eye height / stature
-STRAIGHT_KNEE_DEG = 165.0
+UPRIGHT_KNEE_DEG = 150.0  # roughly standing (MediaPipe's world-shape knees median ~155°)
+UPRIGHT_MIN_FRAMES = 15  # at least this many standing frames before correcting the lean
+UPRIGHT_MAX_DEG = 20.0  # never tilt the body more than this
 
 
 def world_shape(pose: PoseResult, cam: CameraConfig) -> np.ndarray | None:
@@ -67,23 +69,25 @@
     return float(np.degrees(np.arccos(np.clip(c, -1, 1))))
 
 
-def upright_rotation(shapes) -> np.ndarray | None:
+def upright_rotation(shapes, min_frames: int = UPRIGHT_MIN_FRAMES, max_deg: float = UPRIGHT_MAX_DEG
+                     ) -> np.ndarray | None:
     """Rotation (tilt only, about a horizontal axis) that makes the median ankles-to-shoulders line of
-    `shapes` (bodies in the mat frame) vertical, from the frames with both knees straight (> 165 degrees).
-    MediaPipe's world shape leans (mostly toward the camera); this takes the lean out. None if no frame
-    qualifies."""
+    `shapes` (bodies in the mat frame) vertical, from frames where the person is roughly standing (both
+    knees above UPRIGHT_KNEE_DEG; MediaPipe's own knees rarely look fully straight). MediaPipe's world shape
+    leans (mostly toward the camera); this takes the lean out. None with fewer than `min_frames` such frames;
+    the tilt is capped at `max_deg` so one odd session can't tip the body over."""
     ups = []
     for J in shapes:
         J = np.asarray(J, float)
         if not np.isfinite(J[[11, 12, 23, 24, 25, 26, 27, 28]]).all():
             continue
-        if min(_knee_deg(J, 23, 25, 27), _knee_deg(J, 24, 26, 28)) <= STRAIGHT_KNEE_DEG:
+        if min(_knee_deg(J, 23, 25, 27), _knee_deg(J, 24, 26, 28)) <= UPRIGHT_KNEE_DEG:
             continue
         v = (J[11] + J[12]) / 2 - (J[27] + J[28]) / 2
         n = float(np.linalg.norm(v))
         if n > 1e-6:
             ups.append(v / n)
-    if not ups:
+    if len(ups) < min_frames:
         return None
     u = np.median(ups, axis=0)
     u = u / np.linalg.norm(u)
@@ -91,9 +95,10 @@
     s, c = float(np.linalg.norm(axis)), float(u[1])
     if s < 1e-9:
         return np.eye(3)
+    angle = min(float(np.arctan2(s, c)), np.radians(max_deg))
     k = axis / s
     K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
-    return np.eye(3) + s * K + (1 - c) * (K @ K)
+    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)
 
 
 def stature(shape_rel: np.ndarray) -> float:
```

Apply to `vision/tracker.py`:

```diff
--- a/vision/tracker.py
+++ b/vision/tracker.py
@@ -255,6 +255,9 @@
 SMOOTH_MIN_CUTOFF = 0.7  # Hz: One Euro on the pose numbers (0 = off)
 SMOOTH_BETA = 6.0  # two cameras: how much faster it follows when the pose changes quickly
 ROOT_MIN_CUTOFF = 3.0  # Hz: two cameras pin the body's position well, so it is smoothed less
+SINGLE_SMOOTH_TOO = True  # one camera: One Euro on the pose numbers before the 2D-motion gate
+GATE_FULL_PX = 8.0  # one camera: a landmark moving this many px per frame passes its change through fully
+GATE_MIN_GAIN = 0.08  # ... a landmark still in 2D lets only this fraction of a depth change through per frame
 SINGLE_BETA = 2.0  # one camera: noisier depth, so follow speed-ups more cautiously
 SINGLE_ROOT_MIN_CUTOFF = 1.0  # one camera: the floor position wobbles ~2 cm, smooth it more
 
@@ -302,6 +305,7 @@
         root, beta = (SINGLE_ROOT_MIN_CUTOFF, SINGLE_BETA) if self.single else (ROOT_MIN_CUTOFF, SMOOTH_BETA)
         cutoff = np.r_[[root] * 3, [SMOOTH_MIN_CUTOFF] * (N_PARAMS - 3)]
         self._smooth = OneEuroFilter(cutoff, beta) if SMOOTH_MIN_CUTOFF else None
+        self._gate_uv = self._gate_w = self._gate_out = None
 
     @property
     def progress(self) -> float:
@@ -470,6 +474,7 @@
                 if self._median_residual(x2, obs) < self._median_residual(x, obs):
                     x = x2
                     self._smooth.reset() if self._smooth is not None else None
+                    self._gate_out = None  # a recovered pose snaps into place instead of being gated
                     self.recoveries += 1
         self.last_ms = (time.perf_counter() - t0) * 1000
         if not np.isfinite(x).all():
@@ -477,4 +482,28 @@
         self._p = x
         self.last_cost = float(cost)
         # smooth the pose numbers (not the joints): steady when still, quick when moving, bones exact
+        if self.single:  # adaptive smoothing first, then the 2D-motion gate
+            smoothed = self._smooth(x, t) if (self._smooth is not None and SINGLE_SMOOTH_TOO) else x
+            return forward(self._gate(smoothed, obs[0]), self.shape)
         return forward(self._smooth(x, t) if self._smooth is not None else x, self.shape)
+
+    def _gate(self, x: np.ndarray, ob) -> np.ndarray:
+        """One camera: a pose number may only change as fast as the landmarks it moves are moving in the
+        camera's 2D view. The camera sees side-to-side and up-down motion precisely; only depth is noisy,
+        so joints that are still in 2D are held still in 3D, and real movement (which shows in 2D) passes."""
+        _, uv, w = ob
+        prev_uv, prev_w, prev_out = self._gate_uv, self._gate_w, self._gate_out
+        self._gate_uv, self._gate_w = uv.copy(), w.copy()
+        if prev_out is None or prev_uv is None:
+            self._gate_out = x.copy()
+            return x
+        ok = (w > 0) & (prev_w > 0)
+        motion = np.where(ok, np.linalg.norm(uv - prev_uv, axis=1), np.nan)
+        gain = np.ones(N_PARAMS)
+        for j in range(N_PARAMS):
+            m = motion[DEPENDS[:, j]]
+            m = m[np.isfinite(m)]
+            if m.size:
+                gain[j] = float(np.clip(np.median(m) / GATE_FULL_PX, GATE_MIN_GAIN, 1.0))
+        self._gate_out = prev_out + gain * (x - prev_out)
+        return self._gate_out
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (231 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/tracker.py vision/mono.py vision/tests/test_mono.py
git commit -m "vision: one camera holds still joints still (2D-motion gate) and corrects the lean robustly"
```
