# Track only the person at the mat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (Tasks 1–6) Track exactly one person, the one on or within 1 m of the mat: crop pose detection to the mat area, find up to 3 people per camera, pick the consistent pair at the mat (sticky), and drop a camera that disagrees (hand over the lens) so the single-camera fallback takes over. (Task 7) Anchor planted feet so they don't jitter.

**Architecture:** `roi.py` computes each calibrated camera's pixel box around the mat; `pose.py` runs MediaPipe on that crop with `num_poses=3` and returns `CameraPeople` in full-frame pixels; `select.py` (`PersonSelector`) chooses the person; `Pipeline.process` runs selection when given multi-person input and is unchanged for single `PoseResult`s (old recordings, tests). Recordings gain a `people` field; replay re-runs selection. The debug view draws the cyan tracking area, grey ignored people and a dropped-camera message; the full picture is never cropped.

**Tech Stack:** Python 3.11, MediaPipe Tasks PoseLandmarker (VIDEO mode), OpenCV, NumPy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-vision-person-selection-design.md`

Every code block below was run against a scratch copy of the repo before this plan was written: the full suite passed (134 tests), and every diff applies to the current branch.

## Global Constraints

- Vicinity 1 m from the mat edge; stickiness: switch only when another qualifying person is ≥ 0.3 m closer to the mat centre for ≥ 0.5 s; "current" = candidate within 0.5 m of the last tracked position.
- Same person across cameras: hip/shoulder/ankle midpoints, median reprojection ≤ 25 px, hip midpoint required.
- Floor position from one camera: ankle (y = 0.08 m) and heel (y = 0.04 m) rays ∩ floor planes, median (x, z); no hip fallback.
- ROI: mat grown 1 m, floor to 2.1 m, 10% padding, clipped; None (full frame) if uncalibrated or the box is not wholly in front of the camera; minimum 64 px.
- `num_poses = 3` (`vision.pose.NUM_POSES`).
- Display is never cropped; tracking area drawn cyan `(255, 255, 0)`, 2 px, labelled "tracking area"; ignored people grey.
- `Pipeline.process` with single `PoseResult`s behaves exactly as before; old recordings replay unchanged.
- Run tests with `.venv/bin/python -m pytest` from the repo root.

- Foot anchoring (Task 7): planted = heel and toe ≤ 0.12 m and < 0.15 m/s over 0.2 s; release after 2 frames > 0.05 m away or lifted; offset fades over 0.1 s; on by default.

## Review Focus

1. Performance on the rig: 3 poses per camera with two cameras must keep ≥ 20 fps per camera (`checks` reports it); if not, lower `NUM_POSES` to 2.
2. ROI at the real camera spots (3.7–4.5 m, ~0.9 m high): with a 1 m margin the box may cover most of the frame or fall back to full frame; it must never cut off a person standing at the mat edge.
3. A real hand over one lens: MediaPipe may still report plausible-looking feet for the junk; the floor test must not place it at the mat more readily than the real person seen by the other camera.
4. Two people close together (< 0.5 m): identity can swap; acceptable but must not oscillate every frame.
5. Foot anchoring on the rig: a foot must release promptly on a real step (no visible "skating"), and anchoring must not hide a calibration error (e.g. a floor 5 cm off) from `checks`.
6. `calib_refine` now feeds the selected person (`out.raw`) to the bundle adjustment instead of raw pairs; with nobody at the mat it must still raise "not enough body observations" rather than crash.

---

### Task 1: Tracking area (`roi.py`)

**Files:**
- Create: `vision/roi.py`
- Create: `vision/tests/test_roi.py`

**Interfaces:**
- Produces: `mat_roi(cam, mat, margin_m=1.0, height_m=2.1, pad=0.10) -> (x0, y0, x1, y1) | None`; `crop(frame, roi) -> (sub, (x0, y0))` (identity for None); `uncrop(lm2d, offset) -> lm2d`; `MIN_ROI_PX = 64`.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_roi.py`:

```python
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import project
from vision.roi import crop, mat_roi, uncrop
from vision.tests.synth import make_cam

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cam(eye=(3.18, 0.9, 3.18)):
    cam = make_cam("A", eye, target=(0.0, 0.9, 0.0), K=K)
    cam.width, cam.height = 1280, 720
    return cam


def test_roi_covers_the_mat_area_and_is_inside_the_image():
    cam = _cam((4.5, 1.0, 4.5))
    x0, y0, x1, y1 = mat_roi(cam, MAT, margin_m=0.3)
    assert 0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 720
    feet = project(np.array([[x, 0.0, z] for x in (-0.3, 0.3) for z in (-0.86, 0.86)]), cam.K, cam.dist, cam.R, cam.t)
    head = project(np.array([[0.0, 1.9, 0.0]]), cam.K, cam.dist, cam.R, cam.t)
    for u, v in np.vstack([feet, head]):
        assert x0 <= u <= x1 and y0 <= v <= y1
    assert (x1 - x0) * (y1 - y0) < 1280 * 720  # it actually narrows the search


def test_roi_none_when_uncalibrated_or_box_behind_camera():
    assert mat_roi(CameraConfig(name="A", index=0), MAT) is None
    assert mat_roi(_cam((0.5, 1.0, 0.5)), MAT) is None  # camera inside the grown box


def test_crop_and_uncrop_round_trip():
    frame = np.arange(720 * 1280 * 3, dtype=np.uint32).reshape(720, 1280, 3)
    roi = (100, 50, 900, 650)
    sub, off = crop(frame, roi)
    assert sub.shape == (600, 800, 3) and off == (100, 50)
    np.testing.assert_array_equal(sub[0, 0], frame[50, 100])
    np.testing.assert_allclose(uncrop(np.array([[0.0, 0.0], [10.5, 3.0]]), off), [[100, 50], [110.5, 53]])
    full, off0 = crop(frame, None)
    assert full is frame and off0 == (0, 0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_roi.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'vision.roi'`.

- [ ] **Step 3: Implement**

Create `vision/roi.py`:

```python
"""The part of each camera's picture worth running pose on: the mat and the space around it.

Only pose detection is cropped; every view keeps showing the full picture with this area outlined.
"""
from __future__ import annotations

import math

import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import mat_to_cam, project

MIN_ROI_PX = 64


def mat_roi(cam: CameraConfig | None, mat: MatConfig, margin_m: float = 1.0, height_m: float = 2.1,
            pad: float = 0.10) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) full-frame pixels around the mat grown by margin_m, floor to height_m.

    None (= use the full frame) if the camera is uncalibrated or the box is not wholly in front of it.
    """
    if cam is None or not cam.calibrated:
        return None
    hw, hl = mat.width_m / 2 + margin_m, mat.length_m / 2 + margin_m
    base = np.array([[x, 0.0, z] for x in (-hw, hw) for z in (-hl, hl)])
    box = np.vstack([base, base + [0.0, height_m, 0.0]])
    if np.any(mat_to_cam(box, cam.R, cam.t)[:, 2] <= 0.05):
        return None
    uv = project(box, cam.K, cam.dist, cam.R, cam.t)
    (x0, y0), (x1, y1) = uv.min(axis=0), uv.max(axis=0)
    px, py = pad * (x1 - x0), pad * (y1 - y0)
    x0, y0 = max(0, math.floor(x0 - px)), max(0, math.floor(y0 - py))
    x1, y1 = min(cam.width, math.ceil(x1 + px)), min(cam.height, math.ceil(y1 + py))
    if x1 - x0 < MIN_ROI_PX or y1 - y0 < MIN_ROI_PX:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def crop(frame: np.ndarray, roi) -> tuple[np.ndarray, tuple[int, int]]:
    if roi is None:
        return frame, (0, 0)
    x0, y0, x1, y1 = roi
    return frame[y0:y1, x0:x1], (x0, y0)


def uncrop(lm2d: np.ndarray, offset) -> np.ndarray:
    return np.asarray(lm2d, float) + np.asarray(offset, float)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_roi.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/roi.py vision/tests/test_roi.py
git commit -m "vision: tracking area around the mat for pose detection"
```

---

### Task 2: Several people per camera, cropped (`CameraPeople`, `pose.py`)

**Files:**
- Modify: `vision/types.py`
- Modify: `vision/pose.py`
- Modify: `vision/tests/test_pose.py`

**Interfaces:**
- Consumes: `crop`, `uncrop` (Task 1).
- Produces: `CameraPeople(cam, t, people: list[PoseResult])` with `.detected`; `NUM_POSES = 3`; `PoseEstimator(kind, num_poses=NUM_POSES).detect(frame, t, cam, roi=None) -> list[PoseResult]`; `PoseWorker(camera, out, model, roi=None, num_poses=NUM_POSES)` puts `CameraPeople`; `.latest -> (frame, CameraPeople)`.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_pose.py`:

```diff
--- a/vision/tests/test_pose.py
+++ b/vision/tests/test_pose.py
@@ -14,7 +14,41 @@
     est = PoseEstimator("full")
     try:
         r = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")
