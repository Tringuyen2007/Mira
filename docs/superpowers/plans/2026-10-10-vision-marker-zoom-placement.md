# Zoom from markers, placement overlay, camera-moved alarm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calibrate the two webcams with no checkerboard and no tape measure: zoom and pose from the floor markers, an on-screen overlay that guides each camera to a saved symmetric spot, zoom refined by the body bundle adjustment, and an alarm when a camera moves.

**Architecture:** `markers.py` gains a focal-length estimate (least squares over f + pose, principal point at image centre, no distortion) and a richer `placement_hint` (position line, aim line, roll). A new `placement.py` holds the pure placement maths (locked targets, ghost mat, A/B layout) and its drawing. A new `moved.py` compares live marker detections with the saved ones. `bundle.py` adds one focal-scale parameter per camera. The tools (`calib_extrinsics`, `calib_refine`, `debug_view`, `checks`) are wired to these. Checkerboard calibration is deleted.

**Tech Stack:** Python 3.11, OpenCV 5 (aruco, solvePnP, projectPoints), NumPy, SciPy `least_squares`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-vision-marker-zoom-placement-design.md` (builds on `docs/superpowers/specs/2026-10-10-vision-cameras-to-3d-design.md`).

Every code block below was run against a scratch copy of the repo before this plan was written: the full suite passed (98 tests).

## Global Constraints

- Mat frame unchanged: origin mat centre on the mat surface, +y up, +z toward Mira, +x toward the patient's left; `X_cam = R @ X_mat + t`, OpenCV camera axes.
- Zoom model: one focal length f (fx = fy), principal point at the image centre, zero distortion (`dist = zeros(5)`).
- Zoom needs markers at both ends of the mat (one of IDs 0/1 and one of 2/3); rejected if `f_std / f > 3%` or horizontal FOV outside 30–100°; nominal fallback = 55° diagonal.
- Placement tolerance 5 cm and 2° (`config/mat.yaml`). Instruction thresholds: 2 cm, 0.5°. "Clockwise" = as seen from behind the camera.
- Locked targets: `target_eye` per camera in `config/cameras.yaml`; `lock_targets` is exactly mirror-symmetric (x → −x).
- Bundle focal guard: ≤ 15% change per camera; principal point fixed.
- Alarm: `interval_s=1.0`, `threshold_px=3.0`, `confirm=3`, `blind_s=10.0`; warns, never auto-corrects.
- Height check: eye height (landmarks 2, 5) / 0.936, pass within ±5%.
- No checkerboard code remains; an old `cameras.yaml` with `intrinsics_rms` must still load.
- Run tests with `.venv/bin/python -m pytest` from the repo root.

## Review Focus

1. A camera that sees only the near pair of markers (person or furniture covering the far end): zoom falls back to the last good estimate or the guess, the tile says so, and `s` refuses to save. Never a crash, never a silently guessed K saved.
2. Roll/turn/tilt instruction signs on the real rig: a camera whose right edge is high must be told "rotate clockwise"; one pointing left of the aim point "turn right". Wrong signs make the overlay useless.
3. Old `config/cameras.yaml` on Eric's laptop (camera A has a checkerboard K/dist and `intrinsics_rms`, camera B nothing): every tool must still load it; `calib_extrinsics` must replace A's K on save.
4. Alarm false positives while a person walks over markers (partial occlusion), and false negatives when the camera is bumped while markers are partly hidden.
5. Bundle adjustment with focal free: zoom/distance trade-off must stay pinned by the markers (guard trips rather than saving a drifted f).

---
### Task 1: Config: `target_eye`, drop `intrinsics_rms`, delete checkerboard calibration

**Files:**
- Modify: `vision/config.py`
- Modify: `vision/tests/test_config.py`
- Modify: `config/mat.yaml`
- Delete: `vision/calib_intrinsics.py`, `vision/tests/test_calib_intrinsics.py`

**Interfaces:**
- Produces: `CameraConfig.target_eye: np.ndarray | None` (round-trips via `to_dict`/`from_dict`); `CameraConfig` no longer has `intrinsics_rms` (an old YAML key is ignored).
- Produces: `MatConfig.tol_position_m == 0.05`, `tol_angle_deg == 2.0` from the repo `config/mat.yaml`.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_config.py`:

```diff
--- a/vision/tests/test_config.py
+++ b/vision/tests/test_config.py
@@ -33,15 +33,16 @@
     assert load_cameras(path) == {}
     cam = CameraConfig(
         name="A", index=1, hint="1280x720",
-        K=np.eye(3), dist=np.zeros(5), intrinsics_rms=0.3,
+        K=np.eye(3), dist=np.zeros(5),
         R=np.eye(3), t=np.array([0.0, 0.0, 3.0]), extrinsics_source="markers", extrinsics_rms=0.8,
-        marker_obs={0: np.arange(8.0).reshape(4, 2)},
+        marker_obs={0: np.arange(8.0).reshape(4, 2)}, target_eye=np.array([3.2, 0.85, 3.2]),
     )
     assert save_cameras({"A": cam}, path) is None
     loaded = load_cameras(path)["A"]
     assert loaded.index == 1 and loaded.calibrated
     np.testing.assert_allclose(loaded.t, [0.0, 0.0, 3.0])
     np.testing.assert_allclose(loaded.marker_obs[0], cam.marker_obs[0])
+    np.testing.assert_allclose(loaded.target_eye, [3.2, 0.85, 3.2])
     backup = save_cameras({"A": cam}, path)
     assert backup is not None and backup.exists()
 
@@ -50,3 +51,12 @@
     cam = CameraConfig(name="B", index=0)
     assert not cam.has_intrinsics and not cam.calibrated
     assert CameraConfig.from_dict("B", cam.to_dict()).index == 0
+
+
+def test_old_file_with_intrinsics_rms_still_loads(tmp_path):
+    path = tmp_path / "cameras.yaml"
+    path.write_text("cameras:\n  A:\n    index: 0\n    K: [[900, 0, 640], [0, 900, 360], [0, 0, 1]]\n"
+                    "    dist: [0, 0, 0, 0, 0]\n    intrinsics_rms: 0.22\n")
+    cam = load_cameras(path)["A"]
+    assert cam.has_intrinsics and cam.target_eye is None
+    assert "intrinsics_rms" not in cam.to_dict()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_config.py -q`
Expected: FAIL — `TypeError: CameraConfig.__init__() got an unexpected keyword argument 'target_eye'`; the old-file test fails on `assert "intrinsics_rms" not in cam.to_dict()`.

- [ ] **Step 3: Implement**

Apply to `vision/config.py`:

```diff
--- a/vision/config.py
+++ b/vision/config.py
@@ -79,12 +79,12 @@
     height: int = 720
     K: np.ndarray | None = None
     dist: np.ndarray | None = None
-    intrinsics_rms: float | None = None
     R: np.ndarray | None = None
     t: np.ndarray | None = None
     extrinsics_source: str | None = None
     extrinsics_rms: float | None = None
     marker_obs: dict[int, np.ndarray] | None = None
+    target_eye: np.ndarray | None = None  # locked placement target (mat frame), see vision.placement
 
     @property
     def has_intrinsics(self) -> bool:
@@ -99,7 +99,6 @@
         if self.has_intrinsics:
             d["K"] = np.asarray(self.K, float).tolist()
             d["dist"] = np.asarray(self.dist, float).ravel().tolist()
-            d["intrinsics_rms"] = None if self.intrinsics_rms is None else float(self.intrinsics_rms)
         if self.R is not None and self.t is not None:
             d["R"] = np.asarray(self.R, float).tolist()
             d["t"] = np.asarray(self.t, float).ravel().tolist()
@@ -107,6 +106,8 @@
             d["extrinsics_rms"] = None if self.extrinsics_rms is None else float(self.extrinsics_rms)
         if self.marker_obs:
             d["marker_obs"] = {int(k): np.asarray(v, float).tolist() for k, v in self.marker_obs.items()}
+        if self.target_eye is not None:
+            d["target_eye"] = np.asarray(self.target_eye, float).ravel().tolist()
         return d
 
     @classmethod
@@ -123,12 +124,12 @@
             height=int(d.get("height", 720)),
             K=arr("K"),
             dist=arr("dist"),
-            intrinsics_rms=d.get("intrinsics_rms"),
             R=arr("R"),
             t=arr("t"),
             extrinsics_source=d.get("extrinsics_source"),
             extrinsics_rms=d.get("extrinsics_rms"),
             marker_obs=None if not obs else {int(k): np.array(v, float) for k, v in obs.items()},
+            target_eye=arr("target_eye"),
         )
 
 
```

Apply to `config/mat.yaml`:

```diff
--- a/config/mat.yaml
+++ b/config/mat.yaml
@@ -9,4 +9,4 @@
   A: {eye: [1.98, 0.85, 1.98]}
   B: {eye: [-1.98, 0.85, 1.98]}
 aim_point: [0.0, 0.8, 0.0]
-placement_tolerance: {position_m: 0.10, angle_deg: 5.0}
+placement_tolerance: {position_m: 0.05, angle_deg: 2.0}
```

Delete the checkerboard tool and its tests:

```bash
git rm -q vision/calib_intrinsics.py vision/tests/test_calib_intrinsics.py
```

`vision/checks.py` still reads `cams[n].intrinsics_rms`; Task 6 removes that. Until then the checks tests may fail on a real `CameraConfig`; that is expected, and the next step runs only the config tests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_config.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/config.py vision/tests/test_config.py config/mat.yaml
git commit -m "vision: target_eye in camera config; remove checkerboard calibration"
```

---

### Task 2: Zoom from markers and position/aim/roll instructions (`markers.py`)

**Files:**
- Modify: `vision/markers.py`
- Modify: `vision/tests/test_markers.py`

**Interfaces:**
- Produces: `nominal_K(width, height, dfov_deg=55.0) -> np.ndarray (3x3)`.
- Produces: `FocalEstimate(K, f, f_std, hfov_deg, vfov_deg, rms)`; `estimate_focal(obs: dict[int, (4,2)], mat, image_size: (w, h)) -> (FocalEstimate | None, str | None)`.
- Produces: `ZoomSolve(K, R, t, rms, guessed: bool, zoom_msg: str)`; `solve_with_zoom(obs, mat, image_size, last_good: FocalEstimate | None = None) -> (ZoomSolve | None, FocalEstimate | None)`.
- Produces: `placement_hint(R, t, target_eye, aim, tol_position_m, tol_angle_deg) -> dict` with keys `ok, position_ok, aim_ok, dx_cm, dy_cm, dist_cm, yaw_deg, pitch_deg, roll_deg, position_msg, aim_msg, msg`.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_markers.py`:

```diff
--- a/vision/tests/test_markers.py
+++ b/vision/tests/test_markers.py
@@ -4,7 +4,8 @@
 from vision.config import MatConfig
 from vision.frames import cam_center, look_at_pose, project, rotation_angle_deg
 from vision.markers import (
-    MarkerDetector, aruco_dictionary, average_detections, camera_warnings, placement_hint, solve_camera_pose,
+    MarkerDetector, aruco_dictionary, average_detections, camera_warnings, estimate_focal, nominal_K,
+    placement_hint, solve_camera_pose, solve_with_zoom,
 )
 from vision.tests.synth import make_cam
 
@@ -68,8 +69,9 @@
 
 def test_placement_hint_ok_at_target():
     R, t = look_at_pose(TARGET_A, AIM)
-    h = placement_hint(R, t, TARGET_A, AIM, 0.10, 5.0)
+    h = placement_hint(R, t, TARGET_A, AIM, 0.05, 2.0)
     assert h["ok"] and h["msg"] == "OK"
+    assert h["position_msg"] == "Position OK" and h["aim_msg"] == "Aim OK"
 
 
 def test_placement_hint_says_move_left():
@@ -78,10 +80,44 @@
     right[1] = 0
     right /= np.linalg.norm(right)
     R2, t2 = look_at_pose(TARGET_A + 0.15 * right, AIM)
-    h = placement_hint(R2, t2, TARGET_A, AIM, 0.10, 5.0)
-    assert not h["ok"] and h["dx_cm"] == -15 and h["msg"].startswith("Move 15 cm left")
+    h = placement_hint(R2, t2, TARGET_A, AIM, 0.05, 2.0)
+    assert not h["ok"] and not h["position_ok"] and h["aim_ok"]
+    assert h["dx_cm"] == -15 and h["position_msg"] == "Move 15 cm left"
 
 
+def test_placement_hint_says_back_when_too_close():
+    R, t = look_at_pose(TARGET_A * 0.9 + AIM * 0.1, AIM)
+    h = placement_hint(R, t, TARGET_A, AIM, 0.05, 2.0)
+    assert "cm back" in h["position_msg"]
+
+
+def _rolled(R, deg):
+    a = np.radians(deg)
+    x, y = R[0], R[1]
+    return np.vstack([np.cos(a) * x - np.sin(a) * y, np.sin(a) * x + np.cos(a) * y, R[2]])
+
+
+def test_placement_hint_roll_direction():
+    R, t = look_at_pose(TARGET_A, AIM)
+    R2 = _rolled(R, 3.0)
+    assert R2[0][1] > 0  # image right edge raised
+    h = placement_hint(R2, -R2 @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
+    assert h["position_ok"] and not h["aim_ok"]
+    assert h["roll_deg"] == 3.0 and h["aim_msg"] == "Rotate clockwise 3.0°"
+    h = placement_hint(_rolled(R, -3.0), -_rolled(R, -3.0) @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
+    assert h["aim_msg"] == "Rotate counter-clockwise 3.0°"
+
+
+def test_placement_hint_turn_direction():
+    R, t = look_at_pose(TARGET_A, AIM)
+    right = R[0].copy()
+    right[1] = 0
+    aim_left = AIM - 0.3 * right / np.linalg.norm(right)
+    R2, _ = look_at_pose(TARGET_A, aim_left)  # pointing left of the aim point
+    h = placement_hint(R2, -R2 @ TARGET_A, TARGET_A, AIM, 0.05, 2.0)
+    assert h["aim_msg"].startswith("Turn right")
+
+
 def test_camera_warnings():
     R, t = look_at_pose(TARGET_A, AIM)
     assert camera_warnings(R, t) == []
@@ -89,3 +125,51 @@
     assert any("below the floor" in w for w in camera_warnings(R, t))
     R, t = look_at_pose(np.array([1.0, 0.85, -2.0]), AIM)
     assert any("behind" in w for w in camera_warnings(R, t))
+
+FAR_A = np.array([3.18, 0.85, 3.18])
+K_WEBCAM = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])
+
+
+def _webcam_obs(ids=(0, 1, 2, 3), noise=0.3, seed=0, eye=FAR_A):
+    cam = make_cam("A", eye, K=K_WEBCAM)
+    rng = np.random.default_rng(seed)
+    return cam, {i: o + rng.normal(0, noise, o.shape) for i, o in _obs(cam, ids).items()}
+
+
+def test_estimate_focal_from_four_markers():
+    _, obs = _webcam_obs()
+    est, reason = estimate_focal(obs, MAT, (1280, 720))
+    assert reason is None
+    assert abs(est.f / 1425 - 1) < 0.03 and est.f_std / est.f < 0.03
+    assert abs(est.hfov_deg - 48.4) < 1.5 and est.rms < 1.0
+    np.testing.assert_allclose(est.K[:2, 2], [640, 360])
+
+
+def test_estimate_focal_needs_both_ends():
+    _, obs = _webcam_obs(ids=(0, 1))
+    est, reason = estimate_focal(obs, MAT, (1280, 720))
+    assert est is None and "both ends" in reason
+
+
+def test_estimate_focal_rejects_uncertain_estimates():
+    _, obs = _webcam_obs(ids=(1, 3), noise=3.0)
+    est, reason = estimate_focal(obs, MAT, (1280, 720))
+    assert est is None and "uncertain" in reason
+
+
+def test_nominal_K():
+    K = nominal_K(1280, 720)
+    assert abs(np.degrees(2 * np.arctan(np.hypot(640, 360) / K[0, 0])) - 55) < 1e-9
+
+
+def test_solve_with_zoom_measures_then_falls_back():
+    cam, obs = _webcam_obs(noise=0.1)
+    sol, good = solve_with_zoom(obs, MAT, (1280, 720))
+    assert not sol.guessed and "zoom 48" in sol.zoom_msg and good is not None
+    assert np.linalg.norm(cam_center(sol.R, sol.t) - FAR_A) < 0.10
+    near = {i: obs[i] for i in (0, 1)}
+    sol2, good2 = solve_with_zoom(near, MAT, (1280, 720), last_good=good)
+    assert sol2.guessed and good2 is good and np.allclose(sol2.K, good.K)
+    sol3, good3 = solve_with_zoom(near, MAT, (1280, 720))
+    assert sol3.guessed and good3 is None and "guessed" in sol3.zoom_msg
+    assert solve_with_zoom({}, MAT, (1280, 720)) == (None, None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_markers.py -q`
Expected: FAIL at collection — `ImportError: cannot import name 'estimate_focal' from 'vision.markers'`.

- [ ] **Step 3: Implement**

Apply to `vision/markers.py`:

```diff
--- a/vision/markers.py
+++ b/vision/markers.py
@@ -1,8 +1,11 @@
 """Floor-marker detection and camera pose from markers."""
 from __future__ import annotations
 
+from dataclasses import dataclass
+
 import cv2
 import numpy as np
+from scipy.optimize import least_squares
 
 from vision.config import MatConfig
 from vision.frames import angle_between_deg, cam_center, project
@@ -59,14 +62,115 @@
     return R, t, rms
 
 
+NOMINAL_DFOV_DEG = 55.0  # starting guess / fallback: typical narrow webcam diagonal field of view
+MAX_FOCAL_STD = 0.03
+FOV_RANGE_DEG = (30.0, 100.0)
+NEAR_IDS, FAR_IDS = {0, 1}, {2, 3}
+
+
+def nominal_K(width: int, height: int, dfov_deg: float = NOMINAL_DFOV_DEG) -> np.ndarray:
+    f = np.hypot(width, height) / 2 / np.tan(np.radians(dfov_deg) / 2)
+    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1.0]])
+
+
+@dataclass
+class FocalEstimate:
+    K: np.ndarray
+    f: float
+    f_std: float
+    hfov_deg: float
+    vfov_deg: float
+    rms: float
+
+
+def estimate_focal(obs: dict[int, np.ndarray], mat: MatConfig, image_size) -> tuple[FocalEstimate | None, str | None]:
+    """Focal length (fx = fy) from one averaged marker view, principal point at the image centre, no distortion.
+
+    Returns (estimate, None) or (None, reason).
+    """
+    corners = mat.marker_corners()
+    ids = sorted(i for i in obs if i in corners)
+    if not (set(ids) & NEAR_IDS and set(ids) & FAR_IDS):
+        return None, "need markers at both ends of the mat to measure zoom"
+    w, h = image_size
+    obj = np.concatenate([corners[i] for i in ids]).astype(np.float64)
+    img = np.concatenate([obs[i] for i in ids]).astype(np.float64)
+    K0 = nominal_K(w, h)
+    c = K0[:2, 2]
+    ok, rvec, tvec = cv2.solvePnP(obj, img, K0, None, flags=cv2.SOLVEPNP_IPPE)
+    if not ok:
+        return None, "marker pose could not be solved"
+
+    def residuals(p):
+        K = np.array([[p[0], 0, c[0]], [0, p[0], c[1]], [0, 0, 1.0]])
+        px, _ = cv2.projectPoints(obj, p[1:4], p[4:7], K, None)
+        return (px.reshape(-1, 2) - img).ravel()
+
+    x0 = np.r_[K0[0, 0], rvec.ravel(), tvec.ravel()]
+    r = least_squares(residuals, x0, x_scale=np.r_[100.0, [0.1] * 6])
+    f = float(r.x[0])
+    dof = max(len(r.fun) - len(r.x), 1)
+    cov = np.linalg.pinv(r.jac.T @ r.jac) * (np.sum(r.fun ** 2) / dof)
+    f_std = float(np.sqrt(max(cov[0, 0], 0.0)))
+    if f <= 0:
+        return None, "zoom estimate failed"
+    hfov = float(np.degrees(2 * np.arctan(w / 2 / f)))
+    vfov = float(np.degrees(2 * np.arctan(h / 2 / f)))
+    if f_std / f > MAX_FOCAL_STD:
+        return None, f"zoom too uncertain (±{100 * f_std / f:.0f}%): get more of the mat in view"
+    if not FOV_RANGE_DEG[0] <= hfov <= FOV_RANGE_DEG[1]:
+        return None, f"zoom estimate {hfov:.0f}° is implausible: check marker IDs and orientation"
+    rms = float(np.sqrt(np.mean(r.fun.reshape(-1, 2) ** 2) * 2))
+    K = np.array([[f, 0, c[0]], [0, f, c[1]], [0, 0, 1.0]])
+    return FocalEstimate(K=K, f=f, f_std=f_std, hfov_deg=hfov, vfov_deg=vfov, rms=rms), None
+
+
+@dataclass
+class ZoomSolve:
+    K: np.ndarray
+    R: np.ndarray
+    t: np.ndarray
+    rms: float
+    guessed: bool  # True: K is a fallback, not measured this time; the pose is for placement only
+    zoom_msg: str
+
+
+def solve_with_zoom(obs: dict[int, np.ndarray], mat: MatConfig, image_size,
+                    last_good: FocalEstimate | None = None) -> tuple[ZoomSolve | None, FocalEstimate | None]:
+    """Zoom then pose from markers. Returns (solve or None, the focal estimate to remember)."""
+    est, reason = estimate_focal(obs, mat, image_size)
+    if est is not None:
+        K, guessed, msg, last_good = est.K, False, f"zoom {est.hfov_deg:.1f}° x {est.vfov_deg:.1f}°", est
+    elif last_good is not None:
+        K, guessed, msg = last_good.K, True, f"zoom from earlier ({reason})"
+    else:
+        K, guessed, msg = nominal_K(*image_size), True, f"zoom guessed ({reason})"
+    sol = solve_camera_pose(obs, mat, K, np.zeros(5))
+    if sol is None:
+        return None, last_good
+    R, t, rms = sol
+    return ZoomSolve(K=K, R=R, t=t, rms=rms, guessed=guessed, zoom_msg=msg), last_good
+
+
 def _floor_unit(v) -> np.ndarray:
     v = np.array(v, float)
     v[1] = 0.0
     return v / np.linalg.norm(v)
 
 
+def _sentence(parts: list[str], ok: bool, ok_msg: str) -> str:
+    if ok or not parts:
+        return ok_msg
+    msg = ", ".join(parts)
+    return msg[0].upper() + msg[1:]
+
+
 def placement_hint(R, t, target_eye, aim, tol_position_m: float, tol_angle_deg: float) -> dict:
-    """How to move/turn a camera to reach its target, in the camera's own terms (as if standing behind it)."""
+    """How to move/turn a camera to reach its target, in the camera's own terms (as if standing behind it).
+
+    Position first ("Move 20 cm back"), then aim ("Turn right 2°, tilt down 3°, rotate clockwise 1°").
+    Roll is the tilt of the image's horizontal axis; "clockwise" is as seen from behind the camera.
+    """
     C = cam_center(R, t)
     fwd = np.asarray(R[2], float)
     right_f = _floor_unit(R[0])
@@ -77,25 +181,31 @@
     dx, dy, dfwd = float(delta @ right_f), float(delta[1]), float(delta @ aim_f)
     yaw = float(np.degrees(np.arctan2(aim_f @ right_f, aim_f @ fwd_f)))
     pitch = float(np.degrees(np.arcsin(np.clip(aim_dir[1], -1, 1)) - np.arcsin(np.clip(fwd[1], -1, 1))))
-    ok = np.linalg.norm(delta) <= tol_position_m and angle_between_deg(fwd, aim_dir) <= tol_angle_deg
-    parts = []
+    roll = float(np.degrees(np.arcsin(np.clip(R[0][1], -1, 1))))  # > 0: image right edge raised
+    position_ok = bool(np.linalg.norm(delta) <= tol_position_m)
+    aim_ok = bool(angle_between_deg(fwd, aim_dir) <= tol_angle_deg and abs(roll) <= tol_angle_deg)
+    pos = []
     if abs(dx) >= 0.02:
-        parts.append(f"move {abs(dx) * 100:.0f} cm {'right' if dx > 0 else 'left'}")
+        pos.append(f"move {abs(dx) * 100:.0f} cm {'right' if dx > 0 else 'left'}")
     if abs(dfwd) >= 0.02:
-        parts.append(f"move {abs(dfwd) * 100:.0f} cm {'closer' if dfwd > 0 else 'back'}")
+        pos.append(f"move {abs(dfwd) * 100:.0f} cm {'closer' if dfwd > 0 else 'back'}")
     if abs(dy) >= 0.02:
-        parts.append(f"{'raise' if dy > 0 else 'lower'} {abs(dy) * 100:.0f} cm")
-    if abs(yaw) >= 1:
-        parts.append(f"turn {'right' if yaw > 0 else 'left'} {abs(yaw):.0f}°")
-    if abs(pitch) >= 1:
-        parts.append(f"tilt {'up' if pitch > 0 else 'down'} {abs(pitch):.0f}°")
-    if ok or not parts:
-        msg = "OK"
-    else:
-        msg = ", ".join(parts)
-        msg = msg[0].upper() + msg[1:]
-    return {"ok": bool(ok), "dx_cm": round(dx * 100), "dy_cm": round(dy * 100), "dist_cm": round(dfwd * 100),
-            "yaw_deg": round(yaw, 1), "pitch_deg": round(pitch, 1), "msg": msg}
+        pos.append(f"{'raise' if dy > 0 else 'lower'} {abs(dy) * 100:.0f} cm")
+    turn = []
+    if abs(yaw) >= 0.5:
+        turn.append(f"turn {'right' if yaw > 0 else 'left'} {abs(yaw):.1f}°")
+    if abs(pitch) >= 0.5:
+        turn.append(f"tilt {'up' if pitch > 0 else 'down'} {abs(pitch):.1f}°")
+    if abs(roll) >= 0.5:
+        turn.append(f"rotate {'clockwise' if roll > 0 else 'counter-clockwise'} {abs(roll):.1f}°")
+    position_msg = _sentence(pos, position_ok, "Position OK")
+    aim_msg = _sentence(turn, aim_ok, "Aim OK")
+    ok = position_ok and aim_ok
+    return {"ok": ok, "position_ok": position_ok, "aim_ok": aim_ok,
+            "dx_cm": round(dx * 100), "dy_cm": round(dy * 100), "dist_cm": round(dfwd * 100),
+            "yaw_deg": round(yaw, 1), "pitch_deg": round(pitch, 1), "roll_deg": round(roll, 1),
+            "position_msg": position_msg, "aim_msg": aim_msg,
+            "msg": "OK" if ok else f"{position_msg}; {aim_msg}"}
 
 
 def camera_warnings(R, t) -> list[str]:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_markers.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/markers.py vision/tests/test_markers.py
git commit -m "vision: zoom from markers; position, aim and roll placement instructions"
```

---

### Task 3: Placement maths and overlay (`placement.py`)

**Files:**
- Create: `vision/placement.py`
- Create: `vision/tests/test_placement.py`

**Interfaces:**
- Consumes: `CameraConfig.target_eye` (Task 1); `placement_hint` dict keys `position_msg, aim_msg, position_ok, aim_ok` (Task 2).
- Produces: `target_eye(cam: CameraConfig | None, mat, name) -> np.ndarray`; `lock_targets(center_a, center_b) -> (eye_a, eye_b)`; `target_pose(eye, aim) -> (R, t)`; `ghost_polylines(mat, K, eye, aim) -> list[(N,2)]` (mat outline first, then markers 0–3); `layout(R, t) -> {dist_m, height_m, bearing_deg}`; `comparison_lines(layouts: dict[str, dict]) -> list[str]`; `draw_overlay(tile, scale, K, R, t, mat, eye, hint) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_placement.py`:

```python
import numpy as np

from vision.config import CameraConfig, load_mat
from vision.frames import look_at_pose, project
from vision.placement import (
    comparison_lines, draw_overlay, ghost_polylines, layout, lock_targets, target_eye,
)
from vision.markers import placement_hint
from vision.tests.synth import make_cam

MAT = load_mat()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])
EYE_A = np.array([3.18, 0.85, 3.18])


def test_lock_targets_is_mirror_symmetric():
    a, b = lock_targets([3.0, 0.9, 3.3], [-3.4, 0.8, 3.1])
    np.testing.assert_allclose(a, [3.2, 0.85, 3.2])
    np.testing.assert_allclose(b, a * [-1, 1, 1])


def test_lock_targets_keeps_symmetric_input():
    a, b = lock_targets(EYE_A, EYE_A * [-1, 1, 1])
    np.testing.assert_allclose(a, EYE_A)
    np.testing.assert_allclose(b, EYE_A * [-1, 1, 1])


def test_target_eye_prefers_locked():
    cam = CameraConfig(name="A", index=0)
    np.testing.assert_allclose(target_eye(cam, MAT, "A"), MAT.camera_targets["A"])
    cam.target_eye = EYE_A
    np.testing.assert_allclose(target_eye(cam, MAT, "A"), EYE_A)
    np.testing.assert_allclose(target_eye(None, MAT, "B"), MAT.camera_targets["B"])


def test_ghost_matches_markers_when_at_target():
    cam = make_cam("A", EYE_A, target=MAT.aim_point, K=K)
    polys = ghost_polylines(MAT, K, EYE_A, MAT.aim_point)
    assert len(polys) == 5  # mat outline + 4 markers
    for i, poly in zip(sorted(MAT.marker_corners()), polys[1:]):
        expected = project(MAT.marker_corners()[i], cam.K, None, cam.R, cam.t)
        np.testing.assert_allclose(poly, expected, atol=0.01)


def test_layout_and_comparison():
    ra, ta = look_at_pose(EYE_A, MAT.aim_point)
    rb, tb = look_at_pose(EYE_A * [-1, 1, 1] + [0, 0.05, 0], MAT.aim_point)
    la, lb = layout(ra, ta), layout(rb, tb)
    assert abs(la["dist_m"] - np.hypot(3.18, 3.18)) < 1e-9 and abs(la["bearing_deg"] - 45) < 1e-9
    assert abs(lb["bearing_deg"] + 45) < 1e-9 and abs(lb["height_m"] - 0.90) < 1e-9
    lines = comparison_lines({"A": la, "B": lb})
    assert len(lines) == 3 and "height 5 cm" in lines[2] and "distance 0 cm" in lines[2]


def test_draw_overlay_runs():
    R, t = look_at_pose(EYE_A, MAT.aim_point)
    tile = np.zeros((360, 640, 3), np.uint8)
    hint = placement_hint(R, t, EYE_A, MAT.aim_point, 0.05, 2.0)
    draw_overlay(tile, np.array([0.5, 0.5]), K, R, t, MAT, EYE_A, hint)
    assert tile.any()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_placement.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'vision.placement'`.