-        r2 = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")  # same timestamp must not raise
+        r2 = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A", roi=(100, 50, 900, 650))  # same timestamp: no raise
     finally:
         est.close()
-    assert r.cam == "A" and not r.detected and not r2.detected
+    assert r == [] and r2 == []
+
+
+def test_people_are_mapped_from_the_crop_to_full_frame_pixels():
+    try:
+        model_path("full")
+    except FileNotFoundError:
+        pytest.skip("model not downloaded")
+    from types import SimpleNamespace
+
+    from vision.pose import PoseEstimator
+
+    def person(x, y):
+        return [SimpleNamespace(x=x, y=y, z=0.0, visibility=0.9)] * 33
+
+    fake = SimpleNamespace(pose_landmarks=[person(0.5, 0.25), person(0.1, 0.9)],
+                           pose_world_landmarks=[person(0.0, 0.0), person(0.0, 0.0)])
+    est = PoseEstimator("full")
+    try:
+        seen = {}
+
+        def detect_for_video(image, ts):
+            seen["shape"] = image.numpy_view().shape
+            return fake
+
+        est._landmarker.detect_for_video = detect_for_video
+        people = est.detect(np.zeros((720, 1280, 3), np.uint8), 2.0, "B", roi=(400, 100, 1200, 500))
+    finally:
+        est._landmarker.detect_for_video = lambda *a: None
+        est.close()
+    assert seen["shape"][:2] == (400, 800)  # only the crop was searched
+    assert len(people) == 2 and all(p.cam == "B" and p.t == 2.0 for p in people)
+    np.testing.assert_allclose(people[0].lm2d[0], [400 + 0.5 * 800, 100 + 0.25 * 400])
+    np.testing.assert_allclose(people[1].lm2d[0], [400 + 0.1 * 800, 100 + 0.9 * 400])
+    assert people[0].world.shape == (33, 3)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_pose.py -q`
Expected: 2 FAIL — `assert r == []` (detect still returns a single `PoseResult`) and `TypeError: ... unexpected keyword argument 'roi'`. (If the pose model is not downloaded both skip: run `.venv/bin/python -m vision.models.download` first.)

- [ ] **Step 3: Implement**

Apply to `vision/types.py`:

```diff
--- a/vision/types.py
+++ b/vision/types.py
@@ -128,3 +128,17 @@
             flags=d.get("flags") or [],
             tracking=d.get("tracking", "lost"),
         )
+
+
+@dataclass
+class CameraPeople:
+    """Everyone MediaPipe found in one camera frame (full-frame pixels). cam/t let the Pairer pair it."""
+
+    cam: str
+    t: float
+    people: list[PoseResult]
+
+    @property
+    def detected(self) -> bool:
+        return bool(self.people)
+
```

Apply to `vision/pose.py`:

```diff
--- a/vision/pose.py
+++ b/vision/pose.py
@@ -12,9 +12,11 @@
 from mediapipe.tasks import python as mp_python
 from mediapipe.tasks.python import vision as mp_vision
 
-from vision.types import PoseResult
+from vision.roi import crop, uncrop
+from vision.types import CameraPeople, PoseResult
 
 MODEL_DIR = Path(__file__).resolve().parent / "models"
+NUM_POSES = 3  # people per camera; the PersonSelector keeps the one at the mat
 
 
 def model_path(kind: str = "full") -> Path:
@@ -27,11 +29,11 @@
 class PoseEstimator:
     """VIDEO-mode landmarker for one camera (timestamps must increase)."""
 
-    def __init__(self, kind: str = "full"):
+    def __init__(self, kind: str = "full", num_poses: int = NUM_POSES):
         options = mp_vision.PoseLandmarkerOptions(
             base_options=mp_python.BaseOptions(model_asset_path=str(model_path(kind))),
             running_mode=mp_vision.RunningMode.VIDEO,
-            num_poses=1,
+            num_poses=num_poses,
             min_pose_detection_confidence=0.5,
             min_pose_presence_confidence=0.5,
             min_tracking_confidence=0.5,
@@ -39,38 +41,41 @@
         self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
         self._last_ms = -1
 
-    def detect(self, frame_bgr: np.ndarray, t_mono: float, cam: str) -> PoseResult:
+    def detect(self, frame_bgr: np.ndarray, t_mono: float, cam: str, roi=None) -> list[PoseResult]:
+        """Everyone found (up to num_poses), in full-frame pixels. Only `roi` of the frame is searched."""
         ts = max(int(t_mono * 1000), self._last_ms + 1)
         self._last_ms = ts
-        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
+        sub, offset = crop(frame_bgr, roi)
+        rgb = cv2.cvtColor(np.ascontiguousarray(sub), cv2.COLOR_BGR2RGB)
         res = self._landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
-        if not res.pose_landmarks:
-            return PoseResult(cam=cam, t=t_mono)
-        h, w = frame_bgr.shape[:2]
-        lms = res.pose_landmarks[0]
-        lm2d = np.array([[p.x * w, p.y * h] for p in lms])
-        vis = np.array([p.visibility for p in lms], dtype=float)
-        world = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks[0]]) if res.pose_world_landmarks else None
-        return PoseResult(cam=cam, t=t_mono, lm2d=lm2d, vis=vis, world=world)
+        h, w = sub.shape[:2]
+        people = []
+        for k, lms in enumerate(res.pose_landmarks or []):
+            lm2d = uncrop(np.array([[p.x * w, p.y * h] for p in lms]), offset)
+            vis = np.array([p.visibility for p in lms], dtype=float)
+            wl = res.pose_world_landmarks
+            world = np.array([[p.x, p.y, p.z] for p in wl[k]]) if wl and k < len(wl) else None
+            people.append(PoseResult(cam=cam, t=t_mono, lm2d=lm2d, vis=vis, world=world))
+        return people
 
     def close(self) -> None:
         self._landmarker.close()
 
 
 class PoseWorker(threading.Thread):
-    """Runs pose on each new frame from one Camera and puts PoseResults on a shared queue."""
+    """Runs pose on each new frame from one Camera and puts CameraPeople on a shared queue."""
 
-    def __init__(self, camera, out: queue.Queue, model: str = "full"):
+    def __init__(self, camera, out: queue.Queue, model: str = "full", roi=None, num_poses: int = NUM_POSES):
         super().__init__(daemon=True, name=f"pose-{camera.cam_name}")
-        self.camera, self.out, self.model = camera, out, model
+        self.camera, self.out, self.model, self.roi, self.num_poses = camera, out, model, roi, num_poses
         self._halt = threading.Event()  # not _stop: threading.Thread uses that name internally
-        self.latest: tuple[np.ndarray, PoseResult] | None = None
+        self.latest: tuple[np.ndarray, CameraPeople] | None = None
         self.ms = 0.0
         self.error: str | None = None
 
     def run(self) -> None:
         try:
-            est = PoseEstimator(self.model)
+            est = PoseEstimator(self.model, self.num_poses)
         except Exception as e:  # surfaced in the viewer HUD
             self.error = f"pose {self.camera.cam_name}: {e}"
             return
@@ -83,7 +88,7 @@
                     continue
                 last_seq, t, frame = item
                 t0 = time.perf_counter()
-                r = est.detect(frame, t, self.camera.cam_name)
+                r = CameraPeople(self.camera.cam_name, t, est.detect(frame, t, self.camera.cam_name, self.roi))
                 dt = (time.perf_counter() - t0) * 1000
                 self.ms = dt if self.ms == 0 else 0.9 * self.ms + 0.1 * dt
                 self.latest = (frame, r)
```

`vision/live.py`, `debug_view.py` and `calib_refine.py` still expect one `PoseResult` per camera from the worker until Task 5; the live tools are not run between Task 2 and Task 5.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_pose.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/types.py vision/pose.py vision/tests/test_pose.py
git commit -m "vision: up to 3 people per camera, pose searches only the tracking area"
```

---

### Task 3: Person selection (`select.py`)

**Files:**
- Create: `vision/select.py`
- Create: `vision/tests/test_select.py`

**Interfaces:**
- Consumes: `triangulate_points` (existing), `cam_center` (existing).
- Produces: `Selection(a, b, ignored: dict[str, list[PoseResult]], dropped: str | None, position: (x, z) | None)`; `PersonSelector(cams, mat, vicinity_m=1.0, pair_px=25.0, switch_m=0.3, switch_s=0.5).select(t, a_people, b_people) -> Selection`; helpers `mat_distance(mat, xz)`, `floor_position(person, cam)`, `pair_position(pa, pb, ca, cb) -> (err_px, hip_xz) | None`.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_select.py`:

```python
import numpy as np