- [ ] **Step 3: Implement**

Create `vision/placement.py`:

```python
"""Camera placement: saved symmetric targets, the ghost mat overlay and the A/B comparison."""
from __future__ import annotations

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.draw import GREEN, RED, WHITE, YELLOW, draw_text, pt
from vision.frames import cam_center, look_at_pose, project

MIRROR = np.array([-1.0, 1.0, 1.0])  # reflect across the mat's centreline (x -> -x)


def target_eye(cam: CameraConfig | None, mat: MatConfig, name: str) -> np.ndarray:
    """The locked target if there is one, else the mat.yaml default."""
    if cam is not None and cam.target_eye is not None:
        return np.asarray(cam.target_eye, float)
    return np.asarray(mat.camera_targets[name], float)


def lock_targets(center_a, center_b) -> tuple[np.ndarray, np.ndarray]:
    """A symmetric target pair from where the cameras are now: A's spot averaged with B's mirrored spot."""
    eye_a = (np.asarray(center_a, float) + np.asarray(center_b, float) * MIRROR) / 2
    return eye_a, eye_a * MIRROR


def target_pose(eye, aim) -> tuple[np.ndarray, np.ndarray]:
    return look_at_pose(np.asarray(eye, float), np.asarray(aim, float))


def ghost_polylines(mat: MatConfig, K, eye, aim) -> list[np.ndarray]:
    """Mat outline and marker squares as they would appear from the target spot (pixels, undistorted)."""
    R, t = target_pose(eye, aim)
    shapes = [np.array([[x, 0.0, z] for x, z in mat.mat_polygon()])] + list(mat.marker_corners().values())
    out = []
    for X in shapes:
        if np.all((X @ R.T + t)[:, 2] > 0.05):  # skip anything behind the camera
            out.append(project(X, K, None, R, t))
    return out


def layout(R, t) -> dict:
    """Floor distance from mat centre, height and bearing around the mat (A ≈ +45°, B ≈ -45°)."""
    c = cam_center(R, t)
    return {"dist_m": float(np.hypot(c[0], c[2])), "height_m": float(c[1]),
            "bearing_deg": float(np.degrees(np.arctan2(c[0], c[2])))}


def comparison_lines(layouts: dict[str, dict]) -> list[str]:
    lines = [f"{n}: {v['dist_m']:.2f} m from centre, {v['height_m']:.2f} m high, at {v['bearing_deg']:+.0f}°"
             for n, v in sorted(layouts.items())]
    if "A" in layouts and "B" in layouts:
        a, b = layouts["A"], layouts["B"]
        lines.append(f"A-B difference: distance {abs(a['dist_m'] - b['dist_m']) * 100:.0f} cm, "
                     f"height {abs(a['height_m'] - b['height_m']) * 100:.0f} cm, "
                     f"angle {abs(abs(a['bearing_deg']) - abs(b['bearing_deg'])):.1f}°")
    return lines


def _dashed(img, poly, color, dash_px: float = 10.0) -> None:
    poly = np.vstack([poly, poly[:1]])
    for p, q in zip(poly[:-1], poly[1:]):
        n = max(int(np.linalg.norm(q - p) / dash_px), 1)
        for k in range(0, n, 2):
            a, b = p + (q - p) * k / n, p + (q - p) * min(k + 1, n) / n
            cv2.line(img, pt(a), pt(b), color, 2)


def draw_overlay(tile, scale, K, R, t, mat: MatConfig, eye, hint: dict | None) -> None:
    """Crosshair at the image centre, aim dot, ghost mat, and the position/aim instructions."""
    for poly in ghost_polylines(mat, K, eye, mat.aim_point):
        _dashed(tile, poly * scale, GREEN)
    c = np.asarray(K, float)[:2, 2] * scale
    cv2.line(tile, pt(c - [14, 0]), pt(c + [14, 0]), WHITE, 1)
    cv2.line(tile, pt(c - [0, 14]), pt(c + [0, 14]), WHITE, 1)
    if R is not None:
        aim_px = project(mat.aim_point, K, None, R, t)[0] * scale
        if np.all(np.isfinite(aim_px)):
            cv2.circle(tile, pt(aim_px), 6, RED, 2)
    if hint is not None:
        y = tile.shape[0] - 40
        draw_text(tile, hint["position_msg"], (8, y), GREEN if hint["position_ok"] else YELLOW, 0.6)
        draw_text(tile, hint["aim_msg"], (8, y + 24), GREEN if hint["aim_ok"] else YELLOW, 0.6)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_placement.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/placement.py vision/tests/test_placement.py
git commit -m "vision: placement targets, ghost mat and A/B comparison"
```

---

### Task 4: Focal refinement in the bundle adjustment

**Files:**
- Modify: `vision/bundle.py`
- Modify: `vision/tests/test_bundle.py`

**Interfaces:**
- Produces: `BundleReport.K: dict[str, (3,3)]`, `.focal_before`, `.focal_after: dict[str, float]`; `summary()` mentions `focal`.
- Produces: `evaluate_guards(..., marker_after, focal_scale: dict[str, float] | None = None, max_move_m=0.5, max_rot_deg=15.0, max_focal_change=0.15)` (existing positional calls unchanged).
- Consumed by Task 7 (`calib_refine` saves `report.K[n]`).

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_bundle.py`:

```diff
--- a/vision/tests/test_bundle.py
+++ b/vision/tests/test_bundle.py
@@ -76,3 +76,30 @@
     assert ok == []
     bad = evaluate_guards(2.0, 3.0, {"A": 0.7}, {"A": 20.0}, {"A": 0.5}, {"A": 3.0})
     assert len(bad) == 4
+
+
+def _with_focal(K, s):
+    K = np.array(K, float)
+    K[0, 0] *= s
+    K[1, 1] *= s
+    return K
+
+
+def test_refine_recovers_wrong_focal_length():
+    truth, pairs, rng = _scene()
+    start = {n: _perturbed(c, rng, 0.05, 1.5) for n, c in truth.items()}
+    start["A"] = replace(start["A"], K=_with_focal(truth["A"].K, 1.08))
+    start["B"] = replace(start["B"], K=_with_focal(truth["B"].K, 0.95))
+    report = refine_extrinsics(start, MAT, pairs)
+    assert report.ok, report.reasons
+    for n in ("A", "B"):
+        assert abs(report.focal_after[n] / truth[n].K[0, 0] - 1) < 0.02
+        np.testing.assert_allclose(report.K[n][:2, 2], truth[n].K[:2, 2])
+        assert np.linalg.norm(report.centers[n] - cam_center(truth[n].R, truth[n].t)) < 0.03
+    assert "focal" in report.summary()
+
+
+def test_focal_guard():
+    assert evaluate_guards(5.0, 2.0, {}, {}, {}, {}, {"A": 1.10}) == []
+    bad = evaluate_guards(5.0, 2.0, {}, {}, {}, {}, {"A": 1.20})
+    assert len(bad) == 1 and "focal" in bad[0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_bundle.py -q`
Expected: 2 FAIL — `test_refine_recovers_wrong_focal_length` with `AttributeError: 'BundleReport' object has no attribute 'focal_after'` (or a refinement that cannot fix the focal), and `test_focal_guard` with `AssertionError` (the old guard ignores the focal scale and returns `[]`).

- [ ] **Step 3: Implement**

Apply to `vision/bundle.py`:

```diff
--- a/vision/bundle.py
+++ b/vision/bundle.py
@@ -1,8 +1,9 @@
 """Refine both camera poses from body observations, pinned to the floor markers.
 
 Parameters: each camera's pose (Rodrigues vector + translation, mat -> camera) and one 3D point
-per selected (frame, joint). Residuals: pinhole reprojection of the body points in both views and
-of the fixed marker corners (weighted), on undistorted pixel observations. Intrinsics are fixed.
+per selected (frame, joint), plus each camera's focal-length scale. Residuals: pinhole reprojection of
+the body points in both views and of the fixed marker corners (weighted), on undistorted pixel
+observations. The principal point and distortion are fixed.
 """
 from __future__ import annotations
 
@@ -20,6 +21,7 @@
 BA_JOINTS = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32]
 NAMES = ("A", "B")
 MIN_POINTS = 50
+N_CAM = 7  # Rodrigues vector, translation, focal scale
 
 
 @dataclass
@@ -35,6 +37,9 @@
     centers: dict[str, np.ndarray]
     moved_m: dict[str, float]
     rotated_deg: dict[str, float]
+    K: dict[str, np.ndarray]
+    focal_before: dict[str, float]
+    focal_after: dict[str, float]
     baseline_m: float
     median_angle_deg: float
     n_frames: int
@@ -50,7 +55,8 @@
             mb, ma = self.marker_rms_before.get(n), self.marker_rms_after.get(n)
             markers = f"marker RMS {mb:.2f} -> {ma:.2f} px, " if mb is not None else "no markers, "
             lines.append(f"camera {n}: {markers}centre ({c[0]:+.3f}, {c[1]:.3f}, {c[2]:+.3f}) m, "
-                         f"moved {self.moved_m[n] * 100:.1f} cm, rotated {self.rotated_deg[n]:.2f}°")
+                         f"moved {self.moved_m[n] * 100:.1f} cm, rotated {self.rotated_deg[n]:.2f}°, "
+                         f"focal {self.focal_before[n]:.0f} -> {self.focal_after[n]:.0f} px")
         lines.append(f"baseline {self.baseline_m:.3f} m, median triangulation angle {self.median_angle_deg:.1f}°")
         lines.append("OK" if self.ok else "REJECTED: " + "; ".join(self.reasons))
         return "\n".join(lines)
@@ -66,6 +72,13 @@
     return np.eye(3) + np.sin(theta) * Kx + (1 - np.cos(theta)) * Kx @ Kx
 
 
+def _scaled(K, s: float) -> np.ndarray:
+    K = np.array(K, float)
+    K[0, 0] *= s
+    K[1, 1] *= s
+    return K
+
+
 def _pinhole(P, K, R, t) -> np.ndarray:
     Xc = P @ R.T + t
     uv = Xc[:, :2] / Xc[:, 2:3]
@@ -81,7 +94,8 @@
 
 
 def evaluate_guards(body_before, body_after, moved_m, rotated_deg, marker_before, marker_after,
-                    max_move_m: float = 0.5, max_rot_deg: float = 15.0) -> list[str]:
+                    focal_scale: dict[str, float] | None = None, max_move_m: float = 0.5,
+                    max_rot_deg: float = 15.0, max_focal_change: float = 0.15) -> list[str]:
     reasons = []
     if not body_after < body_before:
         reasons.append(f"body reprojection did not improve ({body_before:.2f} -> {body_after:.2f} px)")
@@ -95,6 +109,9 @@
         limit = max(2 * marker_before[n], 1.0)
         if after > limit:
             reasons.append(f"camera {n} marker RMS grew to {after:.2f} px (limit {limit:.2f} px)")
+    for n, s in (focal_scale or {}).items():
+        if abs(s - 1) > max_focal_change:
+            reasons.append(f"camera {n} focal length changed {abs(s - 1) * 100:.0f}% (limit {max_focal_change * 100:.0f}%)")
     return reasons
 
 
@@ -115,26 +132,27 @@
 def _sparsity(n_pts: int, marker_rows: dict[str, int]) -> coo_matrix:
     rows, cols = [], []
     i = np.arange(n_pts)
-    for k, off in enumerate((0, 6)):  # body residuals of camera A then B
-        base = k * 2 * n_pts
+    first = 2 * N_CAM
+    for k in range(2):  # body residuals of camera A then B
+        off, base = k * N_CAM, k * 2 * n_pts
         for r in (0, 1):
             rr = base + 2 * i + r
-            for c in range(6):
+            for c in range(N_CAM):
                 rows.append(rr)
                 cols.append(np.full(n_pts, off + c))
             for c in range(3):
                 rows.append(rr)
-                cols.append(12 + 3 * i + c)
+                cols.append(first + 3 * i + c)
     row = 4 * n_pts
-    for n in NAMES:
+    for k, n in enumerate(NAMES):
         m = marker_rows.get(n, 0)
         if m:
-            off = 0 if n == "A" else 6
-            rows.append(np.repeat(np.arange(row, row + m), 6))
-            cols.append(np.tile(np.arange(off, off + 6), m))
+            off = k * N_CAM
+            rows.append(np.repeat(np.arange(row, row + m), N_CAM))
+            cols.append(np.tile(np.arange(off, off + N_CAM), m))
             row += m
     r, c = np.concatenate(rows), np.concatenate(cols)
-    return coo_matrix((np.ones(len(r)), (r, c)), shape=(row, 12 + 3 * n_pts))
+    return coo_matrix((np.ones(len(r)), (r, c)), shape=(row, first + 3 * n_pts))
 
 
 def refine_extrinsics(cams: dict[str, CameraConfig], mat: MatConfig, pairs, marker_weight: float = 5.0,
@@ -169,14 +187,16 @@
 
     obs = {"A": _undistort(uv_a, ca), "B": _undistort(uv_b, cb)}
     K = {n: cams[n].K for n in NAMES}
-    x0 = np.concatenate([cv2.Rodrigues(ca.R)[0].ravel(), np.ravel(ca.t),
-                         cv2.Rodrigues(cb.R)[0].ravel(), np.ravel(cb.t), X0.ravel()])
+    x0 = np.concatenate([cv2.Rodrigues(ca.R)[0].ravel(), np.ravel(ca.t), [1.0],
+                         cv2.Rodrigues(cb.R)[0].ravel(), np.ravel(cb.t), [1.0], X0.ravel()])
 
     def pieces(x):
-        poses = {"A": (rodrigues(x[0:3]), x[3:6]), "B": (rodrigues(x[6:9]), x[9:12])}
-        P = x[12:].reshape(-1, 3)
-        body = {n: _pinhole(P, K[n], *poses[n]) - obs[n] for n in NAMES}
-        mark = {n: _pinhole(M3, K[n], *poses[n]) - m2 for n, (M3, m2) in markers.items()}
+        cam = {n: x[k * N_CAM:(k + 1) * N_CAM] for k, n in enumerate(NAMES)}
+        poses = {n: (rodrigues(c[0:3]), c[3:6]) for n, c in cam.items()}
+        Ks = {n: _scaled(K[n], c[6]) for n, c in cam.items()}
+        P = x[2 * N_CAM:].reshape(-1, 3)
+        body = {n: _pinhole(P, Ks[n], *poses[n]) - obs[n] for n in NAMES}
+        mark = {n: _pinhole(M3, Ks[n], *poses[n]) - m2 for n, (M3, m2) in markers.items()}
         return body, mark, poses
 
     def residuals(x):
@@ -199,15 +219,19 @@
     centers = {n: cam_center(R[n], t[n]) for n in NAMES}
     moved = {n: float(np.linalg.norm(centers[n] - cam_center(*poses0[n]))) for n in NAMES}
     rotated = {n: rotation_angle_deg(R[n], poses0[n][0]) for n in NAMES}
-    P = result.x[12:].reshape(-1, 3)
+    scale = {n: float(result.x[k * N_CAM + 6]) for k, n in enumerate(NAMES)}
+    P = result.x[2 * N_CAM:].reshape(-1, 3)
     ra, rb = P - centers["A"], P - centers["B"]
     cos = np.sum(ra * rb, axis=1) / (np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1))