from vision.config import MatConfig
from vision.select import PersonSelector, floor_position, mat_distance, pair_position
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cams():
    return {"A": make_cam("A", (3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K),
            "B": make_cam("B", (-3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K)}


def _person(cams, x, z, t=0.0):
    X = standing_skeleton() + [x, 0.0, z]
    return pose_from_points(cams["A"], X, t=t), pose_from_points(cams["B"], X, t=t)


def test_mat_distance():
    assert mat_distance(MAT, (0.0, 0.0)) == 0.0
    assert abs(mat_distance(MAT, (MAT.width_m / 2 + 1.0, 0.0)) - 1.0) < 1e-12


def test_floor_position_from_one_camera():
    cams = _cams()
    a, _ = _person(cams, 0.4, -0.3)
    np.testing.assert_allclose(floor_position(a, cams["A"]), [0.4, -0.3], atol=0.08)


def test_pair_position_same_person_agrees_and_different_people_dont():
    cams = _cams()
    a1, b1 = _person(cams, 0.0, 0.0)
    _, b2 = _person(cams, 2.5, 1.5)
    err, xz = pair_position(a1, b1, cams["A"], cams["B"])
    assert err < 1.0 and np.linalg.norm(xz - [0.0, 0.0]) < 0.05
    bad = pair_position(a1, b2, cams["A"], cams["B"])
    assert bad is None or bad[0] > 25


def test_bystander_away_from_the_mat_is_ignored():
    cams = _cams()
    on_a, on_b = _person(cams, 0.0, 0.2)
    by_a, by_b = _person(cams, 2.6, 0.0)  # 2.3 m to the side of the mat
    sel = PersonSelector(cams, MAT).select(0.0, [by_a, on_a], [on_b, by_b])
    assert sel.a is on_a and sel.b is on_b and sel.dropped is None
    assert sel.ignored == {"A": [by_a], "B": [by_b]}
    only = PersonSelector(cams, MAT).select(0.0, [by_a], [by_b])
    assert only.a is None and only.b is None


def test_person_next_to_the_mat_is_tracked():
    cams = _cams()
    a, b = _person(cams, MAT.width_m / 2 + 0.7, 0.0)
    sel = PersonSelector(cams, MAT).select(0.0, [a], [b])
    assert sel.a is a and sel.b is b


def test_two_on_the_mat_closest_to_centre_then_sticky_then_switch():
    cams = _cams()
    near_a, near_b = _person(cams, 0.0, 0.1)
    edge_a, edge_b = _person(cams, 0.15, -0.75)
    assert PersonSelector(cams, MAT).select(0.0, [edge_a, near_a], [near_b, edge_b]).a is near_a

    sel = PersonSelector(cams, MAT)
    tr_a, tr_b = _person(cams, 0.0, 0.6)  # tracked person, towards the Mira end
    assert sel.select(0.0, [tr_a], [tr_b]).a is tr_a
    ch_a, ch_b = _person(cams, 0.0, -0.05)  # someone steps in at the centre: 0.55 m closer
    picks = [sel.select(0.1 * (k + 1), [ch_a, tr_a], [tr_b, ch_b]).a for k in range(3)]
    assert all(p is tr_a for p in picks)  # not yet: under 0.5 s
    picks = [sel.select(0.4 + 0.1 * k, [ch_a, tr_a], [tr_b, ch_b]).a for k in range(4)]
    assert picks[-1] is ch_a  # closer for over 0.5 s: switched


def test_small_wobble_does_not_switch():
    cams = _cams()
    sel = PersonSelector(cams, MAT)
    a1, b1 = _person(cams, 0.0, 0.3)
    a2, b2 = _person(cams, 0.0, 0.1)  # only 0.2 m closer to the centre than the tracked person
    sel.select(0.0, [a1], [b1])
    assert all(sel.select(0.1 * k, [a2, a1], [b1, b2]).a is a1 for k in range(1, 20))


def test_hand_over_one_lens_drops_that_camera():
    cams = _cams()
    a, b = _person(cams, 0.0, 0.0)
    junk = pose_from_points(cams["A"], standing_skeleton())  # reuse shape, then make it a huge blob
    junk.lm2d = np.random.default_rng(0).uniform([300, 0], [1000, 300], (33, 2))  # big shape, high up
    sel = PersonSelector(cams, MAT).select(0.0, [junk], [b])
    assert sel.a is None and sel.b is b and sel.dropped == "A"
    assert sel.ignored["A"] == [junk]


def test_uncalibrated_falls_back_to_first_person():
    cams = _cams()
    a, b = _person(cams, 0.0, 0.0)
    cams["B"].R = None
    sel = PersonSelector(cams, MAT).select(0.0, [a], [b])
    assert sel.a is a and sel.b is b
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_select.py -q`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'vision.select'`.

- [ ] **Step 3: Implement**

Create `vision/select.py`:

```python
"""Pick the one person to track: the person on or next to the mat.

Each camera may report several people. A person in A and a person in B are the same person when
their hip, shoulder and ankle midpoints triangulate consistently (midpoints don't care about
MediaPipe's left/right flips). Of the consistent pairs standing within `vicinity_m` of the mat, the
one closest to the mat centre wins, with stickiness so tracking doesn't flicker between people. If
no pair agrees (a hand over one lens, the wrong person in one view), each camera's people are placed
on the floor through their feet, and the camera that puts someone at the mat is kept; the other is
dropped for that frame and the pipeline's single-camera fallback takes over.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import cam_center
from vision.triangulate import triangulate_points
from vision.types import PoseResult

FOOT_HEIGHTS = {27: 0.08, 28: 0.08, 29: 0.04, 30: 0.04}  # ankles, heels: metres above the mat surface
MIDPOINTS = ((23, 24), (11, 12), (27, 28))  # hips first: its triangulation is the person's position
MIN_VIS = 0.5
STICK_M = 0.5  # the tracked person is the candidate within this distance of where they were


@dataclass
class Selection:
    a: PoseResult | None
    b: PoseResult | None
    ignored: dict[str, list[PoseResult]] = field(default_factory=dict)
    dropped: str | None = None  # camera ignored this frame because it disagreed
    position: np.ndarray | None = None  # (x, z) of the tracked person on the floor


def mat_distance(mat: MatConfig, xz) -> float:
    """0 on the mat, else the floor distance to its edge."""
    dx = max(abs(float(xz[0])) - mat.width_m / 2, 0.0)
    dz = max(abs(float(xz[1])) - mat.length_m / 2, 0.0)
    return float(np.hypot(dx, dz))


def floor_position(person: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """(x, z) where the person stands, from one camera: rays through the visible feet meet the floor."""
    if not person.detected:
        return None
    idx = [j for j in FOOT_HEIGHTS if person.vis[j] >= MIN_VIS]
    if not idx:
        return None
    uv = person.lm2d[idx].reshape(-1, 1, 2).astype(np.float64)
    n = cv2.undistortPoints(uv, cam.K, cam.dist).reshape(-1, 2)
    dirs = np.hstack([n, np.ones((len(n), 1))]) @ np.asarray(cam.R)  # camera rays in the mat frame
    c = cam_center(cam.R, cam.t)
    heights = np.array([FOOT_HEIGHTS[j] for j in idx])
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (heights - c[1]) / dirs[:, 1]
    hits = [c + si * d for si, d in zip(s, dirs) if np.isfinite(si) and si > 0]
    if not hits:
        return None
    return np.median(np.array(hits)[:, [0, 2]], axis=0)


def _midpoints(p: PoseResult) -> dict[int, np.ndarray]:
    return {k: (p.lm2d[i] + p.lm2d[j]) / 2 for k, (i, j) in enumerate(MIDPOINTS)
            if p.vis[i] >= MIN_VIS and p.vis[j] >= MIN_VIS}


def pair_position(pa: PoseResult, pb: PoseResult, ca: CameraConfig, cb: CameraConfig) -> tuple[float, np.ndarray] | None:
    """(median midpoint reprojection error px, hip (x, z)) for one person in A paired with one in B."""
    ma, mb = _midpoints(pa), _midpoints(pb)
    keys = sorted(set(ma) & set(mb))
    if 0 not in keys:
        return None
    X, err, _ = triangulate_points(np.array([ma[k] for k in keys]), np.array([mb[k] for k in keys]), ca, cb)
    if not np.isfinite(err).all() or not np.isfinite(X[0]).all():
        return None
    return float(np.median(err)), X[0][[0, 2]]


class PersonSelector:
    def __init__(self, cams: dict[str, CameraConfig], mat: MatConfig, vicinity_m: float = 1.0,
                 pair_px: float = 25.0, switch_m: float = 0.3, switch_s: float = 0.5):
        self.cams, self.mat = cams, mat
        self.vicinity_m, self.pair_px, self.switch_m, self.switch_s = vicinity_m, pair_px, switch_m, switch_s
        self._pos: np.ndarray | None = None
        self._challenge_since: float | None = None

    def _choose(self, t: float, cands: list[tuple[np.ndarray, object]]):
        """cands: (xz, key). Closest to the mat centre, but stick with the current person unless another
        is switch_m closer for switch_s."""
        centre = [float(np.linalg.norm(xz)) for xz, _ in cands]
        best = int(np.argmin(centre))
        current = None
        if self._pos is not None:
            d = [float(np.linalg.norm(xz - self._pos)) for xz, _ in cands]
            k = int(np.argmin(d))
            current = k if d[k] <= STICK_M else None
        if current is None or best == current:
            pick = best if current is None else current
            self._challenge_since = None
        elif centre[best] <= centre[current] - self.switch_m:
            if self._challenge_since is None:
                self._challenge_since = t
            if t - self._challenge_since >= self.switch_s:
                pick, self._challenge_since = best, None
            else:
                pick = current
        else:
            pick, self._challenge_since = current, None
        self._pos = np.asarray(cands[pick][0], float)
        return cands[pick]

    def select(self, t: float, a_people: list[PoseResult], b_people: list[PoseResult]) -> Selection:
        a_people = [p for p in a_people if p is not None and p.detected]
        b_people = [p for p in b_people if p is not None and p.detected]
        ca, cb = self.cams.get("A"), self.cams.get("B")
        if not (ca is not None and cb is not None and ca.calibrated and cb.calibrated):
            a = a_people[0] if a_people else None
            b = b_people[0] if b_people else None
            return Selection(a, b, {"A": a_people[1:], "B": b_people[1:]})

        pairs = []
        for i, pa in enumerate(a_people):
            for j, pb in enumerate(b_people):
                r = pair_position(pa, pb, ca, cb)
                if r is not None and r[0] <= self.pair_px and mat_distance(self.mat, r[1]) <= self.vicinity_m:
                    pairs.append((r[1], (i, j)))
        dropped = None
        if pairs:
            xz, (i, j) = self._choose(t, pairs)
            a, b = a_people[i], b_people[j]
        else:
            singles = []
            for name, people, cam in (("A", a_people, ca), ("B", b_people, cb)):
                for k, p in enumerate(people):
                    xz = floor_position(p, cam)
                    if xz is not None and mat_distance(self.mat, xz) <= self.vicinity_m:
                        singles.append((xz, (name, k)))
            if not singles:
                self._pos, self._challenge_since = None, None
                return Selection(None, None, {"A": a_people, "B": b_people})
            xz, (name, k) = self._choose(t, singles)
            a = a_people[k] if name == "A" else None
            b = b_people[k] if name == "B" else None
            other = b_people if name == "A" else a_people
            dropped = ("B" if name == "A" else "A") if other else None
        ignored = {"A": [p for p in a_people if p is not a], "B": [p for p in b_people if p is not b]}
        return Selection(a, b, ignored, dropped, np.asarray(xz, float))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest vision/tests/test_select.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/select.py vision/tests/test_select.py
git commit -m "vision: pick the person at the mat; drop a camera that disagrees"
```

---

### Task 4: Pipeline, recording and replay

**Files:**
- Modify: `vision/pipeline.py`
- Modify: `vision/record.py`
- Modify: `vision/replay.py`
- Create: `vision/tests/test_people_pipeline.py`

**Interfaces:**
- Consumes: `PersonSelector` (Task 3), `CameraPeople` (Task 2).
- Produces: `Pipeline(cams, smoothing=True, min_vis=0.5, max_reproj_px=15.0, mat=None)`; `process(t, a, b, skew_s=None)` accepts `PoseResult | CameraPeople | list[PoseResult] | None` per camera; `PipelineOutput.people`, `.ignored`, `.dropped`; recording frame lines gain `people`; `replay.iter_records(path)` yields `(frame, raw, skew, people|None)`; `iter_frames` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_people_pipeline.py`:

```python
import numpy as np

from vision.config import MatConfig
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import iter_frames, iter_records, replay_outputs
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton
from vision.types import CameraPeople

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cams():
    return {"A": make_cam("A", (3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K),
            "B": make_cam("B", (-3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K)}


def _people(cams, t, positions):
    out = {"A": [], "B": []}
    for x, z in positions:
        X = standing_skeleton() + [x, 0.0, z]
        out["A"].append(pose_from_points(cams["A"], X, t=t))
        out["B"].append(pose_from_points(cams["B"], X, t=t))
    return out


def test_pipeline_tracks_the_person_at_the_mat_not_the_bystander():
    cams = _cams()
    pipe = Pipeline(cams, mat=MAT)
    p = _people(cams, 0.0, [(2.6, 0.0), (0.0, 0.2)])
    out = pipe.process(1.0, CameraPeople("A", 0.0, p["A"]), CameraPeople("B", 0.0, list(reversed(p["B"]))))
    assert out.frame.tracking == "3d"
    hip = (out.frame.joints[23] + out.frame.joints[24]) / 2
    assert np.linalg.norm(hip[[0, 2]] - [0.0, 0.2]) < 0.05
    assert out.raw["A"] is p["A"][1] and out.ignored["A"] == [p["A"][0]] and out.dropped is None
    assert len(out.people["A"]) == 2


def test_pipeline_single_pose_input_unchanged():
    cams = _cams()
    p = _people(cams, 0.0, [(0.0, 0.0)])
    out = Pipeline(cams, mat=MAT).process(1.0, p["A"][0], p["B"][0])
    assert out.frame.tracking == "3d" and out.people is None and out.ignored is None


def test_recording_keeps_everyone_and_replay_reselects(tmp_path):
    cams = _cams()
    path = tmp_path / "multi.jsonl"
    pipe = Pipeline(cams, mat=MAT)
    with Recorder(path, cams, {}) as rec:
        for k in range(5):
            p = _people(cams, k / 30, [(2.6, 0.0), (0.0, 0.2)])
            rec.write(pipe.process(1000 + k / 30, p["A"], p["B"], 0.0))
    _, raw, _, people = next(iter_records(path))
    assert len(people["A"]) == 2 and raw["A"] is not None
    assert len(list(iter_frames(path))) == 5
    outs = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False))
    assert all(o.ignored is not None and len(o.ignored["A"]) == 1 for o in outs)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_people_pipeline.py -q`
Expected: FAIL at collection — `ImportError: cannot import name 'iter_records' from 'vision.replay'`.

- [ ] **Step 3: Implement**

Apply to `vision/pipeline.py`:

```diff
--- a/vision/pipeline.py
+++ b/vision/pipeline.py
@@ -6,11 +6,12 @@
 import numpy as np
 
 from vision.cleanup import Cleaner
-from vision.config import CameraConfig
+from vision.config import CameraConfig, MatConfig
 from vision.filters import OneEuroFilter
 from vision.frames import world_to_mat
 from vision.triangulate import Triangulation, triangulate_pair
-from vision.types import LEFT_HIP, RIGHT_HIP, FrameResult, PoseResult, swap_left_right
+from vision.select import PersonSelector
+from vision.types import LEFT_HIP, RIGHT_HIP, CameraPeople, FrameResult, PoseResult, swap_left_right
 
 MIN_3D_JOINTS = 8  # below this the calibration or detections disagree; use one camera instead
 FLIP_MARGIN_M = 0.05  # a relabelling must beat the as-detected labels by this much to be used
@@ -24,6 +25,9 @@
     tri: Triangulation | None = None
     skew_s: float | None = None
     swapped: str | None = None  # "A", "B" or "AB": cameras whose left/right labels were flipped back
+    people: dict[str, list[PoseResult]] | None = None  # everyone found per camera (multi-person input)
+    ignored: dict[str, list[PoseResult]] | None = None  # people not tracked, per camera
+    dropped: str | None = None  # camera ignored this frame because it disagreed with the other
 
 
 def _flipped(p: PoseResult) -> PoseResult:
@@ -64,16 +68,37 @@
     return cost
 
 
+def _as_people(x) -> list[PoseResult] | None:
+    """A camera's input as a list of people, or None for a plain single PoseResult/None (legacy)."""
+    if isinstance(x, CameraPeople):
+        return list(x.people)
+    if isinstance(x, list):
+        return x
+    return None
+
+
 class Pipeline:
     def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
-                 min_vis: float = 0.5, max_reproj_px: float = 15.0):
+                 min_vis: float = 0.5, max_reproj_px: float = 15.0, mat: MatConfig | None = None):
         self.cams = cams