-    reasons = evaluate_guards(body_before, body_after, moved, rotated, marker_before, marker_after)
+    reasons = evaluate_guards(body_before, body_after, moved, rotated, marker_before, marker_after, scale)
     return BundleReport(
         ok=not reasons, reasons=reasons, R=R, t=t,
         body_rms_before=body_before, body_rms_after=body_after,
         marker_rms_before=marker_before, marker_rms_after=marker_after,
         centers=centers, moved_m=moved, rotated_deg=rotated,
+        K={n: _scaled(K[n], scale[n]) for n in NAMES},
+        focal_before={n: float(K[n][0, 0]) for n in NAMES},
+        focal_after={n: float(K[n][0, 0] * scale[n]) for n in NAMES},
         baseline_m=float(np.linalg.norm(centers["A"] - centers["B"])),
         median_angle_deg=float(np.degrees(np.median(np.arccos(np.clip(cos, -1, 1))))),
         n_frames=int(len(np.unique(frame))), n_points=n_pts,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_bundle.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/bundle.py vision/tests/test_bundle.py
git commit -m "vision: refine each camera's focal length in the bundle adjustment"
```

---

### Task 5: Camera-moved alarm (`moved.py`)

**Files:**
- Create: `vision/moved.py`
- Create: `vision/tests/test_moved.py`

**Interfaces:**
- Consumes: `CameraConfig.marker_obs`; any detector with `.detect(image) -> dict[int, (4,2)]` (`MarkerDetector`).
- Produces: `MoveMonitor(cams, detector=None, interval_s=1.0, threshold_px=3.0, confirm=3, blind_s=10.0)` with `update(name, image, t) -> str`, `observe(name, detections, t) -> str`, `status(name) -> 'ok'|'moved'|'blind'|'unknown'`, `shift_px(name) -> float | None`, `message(name) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_moved.py`:

```python
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import project
from vision.moved import MoveMonitor
from vision.tests.synth import make_cam

MAT = MatConfig()


def _cams():
    cam = make_cam("A", (3.18, 0.85, 3.18))
    cam.marker_obs = {i: project(c, cam.K, None, cam.R, cam.t) for i, c in MAT.marker_corners().items()}
    return {"A": cam, "B": CameraConfig(name="B", index=1)}


def _shifted(cams, dx):
    return {i: c + [dx, 0.0] for i, c in cams["A"].marker_obs.items()}


def test_steady_shift_is_moved_after_three_checks():
    cams = _cams()
    mon = MoveMonitor(cams)
    assert [mon.observe("A", _shifted(cams, 5.0), t) for t in range(3)] == ["ok", "ok", "moved"]
    assert abs(mon.shift_px("A") - 5.0) < 1e-9 and "has moved (5 px)" in mon.message("A")


def test_one_off_outliers_and_missing_markers_are_not_moved():
    cams = _cams()
    mon = MoveMonitor(cams)
    seq = [_shifted(cams, 8.0), {}, _shifted(cams, 0.2), _shifted(cams, 8.0), {0: cams["A"].marker_obs[0]}]
    assert all(mon.observe("A", d, t) == "ok" for t, d in enumerate(seq))
    assert mon.message("A") is None


def test_moved_clears_after_three_good_checks():
    cams = _cams()
    mon = MoveMonitor(cams)
    for t in range(3):
        mon.observe("A", _shifted(cams, 5.0), t)
    assert [mon.observe("A", _shifted(cams, 0.0), 3 + t) for t in range(3)] == ["moved", "moved", "ok"]


def test_all_markers_hidden_is_blind_not_moved():
    cams = _cams()
    mon = MoveMonitor(cams, blind_s=10.0)
    mon.observe("A", _shifted(cams, 0.0), 0.0)
    assert mon.observe("A", {}, 9.0) == "ok"
    assert mon.observe("A", {}, 10.5) == "blind" and "markers hidden" in mon.message("A")
    assert mon.observe("A", _shifted(cams, 0.0), 11.0) == "ok"


def test_uncalibrated_camera_is_unknown():
    mon = MoveMonitor(_cams())
    assert mon.observe("B", {}, 0.0) == "unknown" and mon.message("B") is None


class _FakeDetector:
    def __init__(self, det):
        self.det, self.calls = det, 0

    def detect(self, image):
        self.calls += 1
        return self.det


def test_update_is_rate_limited():
    cams = _cams()
    det = _FakeDetector(_shifted(cams, 5.0))
    mon = MoveMonitor(cams, det, interval_s=1.0)
    for t in np.arange(0, 2.95, 0.1):
        mon.update("A", None, float(t))
    assert det.calls == 3 and mon.status("A") == "moved"
    mon.update("B", None, 5.0)
    assert det.calls == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_moved.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'vision.moved'`.

- [ ] **Step 3: Implement**

Create `vision/moved.py`:

```python
"""Camera-moved alarm: compare live marker detections with the ones saved at calibration.

Statuses: "ok", "moved" (markers shifted > threshold on `confirm` checks in a row), "blind" (no saved
marker seen for `blind_s`, e.g. someone standing over them), "unknown" (never calibrated with markers).
"""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig


class MoveMonitor:
    def __init__(self, cams: dict[str, CameraConfig], detector=None, interval_s: float = 1.0,
                 threshold_px: float = 3.0, confirm: int = 3, blind_s: float = 10.0):
        self.detector = detector
        self.interval_s, self.threshold_px, self.confirm, self.blind_s = interval_s, threshold_px, confirm, blind_s
        self._ref = {n: c.marker_obs for n, c in cams.items() if c.marker_obs}
        self._state = {n: "ok" if n in self._ref else "unknown" for n in cams}
        self._shift: dict[str, float | None] = {n: None for n in cams}
        self._last_check: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        self._above = {n: 0 for n in cams}
        self._below = {n: 0 for n in cams}

    def update(self, name: str, image, t: float) -> str:
        """Detect markers in `image` at most once per interval and update the camera's status."""
        if name not in self._ref or t - self._last_check.get(name, -np.inf) < self.interval_s:
            return self.status(name)
        self._last_check[name] = t
        return self.observe(name, self.detector.detect(image), t)

    def observe(self, name: str, detections: dict[int, np.ndarray], t: float) -> str:
        ref = self._ref.get(name)
        if ref is None:
            return self.status(name)
        self._last_seen.setdefault(name, t)
        common = sorted(set(ref) & set(detections))
        if not common:
            if t - self._last_seen[name] >= self.blind_s and self._state[name] == "ok":
                self._state[name] = "blind"
            return self._state[name]
        self._last_seen[name] = t
        shift = float(np.median([np.median(np.linalg.norm(detections[i] - ref[i], axis=1)) for i in common]))
        self._shift[name] = shift
        if shift > self.threshold_px:
            self._above[name] += 1
            self._below[name] = 0
            if self._above[name] >= self.confirm:
                self._state[name] = "moved"
        else:
            self._below[name] += 1
            self._above[name] = 0
            if self._state[name] == "blind" or (self._state[name] == "moved" and self._below[name] >= self.confirm):
                self._state[name] = "ok"
        return self._state[name]

    def status(self, name: str) -> str:
        return self._state.get(name, "unknown")

    def shift_px(self, name: str) -> float | None:
        return self._shift.get(name)

    def message(self, name: str) -> str | None:
        """Banner text for the live tools, or None when there is nothing to say."""
        s = self.status(name)
        if s == "moved":
            return f"Camera {name} has moved ({self.shift_px(name):.0f} px): re-run calib_extrinsics"
        if s == "blind":
            return f"can't check camera {name} (markers hidden)"
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_moved.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/moved.py vision/tests/test_moved.py
git commit -m "vision: camera-moved alarm from saved marker detections"
```

---

### Task 6: Checks: drop lens RMS, add height and camera-moved checks

**Files:**
- Modify: `vision/checks.py`
- Modify: `vision/tests/test_checks.py`

**Interfaces:**
- Consumes: `MoveMonitor` (Task 5), `MarkerDetector`.
- Produces: `estimate_height_cm(seq: (F,33,3)) -> float`; `run_checks(outputs, cams, mat, fps=None, height_cm=None, moved: dict[str, (status, shift)] | None = None)`; `_live_outputs(cams, mat, model, seconds) -> (outputs, fps, moved)`; CLI `--height-cm`.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_checks.py`:

```diff
--- a/vision/tests/test_checks.py
+++ b/vision/tests/test_checks.py
@@ -1,7 +1,7 @@
 import numpy as np
 
 from vision.checks import (
-    bone_length_stats, outputs_from_recording, format_checks, knee_angle_deg, marker_center_distances, run_checks,
+    estimate_height_cm, bone_length_stats, outputs_from_recording, format_checks, knee_angle_deg, marker_center_distances, run_checks,
     triangulate_marker_centers,
 )
 from vision.config import MatConfig
@@ -15,7 +15,6 @@
 def _cams():
     cams = target_cams()
     for cam in cams.values():
-        cam.intrinsics_rms = 0.3
         cam.marker_obs = {i: project(c, cam.K, cam.dist, cam.R, cam.t) for i, c in MAT.marker_corners().items()}
     return cams
 
@@ -48,7 +47,8 @@
                          pose_from_points(cams["B"], X, noise_px=0.3, rng=rng), 0.005) for k in range(40)]
     checks = run_checks(outs, cams, MAT, fps={"A": 30.0, "B": 29.5})
     names = {c.name for c in checks}
-    assert {"lens RMS A", "frames tracked in 3D", "mean body reprojection", "thigh_l length std",
+    assert not any(c.name.startswith("lens RMS") for c in checks)
+    assert {"frames tracked in 3D", "mean body reprojection", "thigh_l length std",
             "standing knee angle l", "median triangulation angle", "marker distance 0–2",
             "pair skew p95", "capture fps A"} <= names
     failed = [c for c in checks if c.passed is False]
@@ -74,3 +74,22 @@
                                    pose_from_points(cams["B"], X, noise_px=4.0, rng=rng), 0.0))
     for out in outputs_from_recording(path, cams):
         np.testing.assert_allclose(out.frame.joints[out.tri.ok], out.tri.points[out.tri.ok])
+
+
+def test_height_estimate_and_check():
+    cams, X = _cams(), standing_skeleton()
+    expected = (X[2, 1] + X[5, 1]) / 2 / 0.936 * 100
+    assert abs(estimate_height_cm(np.stack([X] * 10)) - expected) < 1e-9
+    pipe = Pipeline(cams)
+    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0) for k in range(20)]
+    ok = {c.name: c for c in run_checks(outs, cams, MAT, height_cm=expected * 1.03)}["estimated height"]
+    bad = {c.name: c for c in run_checks(outs, cams, MAT, height_cm=expected * 1.10)}["estimated height"]
+    assert ok.passed is True and bad.passed is False
+    assert "estimated height" not in {c.name for c in run_checks(outs, cams, MAT)}
+
+
+def test_camera_moved_checks():
+    checks = {c.name: c for c in run_checks([], {}, MAT, moved={"A": ("ok", 0.8), "B": ("moved", 6.0)})}
+    assert checks["camera A not moved"].passed is True and checks["camera B not moved"].passed is False
+    blind = {c.name: c for c in run_checks([], {}, MAT, moved={"A": ("blind", None)})}
+    assert blind["camera A not moved"].passed is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_checks.py -q`
Expected: FAIL at collection — `ImportError: cannot import name 'estimate_height_cm' from 'vision.checks'`.

- [ ] **Step 3: Implement**

Apply to `vision/checks.py`:

```diff
--- a/vision/checks.py
+++ b/vision/checks.py
@@ -1,7 +1,7 @@
 """Numeric sanity checks for the 3D skeleton.
 
-    python -m vision.checks               # 5 s live: stand still, facing Mira, in view of both cameras
-    python -m vision.checks --from FILE   # on a recording, re-triangulated with the current calibration
+    python -m vision.checks --height-cm 180   # 5 s live: stand still, facing Mira, in view of both cameras
+    python -m vision.checks --from FILE       # on a recording, re-triangulated with the current calibration
 """
 from __future__ import annotations
 
@@ -19,6 +19,8 @@
 BONES_CHECKED = {"thigh_l": (23, 25), "thigh_r": (24, 26), "shin_l": (25, 27), "shin_r": (26, 28)}
 KNEE = {"l": (23, 25, 27), "r": (24, 26, 28)}
 MARKER_PAIRS = [(0, 2), (1, 3), (0, 1), (2, 3)]
+EYES = (2, 5)
+EYE_HEIGHT_RATIO = 0.936  # eye height / stature (adult anthropometry); MediaPipe has no head-top point
 
 
 @dataclass
@@ -47,6 +49,13 @@
     return float(np.degrees(np.arccos(np.clip(c, -1, 1))))
 
 
+def estimate_height_cm(seq: np.ndarray) -> float:
+    """Stature from the median eye height above the mat surface (y = 0)."""
+    eye_y = np.nanmean(seq[:, list(EYES), 1], axis=1)
+    eye_y = eye_y[np.isfinite(eye_y)]
+    return float(np.median(eye_y) / EYE_HEIGHT_RATIO * 100) if len(eye_y) else float("nan")
+
+
 def triangulate_marker_centers(cam_a: CameraConfig, cam_b: CameraConfig) -> dict[int, np.ndarray]:
     obs_a, obs_b = cam_a.marker_obs or {}, cam_b.marker_obs or {}
     out = {}
@@ -69,12 +78,10 @@
 
 
 def run_checks(outputs: list[PipelineOutput], cams: dict[str, CameraConfig], mat: MatConfig,
-               fps: dict[str, float] | None = None) -> list[Check]:
+               fps: dict[str, float] | None = None, height_cm: float | None = None,
+               moved: dict[str, tuple[str, float | None]] | None = None) -> list[Check]:
+    """`moved`: camera name -> (MoveMonitor status, shift px), from a live run."""
     checks = []
-    for n in ("A", "B"):
-        rms = cams[n].intrinsics_rms if n in cams else None
-        checks.append(_check(f"lens RMS {n}", rms, "px", "< 0.5", lambda v: v < 0.5))
-
     n3d = sum(o.frame.tracking == "3d" for o in outputs)
     frac = 100.0 * n3d / len(outputs) if outputs else None
     checks.append(_check("frames tracked in 3D", frac, "%", "≥ 90", lambda v: v >= 90))
@@ -93,6 +100,11 @@
         checks.append(_check(f"standing knee angle {side}", float(np.median(vals)) if vals else None, "°", "170–180",
                              lambda v: 170 <= v <= 180))
 
+    if height_cm is not None:
+        est = estimate_height_cm(seq) if seq is not None else None
+        checks.append(_check("estimated height", est, "cm", f"{height_cm:.0f} ± 5%",
+                             lambda v: abs(v / height_cm - 1) <= 0.05))
+
     angles = np.concatenate([t.angle_deg[t.ok] for t in tris]) if tris else np.empty(0)
     checks.append(_check("median triangulation angle", float(np.median(angles)) if angles.size else None, "°", "60–100",
                          lambda v: 60 <= v <= 100))
@@ -108,6 +120,10 @@
                          lambda v: v <= 33))
     for n, v in sorted((fps or {}).items()):
         checks.append(_check(f"capture fps {n}", v, "fps", "≥ 20", lambda x: x >= 20))
+    for n, (status, shift) in sorted((moved or {}).items()):
+        value = shift if status in ("ok", "moved") else None
+        checks.append(Check(f"camera {n} not moved", value, "px", "< 3",
+                            None if value is None else status != "moved"))
     return checks
 
 
@@ -127,11 +143,14 @@
     return list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False))
 
 
-def _live_outputs(cams, model: str, seconds: float):
+def _live_outputs(cams, mat, model: str, seconds: float):
     from vision.capture import mono_to_unix
     from vision.live import LiveSource
+    from vision.markers import MarkerDetector
+    from vision.moved import MoveMonitor
 
     outputs = []
+    monitor = MoveMonitor(cams, MarkerDetector(mat), interval_s=0.25)
     pipeline = Pipeline(cams, smoothing=False)  # smoothing would hide the jitter these checks measure
     with LiveSource(cams, model) as src:
         problems = src.wait_ready()
@@ -144,8 +163,12 @@
                 out = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
                 if time.monotonic() - start >= 2.0:
                     outputs.append(out)
+            for n, v in src.latest_frames().items():
+                if v is not None:
+                    monitor.update(n, v[0], time.monotonic())
         fps = {n: s["fps"] for n, s in src.stats().items()}
-    return outputs, fps
+    moved = {n: (monitor.status(n), monitor.shift_px(n)) for n in cams}
+    return outputs, fps, moved
 
 
 def main() -> int:
@@ -153,13 +176,14 @@
     p.add_argument("--seconds", type=float, default=5.0)
     p.add_argument("--from", dest="from_path", type=Path)
     p.add_argument("--model", default="full", choices=["full", "heavy"])
+    p.add_argument("--height-cm", type=float, help="your height, to check the 3D scale")
     args = p.parse_args()
     cams, mat = load_cameras(), load_mat()
     if args.from_path:
-        outputs, fps = outputs_from_recording(args.from_path, cams), None
+        outputs, fps, moved = outputs_from_recording(args.from_path, cams), None, None
     else:
-        outputs, fps = _live_outputs(cams, args.model, args.seconds)
-    checks = run_checks(outputs, cams, mat, fps)
+        outputs, fps, moved = _live_outputs(cams, mat, args.model, args.seconds)
+    checks = run_checks(outputs, cams, mat, fps, args.height_cm, moved)
     print(format_checks(checks))
     return 0 if all(c.passed is not False for c in checks) else 1
 
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_checks.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/checks.py vision/tests/test_checks.py
git commit -m "vision: height and camera-moved checks; drop lens RMS checks"
```

---

### Task 7: Wire the tools: `calib_extrinsics` overlay, alarm in `debug_view` and `calib_refine`

**Files:**
- Modify (full rewrite): `vision/calib_extrinsics.py`
- Modify: `vision/debug_view.py`
- Modify: `vision/tests/test_debug_view.py`
- Modify: `vision/calib_refine.py`

**Interfaces:**
- Consumes: `solve_with_zoom`, `placement_hint` (Task 2); `target_eye`, `lock_targets`, `layout`, `comparison_lines`, `draw_overlay` (Task 3); `BundleReport.K` (Task 4); `MoveMonitor` (Task 5).
- Produces: `compose_view(..., detector=None, alarms: dict[str, str | None] | None = None)`; `calib_refine.moved_cameras(src, mat, seconds=2.0) -> list[str]`; `capture_live(cams, mat, model, seconds, force=False)`.

- [ ] **Step 1: Write the failing test**

Apply to `vision/tests/test_debug_view.py`:

```diff
--- a/vision/tests/test_debug_view.py
+++ b/vision/tests/test_debug_view.py
@@ -30,3 +30,12 @@
     assert img.shape == (720, 1280, 3)
     img = compose_view({}, MatConfig(), {}, {}, None, None, None, None)
     assert img.shape == (720, 1280, 3)
+
+
+def test_compose_view_shows_camera_moved_banner():
+    cams = target_cams()
+    plain = compose_view(cams, MatConfig(), {}, {}, None, None, None, None)
+    alarm = compose_view(cams, MatConfig(), {}, {}, None, None, None, None,
+                         alarms={"A": "Camera A has moved (6 px): re-run calib_extrinsics", "B": None})
+    assert (alarm[:360, :640] != plain[:360, :640]).any()
+    np.testing.assert_array_equal(alarm[:360, 640:], plain[:360, 640:])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest vision/tests/test_debug_view.py -q`
Expected: FAIL — `TypeError: compose_view() got an unexpected keyword argument 'alarms'`.

- [ ] **Step 3: Implement `debug_view`**

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -21,6 +21,7 @@
 from vision.draw import GREEN, MAGENTA, RED, WHITE, YELLOW, draw_points, draw_skeleton, draw_text, fit, pt
 from vision.frames import cam_center, project
 from vision.markers import MarkerDetector
+from vision.moved import MoveMonitor
 from vision.pipeline import Pipeline, PipelineOutput
 from vision.record import Recorder, default_path
 from vision.replay import cams_from_header, read_header, replay_outputs
@@ -132,7 +133,7 @@
 
 def compose_view(cams: dict[str, CameraConfig], mat: MatConfig, images: dict, poses: dict,
                  out: PipelineOutput | None, stats: dict | None, skew: dict | None, recording: str | None,
-                 detector: MarkerDetector | None = None) -> np.ndarray:
+                 detector: MarkerDetector | None = None, alarms: dict[str, str | None] | None = None) -> np.ndarray:
     frame = out.frame if out is not None else None
     panels = []
     for name in ("A", "B"):