+        self._selector = PersonSelector(cams, mat or MatConfig())
         self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
         self._cleaner = Cleaner(cams)
         self._filter = OneEuroFilter() if smoothing else None
 
-    def process(self, t: float, a: PoseResult | None, b: PoseResult | None, skew_s: float | None = None) -> PipelineOutput:
-        """t is unix seconds (the FrameResult clock)."""
+    def process(self, t: float, a, b, skew_s: float | None = None) -> PipelineOutput:
+        """t is unix seconds (the FrameResult clock).
+
+        a, b: per camera a PoseResult (one person, as in old recordings), a CameraPeople or list of
+        PoseResults (everyone found; the person at the mat is selected first), or None.
+        """
+        pa, pb = _as_people(a), _as_people(b)
+        people = ignored = dropped = None
+        if pa is not None or pb is not None:
+            pa = pa if pa is not None else ([a] if isinstance(a, PoseResult) else [])
+            pb = pb if pb is not None else ([b] if isinstance(b, PoseResult) else [])
+            sel = self._selector.select(t, pa, pb)
+            people, ignored, dropped, a, b = {"A": pa, "B": pb}, sel.ignored, sel.dropped, sel.a, sel.b
         raw = {"A": a, "B": b}
         tri, swapped = None, None
         ca, cb = self.cams.get("A"), self.cams.get("B")
@@ -95,4 +120,5 @@
         if self._filter is not None:
             joints = self._filter(joints, t)
         frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
-        return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped)
+        return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped,
+                              people=people, ignored=ignored, dropped=dropped)
```

Apply to `vision/record.py`:

```diff
--- a/vision/record.py
+++ b/vision/record.py
@@ -31,7 +31,10 @@
 
     def write(self, out: PipelineOutput) -> None:
         raw = {k: (v.to_dict() if v is not None else None) for k, v in out.raw.items()}
-        self._write({"type": "frame", **out.frame.to_dict(), "raw": raw, "skew_s": out.skew_s})
+        line = {"type": "frame", **out.frame.to_dict(), "raw": raw, "skew_s": out.skew_s}
+        if out.people is not None:  # everyone found, so selection can be re-run on replay
+            line["people"] = {k: [p.to_dict() for p in v] for k, v in out.people.items()}
+        self._write(line)
         self.count += 1
 
     def _write(self, obj: dict) -> None:
```

Apply to `vision/replay.py`:

```diff
--- a/vision/replay.py
+++ b/vision/replay.py
@@ -12,7 +12,7 @@
 
 import numpy as np
 
-from vision.config import CameraConfig, load_cameras
+from vision.config import CameraConfig, load_cameras, load_mat
 from vision.pipeline import Pipeline, PipelineOutput
 from vision.types import FrameResult, PoseResult
 
@@ -29,25 +29,35 @@
     return {n: CameraConfig.from_dict(n, d) for n, d in (header.get("cameras") or {}).items()}
 
 
-def iter_frames(path) -> Iterator[tuple[FrameResult, dict[str, PoseResult | None], float | None]]:
+def iter_records(path) -> Iterator[tuple[FrameResult, dict[str, PoseResult | None], float | None,
+                                         dict[str, list[PoseResult]] | None]]:
+    """(frame, selected person per camera, skew, everyone found per camera or None for old recordings)."""
     with open(path) as f:
         for line in f:
             d = json.loads(line)
             if d.get("type") != "frame":
                 continue
             raw = {k: (PoseResult.from_dict(v) if v else None) for k, v in (d.pop("raw") or {}).items()}
+            people = d.pop("people", None)
+            if people is not None:
+                people = {k: [PoseResult.from_dict(p) for p in v] for k, v in people.items()}
             skew = d.pop("skew_s", None)
             d.pop("type")
-            yield FrameResult.from_dict(d), raw, skew
+            yield FrameResult.from_dict(d), raw, skew, people
 
 
+def iter_frames(path) -> Iterator[tuple[FrameResult, dict[str, PoseResult | None], float | None]]:
+    for frame, raw, skew, _ in iter_records(path):
+        yield frame, raw, skew
+
+
 def replay_outputs(path, retriangulate: bool = False, cams: dict[str, CameraConfig] | None = None,
                    realtime: bool = True, speed: float = 1.0, smoothing: bool = True) -> Iterator[PipelineOutput]:
     pipeline = None
     if retriangulate:
-        pipeline = Pipeline(cams if cams is not None else load_cameras(), smoothing=smoothing)
+        pipeline = Pipeline(cams if cams is not None else load_cameras(), smoothing=smoothing, mat=load_mat())
     start_t = start_wall = None
-    for frame, raw, skew in iter_frames(path):
+    for frame, raw, skew, people in iter_records(path):
         if realtime:
             if start_t is None:
                 start_t, start_wall = frame.t, time.monotonic()
@@ -55,7 +65,9 @@
                 delay = (frame.t - start_t) / speed - (time.monotonic() - start_wall)
                 if delay > 0:
                     time.sleep(delay)
-        if pipeline is not None:
+        if pipeline is not None and people is not None:  # re-run person selection too
+            yield pipeline.process(frame.t, people.get("A", []), people.get("B", []), skew)
+        elif pipeline is not None:
             yield pipeline.process(frame.t, raw.get("A"), raw.get("B"), skew)
         else:
             yield PipelineOutput(frame=frame, raw=raw, tri=None, skew_s=skew)
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (133 passed; the debug view test arrives in Task 5).

- [ ] **Step 5: Commit**

```bash
git add vision/pipeline.py vision/record.py vision/replay.py vision/tests/test_people_pipeline.py
git commit -m "vision: person selection in the pipeline; recordings keep everyone found"
```

---

### Task 5: Live tools: tracking area, ignored people, dropped camera

**Files:**
- Modify: `vision/live.py`
- Modify: `vision/debug_view.py`
- Modify: `vision/tests/test_debug_view.py`
- Modify: `vision/calib_refine.py`
- Modify: `vision/checks.py`

**Interfaces:**
- Consumes: `mat_roi` (Task 1), `NUM_POSES`/`PoseWorker` (Task 2), `Pipeline(..., mat=)` and `PipelineOutput.ignored/dropped/people` (Task 4).
- Produces: `LiveSource(cams, model='full', mat=None, num_poses=NUM_POSES)` with `.rois` and `stats()[n]['roi']`; `render_camera(..., ignored=None, roi=None)`; debug view HUD lines "N people seen, tracking 1" and "ignoring camera X (disagrees)".

- [ ] **Step 1: Write the failing test**

Apply to `vision/tests/test_debug_view.py`:

```diff
--- a/vision/tests/test_debug_view.py
+++ b/vision/tests/test_debug_view.py
@@ -39,3 +39,20 @@
                          alarms={"A": "Camera A has moved (6 px): re-run calib_extrinsics", "B": None})
     assert (alarm[:360, :640] != plain[:360, :640]).any()
     np.testing.assert_array_equal(alarm[:360, 640:], plain[:360, 640:])
+
+
+def test_compose_view_draws_tracking_area_and_ignored_people():
+    from vision.types import CameraPeople
+
+    cams, X = target_cams(), standing_skeleton()
+    on = (pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
+    far = X + [2.6, 0.0, 0.0]
+    by = (pose_from_points(cams["A"], far), pose_from_points(cams["B"], far))
+    out = Pipeline(cams).process(1.0, CameraPeople("A", 1.0, [by[0], on[0]]), CameraPeople("B", 1.0, [on[1], by[1]]))
+    assert out.ignored["A"] == [by[0]]
+    stats = {n: {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": None, "roi": (200, 100, 1000, 700)}
+             for n in ("A", "B")}
+    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, out, stats, None, None)
+    plain = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, None, None, None, None)
+    assert (img[:360, :640] != plain[:360, :640]).any()
+    assert (img[:360, :640] == (255, 255, 0)).all(axis=2).any()  # the cyan tracking-area outline
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest vision/tests/test_debug_view.py -q`
Expected: FAIL — the cyan tracking-area assertion (`(img[:360, :640] == (255, 255, 0)).all(axis=2).any()`) is False.

- [ ] **Step 3: Implement**

Apply to `vision/live.py`:

```diff
--- a/vision/live.py
+++ b/vision/live.py
@@ -7,21 +7,26 @@
 import numpy as np
 
 from vision.capture import Camera, camera_problem, resolution_warning
-from vision.config import CameraConfig
+from vision.config import CameraConfig, MatConfig
 from vision.pairing import Pair, Pairer, SkewStats
-from vision.pose import PoseWorker, model_path
-from vision.types import PoseResult
+from vision.pose import NUM_POSES, PoseWorker, model_path
+from vision.roi import mat_roi
+from vision.types import CameraPeople
 
 
 class LiveSource:
-    def __init__(self, cams: dict[str, CameraConfig], model: str = "full"):
+    def __init__(self, cams: dict[str, CameraConfig], model: str = "full", mat: MatConfig | None = None,
+                 num_poses: int = NUM_POSES):
         if not cams:
             raise SystemExit("No cameras configured: run python -m vision.cams first")
         model_path(model)  # fail fast if the model is missing
         self.cams = cams
         self._queue: queue.Queue = queue.Queue()
         self.cameras = {n: Camera(n, c.index, c.width, c.height) for n, c in cams.items()}
-        self.workers = {n: PoseWorker(cam, self._queue, model) for n, cam in self.cameras.items()}
+        # pose only searches the area around the mat (full frame when uncalibrated or no mat given)
+        self.rois = {n: (mat_roi(cams[n], mat) if mat is not None else None) for n in cams}
+        self.workers = {n: PoseWorker(cam, self._queue, model, self.rois[n], num_poses)
+                        for n, cam in self.cameras.items()}
         names = sorted(cams)
         self.pairer = Pairer(anchor=names[0], other=names[1] if len(names) > 1 else None)
         self.skew = SkewStats()
@@ -72,7 +77,7 @@
             except queue.Empty:
                 return out
 
-    def latest_frames(self) -> dict[str, tuple[np.ndarray, PoseResult] | None]:
+    def latest_frames(self) -> dict[str, tuple[np.ndarray, CameraPeople] | None]:
         return {n: w.latest for n, w in self.workers.items()}
 
     def stats(self) -> dict[str, dict]:
@@ -82,6 +87,7 @@
                 "pose_ms": self.workers[n].ms,
                 "error": cam.error or self.workers[n].error,
                 "warning": resolution_warning(self.cams[n], cam.actual_size),
+                "roi": self.rois.get(n),
             }
             for n, cam in self.cameras.items()
         }
```

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -60,8 +60,13 @@
     return np.full((PANEL_H, PANEL_W, 3), BG, np.uint8)
 
 