@@ -141,7 +142,7 @@
         s = (stats or {}).get(name)
         draw_text(panel, f"{name}  {s['fps']:.0f} fps  pose {s['pose_ms']:.0f} ms" if s else name, (8, 20))
         y = 40
-        for msg in ((s or {}).get("error"), (s or {}).get("warning")):
+        for msg in ((s or {}).get("error"), (s or {}).get("warning"), (alarms or {}).get(name)):
             if msg:
                 draw_text(panel, msg, (8, y), RED, 0.45)
                 y += 18
@@ -172,6 +173,7 @@
 
     detector, show_markers = MarkerDetector(mat), False
     pipeline = Pipeline(cams)
+    monitor = MoveMonitor(cams, detector)
     recorder: Recorder | None = None
     last: PipelineOutput | None = None
     last_draw = 0.0
@@ -188,9 +190,12 @@
                     latest = src.latest_frames()
                     images = {n: v[0] for n, v in latest.items() if v}
                     poses = {n: v[1] for n, v in latest.items() if v}
+                    for n, image in images.items():
+                        monitor.update(n, image, now)
+                    alarms = {n: monitor.message(n) for n in cams}
                     rec = f"REC {recorder.count} frames -> {recorder.path.name}" if recorder else None
                     img = compose_view(cams, mat, images, poses, last, src.stats(), src.skew.summary(), rec,
-                                       detector if show_markers else None)
+                                       detector if show_markers else None, alarms)
                     cv2.imshow(WINDOW, img)
                 key = cv2.waitKey(1) & 0xFF
                 if key in (ord("q"), 27):