+ROI_COLOR = (255, 255, 0)  # cyan: the area pose detection pays attention to
+IGNORED_COLOR = (130, 130, 130)
+
+
 def render_camera(image, pose: PoseResult | None, frame: FrameResult | None, cam: CameraConfig | None,
-                  detector: MarkerDetector | None = None) -> np.ndarray:
+                  detector: MarkerDetector | None = None, ignored: list[PoseResult] | None = None,
+                  roi=None) -> np.ndarray:
     if image is not None:
         h, w = image.shape[:2]
         panel = fit(image, (PANEL_W, PANEL_H))
@@ -75,6 +80,15 @@
             poly = (c * scale).astype(np.int32)
             cv2.polylines(panel, [poly], True, YELLOW, 2)
             draw_text(panel, str(i), pt(poly[0]), YELLOW)
+    if roi is not None:
+        x0, y0, x1, y1 = roi
+        cv2.rectangle(panel, pt(np.array([x0, y0]) * scale), pt(np.array([x1, y1]) * scale), ROI_COLOR, 2)
+        draw_text(panel, "tracking area", pt(np.array([x0, y1]) * scale + [4, -6]), ROI_COLOR, 0.45)
+    for other in ignored or []:
+        if other.detected:
+            draw_skeleton(panel, other.lm2d * scale, ok=other.vis >= 0.5, color=IGNORED_COLOR)
+            top = other.lm2d[np.argmin(other.lm2d[:, 1])] * scale
+            draw_text(panel, "ignored", pt(top + [-25, -8]), IGNORED_COLOR, 0.45)
     if pose is not None and pose.detected:
         draw_skeleton(panel, pose.lm2d * scale, ok=pose.vis >= 0.5, color=GREEN)
     if frame is not None and cam is not None and cam.calibrated:
@@ -138,7 +152,9 @@
     panels = []
     for name in ("A", "B"):
         cam = cams.get(name)
-        panel = render_camera(images.get(name), poses.get(name), frame, cam, detector)
+        panel = render_camera(images.get(name), poses.get(name), frame, cam, detector,
+                              (out.ignored or {}).get(name) if out is not None else None,
+                              ((stats or {}).get(name) or {}).get("roi"))
         s = (stats or {}).get(name)
         draw_text(panel, f"{name}  {s['fps']:.0f} fps  pose {s['pose_ms']:.0f} ms" if s else name, (8, 20))
         y = 40
@@ -159,6 +175,11 @@
     if out is not None and out.tri is not None and out.tri.ok.any():
         err = float(np.nanmean(out.tri.reproj_err[out.tri.ok]))
         hud.append(f"3D reprojection {err:.1f} px  ({int(out.tri.ok.sum())}/33 joints)")
+    if out is not None and out.people is not None:
+        n_seen = max((len(v) for v in out.people.values()), default=0)
+        hud.append(f"{n_seen} people seen, tracking {'1' if out.frame.tracking != 'lost' else 'nobody'}")
+    if out is not None and out.dropped:
+        hud.append(f"ignoring camera {out.dropped} (disagrees)")
     if out is not None and out.swapped:
         hud.append(f"undid a left/right flip ({out.swapped})")
     if recording:
@@ -172,12 +193,12 @@
     from vision.live import LiveSource  # imports MediaPipe; keep replay usable without it
 
     detector, show_markers = MarkerDetector(mat), False
-    pipeline = Pipeline(cams)
+    pipeline = Pipeline(cams, mat=mat)
     monitor = MoveMonitor(cams, detector)
     recorder: Recorder | None = None
     last: PipelineOutput | None = None
     last_draw = 0.0
-    with LiveSource(cams, model) as src:
+    with LiveSource(cams, model, mat) as src:
         try:
             while True:
                 for pair in src.poll(0.005):
@@ -189,7 +210,7 @@
                     last_draw = now
                     latest = src.latest_frames()
                     images = {n: v[0] for n, v in latest.items() if v}
-                    poses = {n: v[1] for n, v in latest.items() if v}
+                    poses = last.raw if last is not None else {}  # the tracked person per camera
                     for n, image in images.items():
                         monitor.update(n, image, now)
                     alarms = {n: monitor.message(n) for n in cams}
```

Apply to `vision/calib_refine.py`:

```diff
--- a/vision/calib_refine.py
+++ b/vision/calib_refine.py
@@ -55,8 +55,8 @@
 
     pairs = []
     path = default_path("refine-")
-    pipeline = Pipeline(cams)
-    with LiveSource(cams, model) as src:
+    pipeline = Pipeline(cams, mat=mat)
+    with LiveSource(cams, model, mat) as src:
         problems = src.wait_ready()
         if problems:
             print("cannot capture: " + "; ".join(problems))
@@ -78,16 +78,17 @@
         capturing = elapsed >= COUNTDOWN_S
         for pair in src.poll(0.005):
             if capturing:
-                rec.write(pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s))
-                pairs.append((pair.a, pair.b))
+                out = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
+                rec.write(out)
+                pairs.append((out.raw["A"], out.raw["B"]))  # the person at the mat, as detected
         tiles = []
         for n, v in sorted(src.latest_frames().items()):
             if v is None:
                 tiles.append(np.zeros((360, 640, 3), np.uint8))
                 continue
-            frame, pose = v
+            frame, found = v
             tile = fit(frame, (640, 360))
-            if pose.detected:
+            for pose in found.people:
                 draw_skeleton(tile, pose.lm2d * [640 / frame.shape[1], 360 / frame.shape[0]],
                               ok=pose.vis >= 0.5, color=GREEN)
             tiles.append(tile)
```

Apply to `vision/checks.py`:

```diff
--- a/vision/checks.py
+++ b/vision/checks.py
@@ -151,8 +151,8 @@
 
     outputs = []
     monitor = MoveMonitor(cams, MarkerDetector(mat), interval_s=0.25)
-    pipeline = Pipeline(cams, smoothing=False)  # smoothing would hide the jitter these checks measure
-    with LiveSource(cams, model) as src:
+    pipeline = Pipeline(cams, smoothing=False, mat=mat)  # smoothing would hide the jitter these checks measure
+    with LiveSource(cams, model, mat) as src:
         problems = src.wait_ready()
         if problems:
             raise SystemExit("cannot run checks: " + "; ".join(problems))
```

- [ ] **Step 4: Run the whole suite and import every tool**

Run: `.venv/bin/python -m pytest -q && for m in debug_view calib_refine checks live calib_extrinsics; do .venv/bin/python -c "import vision.$m" || echo FAIL $m; done`
Expected: PASS (134 passed), no `FAIL` lines.

- [ ] **Step 5: Commit**

```bash
git add vision/live.py vision/debug_view.py vision/tests/test_debug_view.py vision/calib_refine.py vision/checks.py
git commit -m "vision: tracking area, ignored people and dropped camera in the live tools"
```

---

### Task 6: README

**Files:**
- Modify: `vision/README.md`

**Interfaces:**
- Consumes: behaviour from Tasks 1–5.

- [ ] **Step 1: Document who gets tracked**

Apply to `vision/README.md`:

```diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -60,6 +60,18 @@
 
 If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.
 
+### Who gets tracked
+
+Only the person on the mat or within 1 m of it. Each camera's pose detection searches only the area
+around the mat (the cyan "tracking area" box in the debug view; the full picture is still shown) and
+finds up to 3 people. A person counts as the same in both cameras when their hip, shoulder and ankle
+midpoints line up in 3D; of those near the mat, the one closest to the mat centre is tracked, and
+tracking only switches to someone else when they are 30 cm closer to the centre for half a second.
+Everyone else is drawn grey and labelled "ignored". If the two cameras disagree (e.g. a hand over one
+lens), the camera whose person stands at the mat is kept and the other is ignored for that frame
+("ignoring camera A (disagrees)"). Recordings keep everyone found, so `--retriangulate` re-runs the
+selection.
+
 ### Left/right flips
 
 Both cameras are at the same height, so a MediaPipe left/right flip in one camera still looks