```

- [ ] **Step 4: Rewrite `calib_extrinsics`**

Replace `vision/calib_extrinsics.py` with:

```python
"""Measure each camera's zoom and pose from the four floor markers, and guide placement.

    python -m vision.calib_extrinsics

Keep the mat empty. Each camera shows the detected markers (yellow), a ghost of where the mat should
appear from the target spot (green dashed), a crosshair and an aim dot (red), and instructions:
position first, then aim. Keys: t lock the current spots as the symmetric target (first time, or
after moving rooms), s save, q quit without saving.
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np

from vision.capture import Camera, camera_problem
from vision.config import load_cameras, load_mat, save_cameras
from vision.draw import RED, WHITE, YELLOW, draw_text, fit, pt
from vision.frames import cam_center
from vision.markers import MarkerDetector, average_detections, camera_warnings, placement_hint, solve_with_zoom
from vision.placement import comparison_lines, draw_overlay, layout, lock_targets, target_eye

TILE = (640, 360)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    missing = [n for n in ("A", "B") if n not in cams]
    if missing:
        print(f"camera(s) {missing} are not configured: run python -m vision.cams first")
        return 1

    detector = MarkerDetector(mat)
    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in ("A", "B")}
    for c in cameras.values():
        c.start()
    history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
    last_seq = {n: -1 for n in cameras}
    last_good = {n: None for n in cameras}
    results: dict[str, tuple] = {}  # name -> (ZoomSolve, obs, hint, warnings)
    last_solve = last_print = 0.0
    print(__doc__)
    try:
        while True:
            tiles = []
            problems = {n: camera_problem(cams[n], cam.error, cam.actual_size) for n, cam in cameras.items()}
            for n, cam in cameras.items():
                item = cam.latest()
                if item is None:
                    tile = np.zeros((TILE[1], TILE[0], 3), np.uint8)
                    draw_text(tile, problems[n] or f"{n}: waiting for frames", (8, 22), RED, 0.5)
                    tiles.append(tile)
                    continue
                seq, _, frame = item
                if seq != last_seq[n]:
                    last_seq[n] = seq
                    history[n].append(detector.detect(frame))
                tile = fit(frame, TILE)
                scale = np.array([TILE[0] / frame.shape[1], TILE[1] / frame.shape[0]])
                for i, c in (history[n][-1] if history[n] else {}).items():
                    cv2.polylines(tile, [(c * scale).astype(np.int32)], True, YELLOW, 2)
                    draw_text(tile, str(i), pt(c[0] * scale), YELLOW)
                draw_text(tile, n, (8, 22))
                y = 44
                if n in results:
                    sol, obs, hint, warns = results[n]
                    draw_overlay(tile, scale, sol.K, sol.R, sol.t, mat, target_eye(cams[n], mat, n), hint)
                    draw_text(tile, f"markers {sorted(obs)}  rms {sol.rms:.2f} px", (8, y))
                    draw_text(tile, sol.zoom_msg, (8, y + 22), YELLOW if sol.guessed else WHITE, 0.45)
                    y += 44
                    for w in warns:
                        draw_text(tile, w, (8, y), RED, 0.45)
                        y += 18
                if problems[n]:
                    draw_text(tile, problems[n], (8, y), RED, 0.45)
                tiles.append(tile)
            now = time.monotonic()
            if now - last_solve > 0.5:
                last_solve = now
                for n, cam in cameras.items():
                    if problems[n]:  # e.g. wrong resolution: a pose solved for this image size would be wrong
                        results.pop(n, None)
                        continue
                    obs = average_detections(list(history[n]))
                    sol, last_good[n] = solve_with_zoom(obs, mat, cam.actual_size or (cams[n].width, cams[n].height),
                                                        last_good[n])
                    if sol is None:
                        results.pop(n, None)
                        continue
                    hint = placement_hint(sol.R, sol.t, target_eye(cams[n], mat, n), mat.aim_point,
                                          mat.tol_position_m, mat.tol_angle_deg)
                    warns = camera_warnings(sol.R, sol.t) + (["only one marker seen: pose is unreliable"]
                                                             if len(obs) < 2 else [])
                    results[n] = (sol, obs, hint, warns)
            layouts = {n: layout(r[0].R, r[0].t) for n, r in results.items()}
            strip = np.zeros((70, TILE[0] * 2, 3), np.uint8)
            for k, line in enumerate(comparison_lines(layouts)):
                draw_text(strip, line, (8, 20 + 20 * k), WHITE, 0.5)
            if now - last_print > 1.0:
                last_print = now
                for n, (sol, obs, hint, _) in sorted(results.items()):
                    c = cam_center(sol.R, sol.t)
                    print(f"{n}: markers {sorted(obs)} rms {sol.rms:.2f}px {sol.zoom_msg} at "
                          f"({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f}) -> {hint['msg']}")
            cv2.imshow("Mira extrinsics", np.vstack([np.hstack(tiles), strip]))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("t"):
                if set(results) != {"A", "B"}:
                    print("both cameras need a marker solution before locking targets")
                    continue
                eye_a, eye_b = lock_targets(*(cam_center(results[n][0].R, results[n][0].t) for n in ("A", "B")))
                cams["A"].target_eye, cams["B"].target_eye = eye_a, eye_b
                save_cameras(cams)
                print(f"locked targets: A {np.round(eye_a, 2).tolist()}  B {np.round(eye_b, 2).tolist()}")
            if key == ord("s"):
                unsure = [n for n in ("A", "B") if n not in results or results[n][0].guessed]
                if unsure:
                    print(f"camera(s) {unsure} need a measured zoom (markers at both ends of the mat) before saving")
                    continue
                for n, (sol, obs, _, _) in results.items():
                    cfg = cams[n]
                    cfg.K, cfg.dist, cfg.R, cfg.t = sol.K, np.zeros(5), sol.R, sol.t
                    cfg.extrinsics_rms, cfg.marker_obs, cfg.extrinsics_source = sol.rms, obs, "markers"
                save_cameras(cams)
                print("saved zoom and marker extrinsics. Next: python -m vision.calib_refine")
                return 0
    finally:
        for c in cameras.values():
            c.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Update `calib_refine`**

Apply to `vision/calib_refine.py`:

```diff
--- a/vision/calib_refine.py
+++ b/vision/calib_refine.py
@@ -4,7 +4,8 @@
     python -m vision.calib_refine --from data/recordings/refine-<time>.jsonl
 
 During capture: walk around the mat, squat, raise and spread your arms, turn.
-Stay visible to both cameras. Requires lens calibration and calib_extrinsics first.
+Stay visible to both cameras. Requires calib_extrinsics first. Refuses to start if a camera has
+moved since calib_extrinsics (--force overrides). Refines camera poses and zoom.
 """
 from __future__ import annotations
 
@@ -19,14 +20,32 @@
 from vision.capture import mono_to_unix
 from vision.config import load_cameras, load_mat, mat_raw, save_cameras
 from vision.draw import GREEN, YELLOW, draw_skeleton, draw_text, fit
+from vision.markers import MarkerDetector
+from vision.moved import MoveMonitor
 from vision.pipeline import Pipeline
 from vision.record import Recorder, default_path
 from vision.replay import iter_frames
 
 COUNTDOWN_S = 3.0
+MOVE_CHECK_S = 2.0
 
 
-def capture_live(cams, model: str, seconds: float):
+def moved_cameras(src, mat, seconds: float = MOVE_CHECK_S) -> list[str]:
+    """Watch the markers for a moment; names of cameras whose markers moved since calib_extrinsics."""
+    monitor = MoveMonitor(src.cams, MarkerDetector(mat), interval_s=0.25)
+    end = time.monotonic() + seconds
+    while time.monotonic() < end:
+        for n, v in src.latest_frames().items():
+            if v is not None:
+                monitor.update(n, v[0], time.monotonic())
+        time.sleep(0.02)
+    for n in sorted(src.cams):
+        if monitor.message(n):
+            print(monitor.message(n))
+    return [n for n in sorted(src.cams) if monitor.status(n) == "moved"]
+
+
+def capture_live(cams, mat, model: str, seconds: float, force: bool = False):
     from vision.live import LiveSource
 
     pairs = []
@@ -37,6 +56,10 @@
         if problems:
             print("cannot capture: " + "; ".join(problems))
             return None
+        moved = moved_cameras(src, mat)
+        if moved and not force:
+            print(f"camera(s) {moved} moved since calib_extrinsics: re-run it first (or --force)")
+            return None
         with Recorder(path, cams, mat_raw()) as rec:
             _capture(src, rec, pipeline, pairs, seconds)
     cv2.destroyAllWindows()
@@ -78,7 +101,8 @@
     p.add_argument("--from", dest="from_path", type=Path)
     p.add_argument("--model", default="full", choices=["full", "heavy"])
     p.add_argument("--marker-weight", type=float, default=5.0)
-    p.add_argument("--force", action="store_true", help="save even if the guards reject the result")
+    p.add_argument("--force", action="store_true",
+                   help="capture even if a camera moved; save even if the guards reject the result")
     args = p.parse_args()
 
     cams, mat = load_cameras(), load_mat()
@@ -86,7 +110,7 @@
         pairs = [(raw.get("A"), raw.get("B")) for _, raw, _ in iter_frames(args.from_path)]
     else:
         print(__doc__)
-        pairs = capture_live(cams, args.model, args.seconds)
+        pairs = capture_live(cams, mat, args.model, args.seconds, args.force)
         if pairs is None:
             return 1
     try:
@@ -99,7 +123,7 @@
         print("not saved (use --force to override)")
         return 1
     for n in ("A", "B"):
-        cams[n].R, cams[n].t = report.R[n], report.t[n]
+        cams[n].R, cams[n].t, cams[n].K = report.R[n], report.t[n], report.K[n]
         cams[n].extrinsics_source, cams[n].extrinsics_rms = "bundle", report.body_rms_after
     save_cameras(cams)
     print("saved refined extrinsics. Next: python -m vision.debug_view and python -m vision.checks")
```

- [ ] **Step 6: Run the whole suite and import every tool**

Run: `.venv/bin/python -m pytest -q && for m in calib_extrinsics calib_refine checks debug_view cams; do .venv/bin/python -c "import vision.$m" || echo FAIL $m; done`
Expected: PASS (98 passed), no `FAIL` lines.

- [ ] **Step 7: Commit**

```bash
git add vision/calib_extrinsics.py vision/debug_view.py vision/tests/test_debug_view.py vision/calib_refine.py
git commit -m "vision: placement overlay in calib_extrinsics; camera-moved alarm in the live tools"
```

---

### Task 8: README

**Files:**
- Modify: `vision/README.md`

**Interfaces:**
- Consumes: the commands and keys from Tasks 2–7.

- [ ] **Step 1: Update the workflow**

Apply to `vision/README.md`:

```diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -22,18 +22,34 @@
 
 ## Workflow
 
-1. `python -m vision.cams`: assign A (front-left) and B (front-right).
+No lens calibration: each camera's zoom comes from the floor markers. These webcams (~48° wide) need
+to be about 4.5 m from the mat centre to see a whole person.
+
+1. `python -m vision.cams`: assign A (front-left, near marker 0) and B (front-right, near marker 1).
 2. `python -m vision.debug_view`: check both feeds, skeletons, ≥ 20 fps each (2D only until calibrated).
-3. `python -m vision.calib_intrinsics --cam A` then `--cam B`: before mounting, point each webcam at the
-   on-screen checkerboard; target RMS < 0.5 px.
-4. Mount the cameras. `python -m vision.calib_extrinsics`: follow the hints until both say OK, press s.
-5. `python -m vision.calib_refine`: move around the mat for 20 s; saves only if the fit improves.
-6. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
+3. Mat empty: `python -m vision.calib_extrinsics`. Each camera needs to see markers at both ends of the
+   mat so its zoom can be measured.
+   - First time (or a new room): put the cameras roughly in place and press `t` to lock the current
+     spots as a mirror-symmetric target (saved in `config/cameras.yaml`).
+   - Follow the instructions: position first ("Move 20 cm back"), then aim ("Turn right 2°, tilt down
+     1°, rotate clockwise 1°"), until both say Position OK and Aim OK. The green dashed ghost shows
+     where the mat should appear; the red aim dot should sit on the crosshair. The strip at the bottom
+     compares A and B (distance, height, angle). Press `s` to save.
+4. `python -m vision.calib_refine`: move around the mat for 20 s; refines poses and zoom; saves only if
+   the fit improves.
+5. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
    left hand → it moves on the +x side (left in the top view, right in the front view).
-7. `python -m vision.checks`: stand still for 5 s; everything should PASS.
-8. Press r in the debug view to record; `python -m vision.replay FILE` or
+6. `python -m vision.checks --height-cm <your height>`: stand still for 5 s; everything should PASS.
+7. Press r in the debug view to record; `python -m vision.replay FILE` or
    `python -m vision.debug_view --replay FILE [--retriangulate]` to play back.
 
+Every session after: step 3 without `t`, then step 4.
+
+**Camera moved?** The live tools re-check the markers about once a second. If a camera has been
+bumped, the debug view shows "Camera A has moved (… px): re-run calib_extrinsics", `calib_refine`
+refuses to start, and `checks` fails "camera A not moved". Re-run steps 3–4. "Markers hidden" only
+means someone is standing over them.
+
 If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.
 
 ### Left/right flips
```

- [ ] **Step 2: Check no stale references remain**

Run: `grep -rn "calib_intrinsics\|intrinsics_rms\|checkerboard" vision --include='*.py' --include='*.md' | grep -v test_config.py`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add vision/README.md
git commit -m "vision: README for marker-only calibration, placement and the moved alarm"
```

---