```

- [ ] **Step 2: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (134 passed).

- [ ] **Step 3: Commit**

```bash
git add vision/README.md
git commit -m "vision: README: who gets tracked"
```

---

### Task 7: Foot anchoring

**Files:**
- Create: `vision/anchor.py`
- Create: `vision/tests/test_anchor.py`
- Modify: `vision/pipeline.py`, `vision/debug_view.py`, `vision/checks.py`, `vision/README.md`
- Modify: `vision/tests/test_checks.py`, `vision/tests/test_record_replay.py`

**Interfaces:**
- Consumes: `Pipeline` and `PipelineOutput` as changed by Task 4; `render_topdown`/`compose_view` as changed by Task 5.
- Produces: `FootAnchor().update(t, joints) -> (joints, {"l": bool, "r": bool})`, `.reset()`; `Pipeline(..., anchor_feet=True)`; `PipelineOutput.feet_locked`; `render_topdown(frame, mat, cams, feet_locked=None)`; check "feet anchored while standing".
- Spec: the "Addendum: foot anchoring (Task 7)" section of the spec.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_anchor.py`:

```python
import numpy as np

from vision.anchor import FEET, FootAnchor
from vision.tests.synth import standing_skeleton

DT = 1 / 30


def _run(frames, anchor=None):
    anchor = anchor or FootAnchor()
    outs, locks = [], []
    for k, X in enumerate(frames):
        o, lk = anchor.update(k * DT, X)
        outs.append(o)
        locks.append(lk)
    return np.array(outs), locks


def test_jittery_planted_foot_is_held_still():
    rng = np.random.default_rng(0)
    X = standing_skeleton()
    frames = [X + rng.normal(0, 0.01, X.shape) for _ in range(60)]
    outs, locks = _run(frames)
    assert all(lk["l"] and lk["r"] for lk in locks[10:])
    for side, idx in FEET.items():
        assert outs[10:, list(idx)].std(axis=0).max() < 0.002  # raw jitter was 1 cm
    toe_y = outs[-1, 31, 1]
    assert abs(toe_y - 0.02) < 0.01  # lowest point on the mat surface
    assert outs[10:, 0].std(axis=0).max() > 0.005  # the rest of the body is untouched


def test_real_step_releases_quickly_without_a_jump():
    X = standing_skeleton()
    frames = [X.copy() for _ in range(15)]
    for k in range(1, 16):  # left foot steps 30 cm forward over 0.5 s
        Y = X.copy()
        Y[[27, 29, 31], 2] += 0.02 * k
        frames.append(Y)
    outs, locks = _run(frames)
    assert locks[14]["l"]
    first_away = next(k for k in range(15, 30) if frames[k][29, 2] - X[29, 2] > 0.05)
    assert not locks[first_away + 1]["l"]  # released within 2 frames of really moving
    assert locks[-1]["r"]  # the other foot stays planted
    jumps = np.linalg.norm(np.diff(outs[:, 29], axis=0), axis=1)
    assert jumps.max() < 0.06  # blends instead of jumping
    assert np.linalg.norm(outs[-1, 29] - frames[-1][29]) < 0.03  # follows the foot again


def test_squat_keeps_feet_locked_while_hips_drop():
    rng = np.random.default_rng(1)
    X = standing_skeleton()
    frames = []
    for k in range(60):
        Y = X + rng.normal(0, 0.008, X.shape)
        drop = 0.4 * min(k / 30, 1.0)
        Y[:25, 1] -= drop  # upper body and hips go down, feet stay
        frames.append(Y)
    outs, locks = _run(frames)
    assert all(lk["l"] and lk["r"] for lk in locks[10:])
    assert outs[-1, 23, 1] < X[23, 1] - 0.35


def test_lifted_or_missing_foot_is_not_locked():
    X = standing_skeleton()
    lifted = X.copy()
    lifted[[27, 29, 31], 1] += 0.25
    _, locks = _run([lifted] * 20)
    assert not locks[-1]["l"] and locks[-1]["r"]
    gone = X.copy()
    gone[29] = np.nan
    a = FootAnchor()
    _run([X] * 20, a)
    out, lk = a.update(1.0, gone)
    assert not lk["l"] and np.isnan(out[29]).all()


def test_pipeline_anchors_feet_and_can_be_turned_off():
    from vision.pipeline import Pipeline
    from vision.tests.synth import pose_from_points, target_cams

    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(2)
    on, off = Pipeline(cams, smoothing=False), Pipeline(cams, smoothing=False, anchor_feet=False)
    heels_on, heels_off, locked = [], [], []
    for k in range(45):
        a = pose_from_points(cams["A"], X, noise_px=2.0, rng=rng)
        b = pose_from_points(cams["B"], X, noise_px=2.0, rng=rng)
        o1, o2 = on.process(k / 30, a, b), off.process(k / 30, a, b)
        heels_on.append(o1.frame.joints[29])
        heels_off.append(o2.frame.joints[29])
        locked.append(o1.feet_locked)
    assert locked[-1] == {"l": True, "r": True} and off.process(2.0, a, b).feet_locked is None
    assert np.std(heels_on[15:], axis=0).max() < 0.3 * np.std(heels_off[15:], axis=0).max()
```

Apply to `vision/tests/test_checks.py` (expects the new check, and excludes feet from the unsmoothed-equality test):

```diff
--- a/vision/tests/test_checks.py
+++ b/vision/tests/test_checks.py
@@ -50,7 +50,7 @@
     assert not any(c.name.startswith("lens RMS") for c in checks)
     assert {"frames tracked in 3D", "mean body reprojection", "thigh_l length std",
             "standing knee angle l", "median triangulation angle", "marker distance 0–2",
-            "pair skew p95", "capture fps A"} <= names
+            "pair skew p95", "capture fps A", "feet anchored while standing"} <= names
     failed = [c for c in checks if c.passed is False]
     assert failed == [], format_checks(failed)
     assert "PASS" in format_checks(checks)
@@ -73,7 +73,9 @@
             rec.write(pipe.process(1000 + k / 30, pose_from_points(cams["A"], X, noise_px=4.0, rng=rng),
                                    pose_from_points(cams["B"], X, noise_px=4.0, rng=rng), 0.0))
     for out in outputs_from_recording(path, cams):
-        np.testing.assert_allclose(out.frame.joints[out.tri.ok], out.tri.points[out.tri.ok])
+        body = out.tri.ok.copy()
+        body[27:33] = False  # feet may be held by the FootAnchor; everything else is unsmoothed
+        np.testing.assert_allclose(out.frame.joints[body], out.tri.points[body])
 
 
 def test_height_estimate_and_check():
```

Apply to `vision/tests/test_record_replay.py`:

```diff
--- a/vision/tests/test_record_replay.py
+++ b/vision/tests/test_record_replay.py
@@ -60,4 +60,6 @@
             rec.write(pipe.process(1000 + k / 30, a, b, 0.0))
     raw = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False))
     for out in raw:
-        np.testing.assert_allclose(out.frame.joints[out.tri.ok], out.tri.points[out.tri.ok])
+        body = out.tri.ok.copy()
+        body[27:33] = False  # feet may be held by the FootAnchor; everything else is unsmoothed
+        np.testing.assert_allclose(out.frame.joints[body], out.tri.points[body])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_anchor.py vision/tests/test_checks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vision.anchor'` at collection of `test_anchor.py` (run `.venv/bin/python -m pytest vision/tests/test_checks.py -q` alone to see `test_run_checks_all_pass_on_clean_synthetic_data` fail on the missing "feet anchored while standing" check).

- [ ] **Step 3: Implement**

Create `vision/anchor.py`:

```python
"""Foot anchoring: pin a planted foot so MediaPipe's wobble doesn't make it jitter.

A foot (ankle, heel, toe) is planted when its heel and toe are near the floor and it has barely
moved for a moment; it is then held at its average position, with the lowest point snapped to the
mat surface. It is released by a real step or lift (the raw foot moves away from the anchor, or the
heel leaves the floor, for a couple of frames) and blends back to the live position so it never jumps.
The rest of the body is untouched: a squat with planted feet keeps the feet locked while hips drop.
"""
from __future__ import annotations

from collections import deque

import numpy as np

FEET = {"l": (27, 29, 31), "r": (28, 30, 32)}  # ankle, heel, toe (foot index)
CONTACT_HEIGHT_M = 0.12  # heel and toe below this: touching the floor
STILL_SPEED_MPS = 0.15  # slower than this over STILL_S: not moving
STILL_S = 0.2
RELEASE_M = 0.05  # raw foot this far from the anchor ...
RELEASE_FRAMES = 2  # ... for this many frames in a row: a real step
BLEND_S = 0.1
TOE_HEIGHT_M = 0.02  # where the lowest foot point sits on the mat surface


class _Foot:
    def __init__(self):
        self.history: deque[tuple[float, np.ndarray]] = deque()
        self.anchor: np.ndarray | None = None  # held output (lowest point snapped to the floor)
        self.anchor_raw: np.ndarray | None = None  # what the detections said when it planted
        self.away = 0
        self.blend: tuple[float, np.ndarray] | None = None  # (release time, held - live offset) fading out

    def reset(self):
        self.__init__()


class FootAnchor:
    def __init__(self):
        self._feet = {side: _Foot() for side in FEET}

    def reset(self) -> None:
        for f in self._feet.values():
            f.reset()

    def update(self, t: float, joints: np.ndarray) -> tuple[np.ndarray, dict[str, bool]]:
        """Returns (joints with planted feet held, side -> locked)."""
        out = np.array(joints, float, copy=True)
        locked = {}
        for side, idx in FEET.items():
            foot = self._feet[side]
            pts = out[list(idx)]
            if not np.isfinite(pts).all():
                foot.reset()
                locked[side] = False
                continue
            locked[side] = self._step(foot, t, pts)
            if foot.anchor is not None:
                out[list(idx)] = foot.anchor
            elif foot.blend is not None:
                t0, offset = foot.blend
                a = (t - t0) / BLEND_S
                if a >= 1:
                    foot.blend = None
                else:  # follow the live foot, with the gap from the held spot fading out
                    out[list(idx)] = pts + (1 - a) * offset
        return out, locked

    @staticmethod
    def _step(foot: _Foot, t: float, pts: np.ndarray) -> bool:
        on_floor = pts[1, 1] <= CONTACT_HEIGHT_M and pts[2, 1] <= CONTACT_HEIGHT_M
        if foot.anchor is not None:
            moved = float(np.max(np.linalg.norm(pts - foot.anchor_raw, axis=1))) > RELEASE_M
            foot.away = foot.away + 1 if (moved or not on_floor) else 0
            if foot.away < RELEASE_FRAMES:
                return True
            foot.blend = (t, foot.anchor - pts)
            foot.anchor = foot.anchor_raw = None
            foot.away = 0
            foot.history.clear()
            return False
        foot.history.append((t, pts.copy()))
        while len(foot.history) > 1 and t - foot.history[1][0] >= STILL_S:
            foot.history.popleft()
        t_first, first = foot.history[0]
        span = t - t_first
        if not on_floor or span < STILL_S:
            return False
        window = np.stack([p for _, p in foot.history])
        speed = float(np.max(np.linalg.norm(window.mean(axis=0) - first, axis=1))) / span
        if speed >= STILL_SPEED_MPS:
            return False
        foot.anchor_raw = window.mean(axis=0)
        foot.anchor = foot.anchor_raw.copy()
        offset = min(foot.anchor[1, 1], foot.anchor[2, 1]) - TOE_HEIGHT_M
        if abs(offset) < CONTACT_HEIGHT_M:
            foot.anchor[:, 1] -= offset
        foot.blend = None
        return True
```

Apply to `vision/pipeline.py`:

```diff
--- a/vision/pipeline.py
+++ b/vision/pipeline.py
@@ -5,6 +5,7 @@
 
 import numpy as np
 
+from vision.anchor import FootAnchor
 from vision.cleanup import Cleaner
 from vision.config import CameraConfig, MatConfig
 from vision.filters import OneEuroFilter
@@ -28,6 +29,7 @@
     people: dict[str, list[PoseResult]] | None = None  # everyone found per camera (multi-person input)
     ignored: dict[str, list[PoseResult]] | None = None  # people not tracked, per camera
     dropped: str | None = None  # camera ignored this frame because it disagreed with the other
+    feet_locked: dict[str, bool] | None = None  # "l"/"r" -> foot held by the FootAnchor this frame
 
 
 def _flipped(p: PoseResult) -> PoseResult:
@@ -79,8 +81,10 @@
 
 class Pipeline:
     def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
-                 min_vis: float = 0.5, max_reproj_px: float = 15.0, mat: MatConfig | None = None):
+                 min_vis: float = 0.5, max_reproj_px: float = 15.0, mat: MatConfig | None = None,
+                 anchor_feet: bool = True):
         self.cams = cams
+        self._anchor = FootAnchor() if anchor_feet else None
         self._selector = PersonSelector(cams, mat or MatConfig())
         self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
         self._cleaner = Cleaner(cams)
@@ -117,8 +121,14 @@
                     best, swapped, a, b, tri = cost, name, a2, b2, tri2
         usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
         joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
+        feet_locked = None
+        if self._anchor is not None:
+            if tracking == "lost":
+                self._anchor.reset()
+            else:
+                joints, feet_locked = self._anchor.update(t, joints)
         if self._filter is not None:
             joints = self._filter(joints, t)
         frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
         return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped,
-                              people=people, ignored=ignored, dropped=dropped)
+                              people=people, ignored=ignored, dropped=dropped, feet_locked=feet_locked)
```

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -96,7 +96,8 @@
     return panel
 
 
-def render_topdown(frame: FrameResult | None, mat: MatConfig, cams: dict[str, CameraConfig]) -> np.ndarray:
+def render_topdown(frame: FrameResult | None, mat: MatConfig, cams: dict[str, CameraConfig],
+                   feet_locked: dict[str, bool] | None = None) -> np.ndarray:
     img = _blank()
     for x in np.arange(-2.5, 2.51, 0.5):
         a, b = TOP([[x, -1.3], [x, 2.9]])
@@ -124,10 +125,14 @@
         draw_text(img, name, pt(p + [8, -8]), YELLOW)
     if frame is not None:
         draw_skeleton(img, TOP(frame.joints[:, [0, 2]]))
+        for side, heel in (("l", 29), ("r", 30)):
+            if (feet_locked or {}).get(side) and np.isfinite(frame.joints[heel]).all():
+                c = TOP([frame.joints[heel][[0, 2]]])[0]
+                cv2.rectangle(img, pt(c - 7), pt(c + 7), WHITE, 2)  # this foot is anchored
     a, b = TOP([[0.0, -1.15], [0.5, -1.15]])
     cv2.arrowedLine(img, pt(a), pt(b), WHITE, 1, tipLength=0.2)
     draw_text(img, "+x", pt(b + [-24, -6]), WHITE, 0.4)
-    draw_text(img, "top view   orange = left, blue = right", (8, PANEL_H - 10), WHITE, 0.45)
+    draw_text(img, "top view   orange = left, blue = right, square = foot anchored", (8, PANEL_H - 10), WHITE, 0.45)
     return img
 
 
@@ -185,7 +190,8 @@
     if recording:
         hud.append(recording)
     hud.append("keys: r record   m markers   q quit")
-    bottom = np.hstack([render_topdown(frame, mat, cams), render_front(frame, hud)])
+    bottom = np.hstack([render_topdown(frame, mat, cams, out.feet_locked if out is not None else None),
+                        render_front(frame, hud)])
     return np.vstack([np.hstack(panels), bottom])
 
 
```

Apply to `vision/checks.py`:

```diff
--- a/vision/checks.py
+++ b/vision/checks.py
@@ -100,6 +100,10 @@
         checks.append(_check(f"standing knee angle {side}", float(np.median(vals)) if vals else None, "°", "170–180",
                              lambda v: 170 <= v <= 180))
 
+    locks = [o.feet_locked for o in outputs if o.frame.tracking == "3d" and o.feet_locked is not None]
+    frac_locked = 100.0 * sum(lk.get("l", False) and lk.get("r", False) for lk in locks) / len(locks) if locks else None
+    checks.append(_check("feet anchored while standing", frac_locked, "%", "≥ 80", lambda v: v >= 80))
+
     if height_cm is not None:
         est = estimate_height_cm(seq) if seq is not None else None
         checks.append(_check("estimated height", est, "cm", f"{height_cm:.0f} ± 5%",
```

Apply to `vision/README.md`:

```diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -72,6 +72,15 @@
 ("ignoring camera A (disagrees)"). Recordings keep everyone found, so `--retriangulate` re-runs the
 selection.
 
+### Foot anchoring
+
+A planted foot (heel and toe within 12 cm of the floor, barely moving for 0.2 s) is held still at its
+average position with its lowest point on the mat surface, so MediaPipe's wobble doesn't make the
+feet jitter while standing or squatting. It releases on a real step or lift (the detected foot more
+than 5 cm away, or the heel off the floor, for 2 frames) and fades back to the live position over
+0.1 s. The debug view's top view draws a white square under an anchored foot; `checks` reports
+"feet anchored while standing". `Pipeline(..., anchor_feet=False)` turns it off.
+
 ### Left/right flips
 
 Both cameras are at the same height, so a MediaPipe left/right flip in one camera still looks
```

- [ ] **Step 4: Run the whole suite and import every tool**

Run: `.venv/bin/python -m pytest -q && for m in debug_view calib_refine checks live calib_extrinsics; do .venv/bin/python -c "import vision.$m" || echo FAIL $m; done`
Expected: PASS (139 passed), no `FAIL` lines.

- [ ] **Step 5: Commit**

```bash
git add vision/anchor.py vision/tests/test_anchor.py vision/pipeline.py vision/debug_view.py vision/checks.py vision/README.md vision/tests/test_checks.py vision/tests/test_record_replay.py
git commit -m "vision: anchor planted feet so they don't jitter"
```

---

