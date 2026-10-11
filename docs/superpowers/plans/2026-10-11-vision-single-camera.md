# Single camera (camera B only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tool works with camera B alone and produces a smooth 3D body in the mat frame from one view: position and scale from the planted foot meeting the calibrated floor, shape and depth from MediaPipe's world landmarks, posed by the body-model fit with the person's height.

**Architecture:** `vision/mono.py` builds a one-camera body (world-landmark shape rotated into the mat frame, moved so the planted foot stands where its ray meets the floor). `PersonSelector` gets a one-camera path (candidates by floor position near the mat); `floor_position` trusts the foot nearer the camera (a lifted foot's ray lands far behind). `ModelTracker` gets a one-camera mode (measure from those bodies scaled to the entered height, a world-shape prior and a floor-contact prior, its own smoothing). `Pipeline(tracker="auto")` uses it when exactly one camera is calibrated. Tools: `config/person.yaml` height, `cams --remove`, one-camera `calib_extrinsics`/`checks`/`debug_view`, `calib_refine` explains it needs two cameras.

**Tech Stack:** Python 3.11, NumPy, OpenCV, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-vision-single-camera-design.md`

Every code block below was run on a copy of the branch before this plan was written: applying the tasks in order gives 212, 218, 221, 221 passing tests, and camera-B-only replays of Eric's recordings gave: selected 100% of frames, hip position 5.3–8.8 cm (median) from two-camera triangulation, bones 0 mm, still jitter 0.78 cm, 5.4 ms/frame, height estimate 159–164 cm for 165 entered.

## Global Constraints

- One camera = exactly one calibrated camera in `config/cameras.yaml`; two-camera behaviour is unchanged.
- Floor placement: per foot (ankle 0.08 m, heel 0.04 m planes), feet agreeing within 0.6 m are averaged, else the foot nearer the camera; person-size check range 0.5–2.4 m.
- One-camera fit: world-shape prior 150 px/m, floor-contact 300 px/m; smoothing min cutoff 0.7 Hz, beta 2.0, position 1.0 Hz (two cameras keep beta 6.0, position 3.0 Hz).
- Height: `config/person.yaml` `height_cm`, set by `--height-cm` on `debug_view`/`checks` and reused.
- `Pipeline(..., tracker="auto")` default: "model" with one camera, "triangulate" with two.
- Run tests with `.venv/bin/python -m pytest` from the repo root.

## Review Focus

1. Live with only camera B plugged in: `LiveSource`/`Pairer` with one camera, `debug_view` layout, `calib_extrinsics` save, `checks` — no crash and no reference to camera A.
2. No height entered: the body must still appear (scale 1.0 from MediaPipe) and `debug_view` must say to pass `--height-cm`.
3. Feet not visible to B (e.g. cropped at the frame bottom): position held from the last frame, not snapped to the mat centre; first frames without feet produce "lost", not garbage.
4. A second person beside the mat with one camera: floor-position selection with stickiness must keep the person on the mat.
5. Old two-camera recordings replayed with a one-camera config (`--retriangulate`): must run without error.

---

### Task 1: Height setting, `cams --remove`, `calib_refine` needs two cameras

**Files:**
- Modify: `vision/config.py`, `vision/cams.py`, `vision/calib_refine.py`
- Modify: `vision/tests/test_config.py`, `vision/tests/test_calib_refine.py`
- Create: `vision/tests/test_cams.py`

**Interfaces:**
- Produces: `config.PERSON_PATH`, `load_person(path=PERSON_PATH) -> float | None`, `save_person(height_cm, path=PERSON_PATH)`, `height_from(args_height_cm, path=PERSON_PATH) -> float | None` (saves when given).
- Produces: `python -m vision.cams --remove A|B`; `calib_refine.main()` returns 1 with "needs two calibrated cameras" when fewer than two cameras are calibrated.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_config.py`:

```diff
--- a/vision/tests/test_config.py
+++ b/vision/tests/test_config.py
@@ -86,3 +86,14 @@
     assert loaded.zoom_std == 0.012
     np.testing.assert_allclose(loaded.target_aim, [0.1, 0.8, 0.0])
     assert CameraConfig.from_dict("B", {"index": 1}).zoom_std is None
+
+
+def test_person_height_is_remembered(tmp_path):
+    from vision.config import height_from, load_person, save_person
+
+    path = tmp_path / "person.yaml"
+    assert load_person(path) is None and height_from(None, path) is None
+    assert height_from(175.0, path) == 175.0  # given once ...
+    assert height_from(None, path) == 175.0  # ... remembered
+    save_person(168.5, path)
+    assert load_person(path) == 168.5
```

Apply to `vision/tests/test_calib_refine.py`:

```diff
--- a/vision/tests/test_calib_refine.py
+++ b/vision/tests/test_calib_refine.py
@@ -35,3 +35,15 @@
     out2 = Pipeline(cams, mat=MatConfig()).process(1000.0, pair2.a, pair2.b, 0.0)
     a, b = refine_views(pair2, out2)
     assert a is out2.raw["A"] and b is out2.raw["B"]
+
+
+def test_calib_refine_explains_it_needs_two_cameras(monkeypatch, capsys):
+    import sys
+
+    import vision.calib_refine as R
+    from vision.tests.synth import target_cams
+
+    monkeypatch.setattr(R, "load_cameras", lambda: {"B": target_cams()["B"]})
+    monkeypatch.setattr(sys, "argv", ["calib_refine"])
+    assert R.main() == 1
+    assert "needs two calibrated cameras" in capsys.readouterr().out
```

Create `vision/tests/test_cams.py`:

```python
def test_remove_drops_a_camera_without_opening_any(monkeypatch, capsys):
    import sys

    import vision.cams as C
    from vision.tests.synth import target_cams

    saved = {}
    monkeypatch.setattr(C, "load_cameras", lambda: target_cams())
    monkeypatch.setattr(C, "save_cameras", lambda cams: saved.update(cams=cams))
    monkeypatch.setattr(C.cv2, "VideoCapture", lambda *a: (_ for _ in ()).throw(AssertionError("opened a camera")))
    monkeypatch.setattr(sys, "argv", ["cams", "--remove", "A"])
    assert C.main() == 0 and sorted(saved["cams"]) == ["B"]
    assert "removed camera A" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_config.py vision/tests/test_cams.py vision/tests/test_calib_refine.py -q`
Expected: 3 FAIL — `ImportError: cannot import name 'height_from'`; `SystemExit: 2` (argparse: unrecognized `--remove`); the calib_refine test fails on the missing message (without the guard it tries to open cameras, which fail in this shell, and returns 1 with "cannot capture").

- [ ] **Step 3: Implement**

Apply to `vision/config.py`:

```diff
--- a/vision/config.py
+++ b/vision/config.py
@@ -13,6 +13,7 @@
 CONFIG_DIR = REPO_ROOT / "config"
 MAT_PATH = CONFIG_DIR / "mat.yaml"
 CAMERAS_PATH = CONFIG_DIR / "cameras.yaml"
+PERSON_PATH = CONFIG_DIR / "person.yaml"  # local: the tracked person's height (one-camera scale)
 
 # Marker id -> (sign of x, sign of z). +x is the patient's left, +z is toward Mira.
 _MARKER_SIGNS = {0: (1, 1), 1: (-1, 1), 2: (1, -1), 3: (-1, -1)}
@@ -166,3 +167,26 @@
     data = {"cameras": {n: c.to_dict() for n, c in sorted(cams.items())}}
     path.write_text(yaml.safe_dump(data, sort_keys=False))
     return backup
+
+
+def load_person(path: Path = PERSON_PATH) -> float | None:
+    """The person's height in cm, or None if never entered."""
+    path = Path(path)
+    if not path.exists():
+        return None
+    h = (yaml.safe_load(path.read_text()) or {}).get("height_cm")
+    return None if h is None else float(h)
+
+
+def save_person(height_cm: float, path: Path = PERSON_PATH) -> None:
+    Path(path).parent.mkdir(parents=True, exist_ok=True)
+    Path(path).write_text(yaml.safe_dump({"height_cm": float(height_cm)}))
+
+
+def height_from(args_height_cm: float | None, path: Path = PERSON_PATH) -> float | None:
+    """--height-cm if given (and remembered for next time), else the saved height."""
+    if args_height_cm is not None:
+        save_person(args_height_cm, path)
+        return float(args_height_cm)
+    return load_person(path)
+
```

Apply to `vision/cams.py`:

```diff
--- a/vision/cams.py
+++ b/vision/cams.py
@@ -4,6 +4,7 @@
 
 Shows every camera that opens, tiled and labelled by index. Press a or b, then the
 index digit, to assign; s saves to config/cameras.yaml; q quits without saving.
+One camera is enough (the other is simply not assigned); `--remove A` drops A from the config.
 """
 from __future__ import annotations
 
@@ -22,7 +23,16 @@
 def main() -> int:
     p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
     p.add_argument("--max-index", type=int, default=6)
+    p.add_argument("--remove", choices=["A", "B"], help="drop a camera from config/cameras.yaml and exit")
     args = p.parse_args()
+    if args.remove:
+        cams = load_cameras()
+        if cams.pop(args.remove, None) is None:
+            print(f"camera {args.remove} is not configured")
+            return 1
+        save_cameras(cams)
+        print(f"removed camera {args.remove}; configured: {sorted(cams) or 'none'}")
+        return 0
 
     caps = {}
     for i in range(args.max_index):
```

Apply to `vision/calib_refine.py`:

```diff
--- a/vision/calib_refine.py
+++ b/vision/calib_refine.py
@@ -124,6 +124,10 @@
     args = p.parse_args()
 
     cams, mat = load_cameras(), load_mat()
+    if len([c for c in cams.values() if c.calibrated]) < 2:
+        print("calib_refine needs two calibrated cameras; with one camera, calib_extrinsics is the whole "
+              "calibration")
+        return 1
     if args.from_path:
         pairs = [(raw.get("A"), raw.get("B")) for _, raw, _ in iter_frames(args.from_path)]
     else:
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (212 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/config.py vision/cams.py vision/calib_refine.py vision/tests/test_config.py vision/tests/test_cams.py vision/tests/test_calib_refine.py
git commit -m "vision: saved person height, cams --remove, calib_refine needs two cameras"
```

---

### Task 2: One-camera 3D: floor placement, selection, model fit, pipeline

**Files:**
- Create: `vision/mono.py`, `vision/tests/test_mono.py`
- Modify: `vision/select.py`, `vision/tracker.py`, `vision/pipeline.py`, `vision/tests/test_select.py`

**Interfaces:**
- Produces: `mono.world_shape(pose, cam)`, `mono.mono_joints(pose, cam, scale=1.0, last_xz=None)`, `mono.stature(shape_rel)`.
- Produces: `PersonSelector` one-camera path (`_select_one`) when exactly one camera is calibrated; `floor_position` per-foot (nearer foot); `HEIGHT_RANGE_M = (0.5, 2.4)`; `FEET_AGREE_M = 0.6`.
- Produces: `ModelTracker(cams, ..., height_m=None)` with `.single`, `.world_scale`, `BodyShape.scaled(k)`; constants `WORLD_PX_PER_M`, `CONTACT_PX_PER_M`, `SINGLE_BETA`, `SINGLE_ROOT_MIN_CUTOFF`.
- Produces: `Pipeline(cams, ..., tracker="auto", height_m=None)`; one calibrated camera → mono joints + model fit, tracking `single_cam_<name>` or `lost`.
- Test changes: `test_uncalibrated_falls_back_to_first_person` is replaced by `test_only_one_calibrated_camera_selects_from_that_camera_by_the_floor` (one calibrated camera now means a one-camera setup); `test_plausible_height` uses scale 0.25 for "tiny" and checks a 0.7 m deep squat is plausible.

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_mono.py`:

```python
import numpy as np

from vision.config import MatConfig
from vision.mono import mono_joints, stature, world_shape
from vision.pipeline import Pipeline
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

K = np.array([[1484.0, 0, 640], [0, 1484.0, 360], [0, 0, 1]])


def _cam_b():
    return make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K)


def test_world_shape_is_the_body_relative_to_its_hips_in_the_mat_frame():
    cam, X = _cam_b(), standing_skeleton()
    W = world_shape(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(W, X - (X[23] + X[24]) / 2, atol=1e-9)


def test_mono_joints_stand_where_the_feet_meet_the_floor():
    cam = _cam_b()
    X = standing_skeleton() + [0.25, 0.0, -0.4]
    J = mono_joints(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(J[[23, 24]].mean(axis=0)[[0, 2]], X[[23, 24]].mean(axis=0)[[0, 2]], atol=0.05)
    assert abs(np.min(J[[29, 30, 31, 32], 1])) < 1e-9  # lowest foot point on the mat


def test_stature_of_the_synthetic_body():
    X = standing_skeleton()
    s = stature(X - (X[23] + X[24]) / 2)
    assert abs(s - X[2, 1] / 0.936 - (0 - np.min(X[[29, 30, 31, 32], 1])) / 0.936) < 1e-9


def test_one_camera_pipeline_fits_a_moving_body_with_the_right_scale():
    cam, X0 = _cam_b(), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    rng = np.random.default_rng(0)
    errs = []
    for k in range(120):
        X = X0 + [0.003 * max(k - 60, 0), 0.0, 0.0]
        v = pose_from_points(cam, X, t=k / 30, noise_px=1.0, rng=rng)
        out = pipe.process(k / 30, None, v)
        if k > 70:
            assert out.tracker == "model"
            errs.append(np.median(np.linalg.norm(out.frame.joints - X, axis=1)))
    assert np.median(errs) < 0.04  # one camera: a few cm


def test_one_camera_without_feet_keeps_the_last_position():
    cam, X = _cam_b(), standing_skeleton()
    J1 = mono_joints(pose_from_points(cam, X), cam)
    p = pose_from_points(cam, X)
    p.vis[[27, 28, 29, 30]] = 0.0
    assert mono_joints(p, cam) is None
    J2 = mono_joints(p, cam, last_xz=J1[[23, 24]].mean(axis=0)[[0, 2]])
    assert J2 is not None
```

Apply to `vision/tests/test_select.py`:

```diff
--- a/vision/tests/test_select.py
+++ b/vision/tests/test_select.py
@@ -92,14 +92,15 @@
     assert sel.ignored["A"] == [junk]
 
 
-def test_uncalibrated_falls_back_to_first_person():
+def test_only_one_calibrated_camera_selects_from_that_camera_by_the_floor():
     cams = _cams()
-    a, b = _person(cams, 0.0, 0.0)
-    cams["B"].R = None
-    sel = PersonSelector(cams, MAT).select(0.0, [a], [b])
-    assert sel.a is a and sel.b is b
+    on_a, on_b = _person(cams, 0.0, 0.2)
+    by_a, by_b = _person(cams, 2.6, 0.0)
+    cams["A"].R = None  # A not calibrated (or unplugged): B alone
+    sel = PersonSelector(cams, MAT).select(0.0, [on_a], [by_b, on_b])
+    assert sel.a is None and sel.b is on_b and sel.ignored["B"] == [by_b]
+    np.testing.assert_allclose(sel.position, [0.0, 0.2], atol=0.08)
 
-
 def _giant(cam, x=0.0, z=0.0, scale=2.0):
     """A skeleton standing at (x, z) in one view, blown up about its feet: far too tall for a person
     (head well above the image), like MediaPipe fitting a body to a hand over the lens."""
@@ -117,8 +118,10 @@
     assert plausible_height(a, cams["A"], floor_position(a, cams["A"]))
     giant = _giant(cams["A"])
     assert not plausible_height(giant, cams["A"], floor_position(giant, cams["A"]))
-    tiny = _giant(cams["A"], scale=0.4)
+    tiny = _giant(cams["A"], scale=0.25)  # ~0.4 m: not a person
     assert not plausible_height(tiny, cams["A"], floor_position(tiny, cams["A"]))
+    squat = _giant(cams["A"], scale=0.42)  # ~0.7 m tall: the bottom of a deep squat is still a person
+    assert plausible_height(squat, cams["A"], floor_position(squat, cams["A"]))
     headless = pose_from_points(cams["A"], standing_skeleton())
     headless.vis[[0, 2, 5]] = 0.0
     assert not plausible_height(headless, cams["A"], floor_position(headless, cams["A"]))
@@ -158,3 +161,12 @@
     for k in range(1, 37):  # 1.2 s of nobody
         sel.select(k / 30, [], [])
     assert sel.select(1.2, [ch_a, tr_a], [tr_b, ch_b]).a is ch_a  # track forgotten: centre wins at once
+
+
+def test_floor_position_trusts_the_planted_foot_not_the_lifted_one():
+    cams = _cams()
+    X = standing_skeleton()
+    X[[27, 29, 31], 1] += 0.25  # left foot lifted 25 cm (a step)
+    a = pose_from_points(cams["A"], X)
+    xz = floor_position(a, cams["A"])
+    np.testing.assert_allclose(xz, X[[28, 30], :][:, [0, 2]].mean(axis=0), atol=0.06)  # the right (planted) foot
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_mono.py vision/tests/test_select.py -q`
Expected: FAIL — `test_mono.py` at collection (`ModuleNotFoundError: No module named 'vision.mono'`); in `test_select.py` the one-camera selection, deep-squat and planted-foot tests fail with AssertionError.

- [ ] **Step 3: Implement**

Create `vision/mono.py`:

```python
"""3D from one calibrated camera: MediaPipe's world landmarks for shape, the floor for position and scale.

MediaPipe estimates a metric, hip-centred 3D pose for each person (world landmarks, camera-aligned
axes). Rotated into the mat frame with the camera's rotation it gives the body's shape and depth; the
floor fixes where it stands: rays through the visible feet meet the mat surface, and the body is moved so
its feet are there and its lowest point is on the floor.
"""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import world_to_mat
from vision.select import floor_position
from vision.types import LEFT_HIP, RIGHT_HIP, PoseResult

FEET = [27, 28, 29, 30, 31, 32]
EYES = (2, 5)
EYE_HEIGHT_RATIO = 0.936  # eye height / stature


def world_shape(pose: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """MediaPipe's 3D pose in the mat frame, relative to the hip midpoint (metres)."""
    if pose is None or not pose.detected or pose.world is None or cam is None or not cam.calibrated:
        return None
    hip = (pose.world[LEFT_HIP] + pose.world[RIGHT_HIP]) / 2
    return world_to_mat(pose.world - hip, cam.R)


def mono_joints(pose: PoseResult, cam: CameraConfig, scale: float = 1.0, last_xz=None) -> np.ndarray | None:
    """Full 33-joint body in the mat frame from one camera: the world-landmark shape (times scale), moved so
    the feet stand where their rays meet the floor (or at last_xz when the feet aren't visible)."""
    body = world_shape(pose, cam)
    if body is None:
        return None
    body = body * scale
    feet_xz = floor_position(pose, cam)
    if feet_xz is None:
        if last_xz is None:
            return None
        feet_xz = np.asarray(last_xz, float)
    vis = [k for k in (27, 28, 29, 30) if pose.vis[k] >= 0.5]
    body_feet_xz = body[vis or [27, 28]][:, [0, 2]].mean(axis=0)
    off_xz = feet_xz - body_feet_xz
    return body + np.array([off_xz[0], -np.min(body[FEET, 1]), off_xz[1]])


def stature(shape_rel: np.ndarray) -> float:
    """Height (m) of a hip-relative body from its eye height above its lowest foot point."""
    eyes = (shape_rel[EYES[0], 1] + shape_rel[EYES[1], 1]) / 2
    return float((eyes - np.min(shape_rel[FEET, 1])) / EYE_HEIGHT_RATIO)
```

Apply to `vision/select.py`:

```diff
--- a/vision/select.py
+++ b/vision/select.py
@@ -25,8 +25,9 @@
 FOOT_HEIGHTS = {27: 0.08, 28: 0.08, 29: 0.04, 30: 0.04}  # ankles, heels: metres above the mat surface
 MIDPOINTS = ((23, 24), (11, 12), (27, 28))  # hips first: its triangulation is the person's position
 MIN_VIS = 0.5
+FEET_AGREE_M = 0.6  # two feet placed closer than this are taken as both planted
 STICK_M = 0.5  # the tracked person is the candidate within this distance of where they were
-HEIGHT_RANGE_M = (0.8, 2.4)  # implied feet-to-head height of a person seen by one camera (squats allowed)
+HEIGHT_RANGE_M = (0.5, 2.4)  # implied feet-to-head height of a person seen by one camera (squats allowed)
 
 
 @dataclass
@@ -59,10 +60,16 @@
     heights = np.array([FOOT_HEIGHTS[j] for j in idx])
     with np.errstate(divide="ignore", invalid="ignore"):
         s = (heights - c[1]) / dirs[:, 1]
-    hits = [c + si * d for si, d in zip(s, dirs) if np.isfinite(si) and si > 0]
+    hits = {j: (c + si * d)[[0, 2]] for j, si, d in zip(idx, s, dirs) if np.isfinite(si) and si > 0}
     if not hits:
         return None
-    return np.median(np.array(hits)[:, [0, 2]], axis=0)
+    # each foot on its own: a lifted foot's ray meets the floor far behind it, so trust the foot nearer the
+    # camera; average the two when they agree (both planted)
+    feet = [np.median([hits[j] for j in side if j in hits], axis=0) for side in ((27, 29), (28, 30))
+            if any(j in hits for j in side)]
+    if len(feet) == 2 and np.linalg.norm(feet[0] - feet[1]) < FEET_AGREE_M:
+        return (feet[0] + feet[1]) / 2
+    return min(feet, key=lambda f: np.linalg.norm(f - c[[0, 2]]))
 
 
 def plausible_height(person: PoseResult, cam: CameraConfig, xz) -> bool:
@@ -147,14 +154,36 @@
         self._pos = np.asarray(cands[pick][0], float)
         return cands[pick]
 
+    def _select_one(self, t: float, name: str, people: list[PoseResult]) -> Selection:
+        cam = self.cams[name]
+        if self._pos is not None and t - self._last_pick_t > self.forget_s:
+            self._pos, self._challenge_since = None, None
+        cands = []
+        for k, p in enumerate(people):
+            xz = floor_position(p, cam)
+            if xz is not None and mat_distance(self.mat, xz) <= self.vicinity_m and plausible_height(p, cam, xz):
+                cands.append((xz, k))
+        if not cands:
+            self._challenge_since = None
+            return Selection(None, None, {"A": [], "B": [], name: people})
+        xz, k = self._choose(t, cands)
+        self._last_pick_t = t
+        chosen = people[k]
+        sel = {"A": None, "B": None, name: chosen}
+        ignored = {"A": [], "B": [], name: [p for p in people if p is not chosen]}
+        return Selection(sel["A"], sel["B"], ignored, None, np.asarray(xz, float))
+
     def select(self, t: float, a_people: list[PoseResult], b_people: list[PoseResult]) -> Selection:
         a_people = [p for p in a_people if p is not None and p.detected]
         b_people = [p for p in b_people if p is not None and p.detected]
         ca, cb = self.cams.get("A"), self.cams.get("B")
-        if not (ca is not None and cb is not None and ca.calibrated and cb.calibrated):
+        calibrated = [n for n, c in (("A", ca), ("B", cb)) if c is not None and c.calibrated]
+        if not calibrated:
             a = a_people[0] if a_people else None
             b = b_people[0] if b_people else None
             return Selection(a, b, {"A": a_people[1:], "B": b_people[1:]})
+        if len(calibrated) == 1:  # one camera: people placed on the floor by their feet, closest to the centre
+            return self._select_one(t, calibrated[0], a_people if calibrated[0] == "A" else b_people)
         if self._pos is not None and t - self._last_pick_t > self.forget_s:
             self._pos, self._challenge_since = None, None  # gone too long: start afresh
 
```

Apply to `vision/tracker.py`:

```diff
--- a/vision/tracker.py
+++ b/vision/tracker.py
@@ -48,6 +48,8 @@
 LIMIT_PX_PER_RAD = 200.0
 FLOOR_PX_PER_M = 400.0
 ANCHOR_PX_PER_M = 200.0
+WORLD_PX_PER_M = 150.0  # one camera: pull toward MediaPipe's own 3D shape (the depth cue), px per metre
+CONTACT_PX_PER_M = 300.0  # one camera: the lowest foot point is pulled onto the floor
 
 
 def _unit(v, fallback=None):
@@ -106,7 +108,11 @@
     hand: dict  # side -> (3, 3) offsets from the wrist in the forearm frame
     foot: dict  # side -> (2, 3) heel, toe offsets from the ankle in the shin frame
 
+    def scaled(self, k: float) -> "BodyShape":
+        return BodyShape({n: v * k for n, v in self.lengths.items()}, self.face * k,
+                         {s: v * k for s, v in self.hand.items()}, {s: v * k for s, v in self.foot.items()})
 
+
 def segment_frames(J: np.ndarray) -> dict | None:
     """Rotation of each segment for joints J (None if a needed joint is missing)."""
     if not np.isfinite(J[NEEDED]).all():
@@ -247,8 +253,10 @@
 ITERATIONS = 3
 RECOVER_PX = 20.0  # a fit whose median 2D residual is above this also tries a fresh start from triangulation
 SMOOTH_MIN_CUTOFF = 0.7  # Hz: One Euro on the pose numbers (0 = off)
-SMOOTH_BETA = 6.0  # how much faster it follows when the pose changes quickly
-ROOT_MIN_CUTOFF = 3.0  # Hz: the body's position is pinned well by both cameras, so it is smoothed less
+SMOOTH_BETA = 6.0  # two cameras: how much faster it follows when the pose changes quickly
+ROOT_MIN_CUTOFF = 3.0  # Hz: two cameras pin the body's position well, so it is smoothed less
+SINGLE_BETA = 2.0  # one camera: noisier depth, so follow speed-ups more cautiously
+SINGLE_ROOT_MIN_CUTOFF = 1.0  # one camera: the floor position wobbles ~2 cm, smooth it more
 
 
 class _Cam:
@@ -270,9 +278,15 @@
 
 
 class ModelTracker:
-    def __init__(self, cams: dict[str, CameraConfig], frames_needed: int = FRAMES_NEEDED, max_nfev: int = 6):
+    def __init__(self, cams: dict[str, CameraConfig], frames_needed: int = FRAMES_NEEDED, max_nfev: int = 6,
+                 height_m: float | None = None):
+        """height_m: the person's height; with one camera it fixes the body's scale (MediaPipe's world
+        landmarks are only roughly metric)."""
         self.cams = {n: _Cam(c) for n, c in cams.items() if c is not None and c.calibrated}
         self.cfgs = cams
+        self.single = len(self.cams) == 1
+        self.height_m = height_m
+        self.world_scale = 1.0
         self.frames_needed, self.max_nfev = frames_needed, max_nfev
         self.reset()
 
@@ -284,18 +298,24 @@
         self.last_ms = 0.0
         self.last_cost = np.nan
         self.recoveries = 0
-        cutoff = np.r_[[ROOT_MIN_CUTOFF] * 3, [SMOOTH_MIN_CUTOFF] * (N_PARAMS - 3)]
-        self._smooth = OneEuroFilter(cutoff, SMOOTH_BETA) if SMOOTH_MIN_CUTOFF else None
+        root, beta = (SINGLE_ROOT_MIN_CUTOFF, SINGLE_BETA) if self.single else (ROOT_MIN_CUTOFF, SMOOTH_BETA)
+        cutoff = np.r_[[root] * 3, [SMOOTH_MIN_CUTOFF] * (N_PARAMS - 3)]
+        self._smooth = OneEuroFilter(cutoff, beta) if SMOOTH_MIN_CUTOFF else None
 
     @property
     def progress(self) -> float:
         return 1.0 if self.shape is not None else min(len(self._samples) / self.frames_needed, 1.0)
 
-    def _residuals(self, p, obs, prev, anchors):
+    def _residuals(self, p, obs, prev, anchors, world=None):
         X = forward(p, self.shape)
         parts = []
         for cam, uv, w in obs:
             parts.append(((cam.project(X) - uv) * w[:, None]).ravel())
+        if world is not None:  # one camera: MediaPipe's 3D shape, relative to the hips, is the depth cue
+            W, ww = world
+            rel = X - (X[23] + X[24]) / 2
+            parts.append((WORLD_PX_PER_M * (rel - W) * ww[:, None]).ravel())
+            parts.append(np.array([CONTACT_PX_PER_M * np.min(X[[29, 30, 31, 32], 1])]))
         parts.append((p - prev) / SIGMA * TEMPORAL_PX)
         lim = []
         for s in SIDE:
@@ -306,6 +326,20 @@
         for k, pos in anchors:
             parts.append(ANCHOR_PX_PER_M * (X[k] - pos))
         return np.concatenate(parts)
+
+    def _world(self, views):
+        """(hip-relative world shape scaled to the body, weights) from the one camera, or None."""
+        if not self.single:
+            return None
+        from vision.mono import world_shape
+
+        n = next(iter(self.cams))
+        v = views.get(n)
+        W = world_shape(v, self.cfgs[n]) if v is not None else None
+        if W is None:
+            return None
+        ww = np.where(v.vis >= 0.5, 1.0, 0.2) * np.where(np.isfinite(W).all(axis=1), 1.0, 0.0)
+        return np.nan_to_num(W * self.world_scale), ww
 
     def _median_residual(self, p, obs) -> float:
         """Median 2D distance (px) between the posed model and the detections it is fitted to."""
@@ -314,11 +348,11 @@
         d = np.concatenate(d) if d else np.empty(0)
         return float(np.median(d)) if d.size else 0.0
 
-    def _solve(self, p0, obs, prev, anchors, S: np.ndarray) -> tuple[np.ndarray, float]:
+    def _solve(self, p0, obs, prev, anchors, S: np.ndarray, world=None) -> tuple[np.ndarray, float]:
         """Robust (soft-L1) Levenberg-Marquardt from p0, Jacobian by grouped finite differences."""
         p = p0.copy()
         lam = 1e-3
-        r = self._residuals(p, obs, prev, anchors)
+        r = self._residuals(p, obs, prev, anchors, world)
 
         def robust(res):
             z = (res / ROBUST_PX) ** 2
@@ -331,14 +365,14 @@
             for g in FD_GROUPS:
                 dp = np.zeros(N_PARAMS)
                 dp[g] = h[g]
-                dr = (self._residuals(p + dp, obs, prev, anchors) - r)
+                dr = (self._residuals(p + dp, obs, prev, anchors, world) - r)
                 for j in g:
                     J[:, j] = np.where(S[:, j], dr / h[j], 0.0)
             Jw, rw = J * sw[:, None] * SIGMA, r * sw  # scaled parameters: steps measured in sigmas
             A, g_ = Jw.T @ Jw, Jw.T @ rw
             for _ in range(4):
                 step = -np.linalg.solve(A + lam * (np.diag(np.diag(A)) + 1e-6 * np.eye(N_PARAMS)), g_) * SIGMA
-                r_new = self._residuals(p + step, obs, prev, anchors)
+                r_new = self._residuals(p + step, obs, prev, anchors, world)
                 cost_new, sw_new = robust(r_new)
                 if cost_new < cost:
                     p, r, cost, sw = p + step, r_new, cost_new, sw_new
@@ -349,10 +383,14 @@
                 break
         return p, cost
 
-    def _sparsity(self, obs, anchors):
+    def _sparsity(self, obs, anchors, world=None):
         rows = []
         for _ in obs:
             rows.append(np.repeat(DEPENDS, 2, axis=0))
+        if world is not None:
+            rel = DEPENDS | DEPENDS[[23]] | DEPENDS[[24]]
+            rows.append(np.repeat(rel, 3, axis=0))
+            rows.append(DEPENDS[[29, 30, 31, 32]].any(axis=0, keepdims=True))
         rows.append(np.eye(N_PARAMS, dtype=bool))
         lim = np.zeros((4, N_PARAMS), bool)
         for i, s in enumerate(SIDE):
@@ -377,10 +415,17 @@
             return None
         self._last_seen = t
         if self.shape is None:
-            if tracking == "3d" and joints is not None and np.isfinite(joints[NEEDED]).all():
+            usable = tracking == "3d" or (self.single and tracking.startswith("single_cam"))
+            if usable and joints is not None and np.isfinite(joints[NEEDED]).all():
                 self._samples.append(np.array(joints, float))
                 if len(self._samples) >= self.frames_needed:
                     self.shape = measure(self._samples)
+                    if self.shape is not None and self.single and self.height_m:
+                        from vision.mono import stature
+
+                        est = float(np.median([stature(J - (J[23] + J[24]) / 2) for J in self._samples]))
+                        self.world_scale = self.height_m / est
+                        self.shape = self.shape.scaled(self.world_scale)
             if self.shape is None:
                 return None
         obs = []
@@ -406,13 +451,14 @@
         import time
 
         t0 = time.perf_counter()
-        S = self._sparsity(obs, anc).toarray().astype(bool)
-        x, cost = self._solve(prev, obs, prev, anc, S)
+        world = self._world(views)
+        S = self._sparsity(obs, anc, world).toarray().astype(bool)
+        x, cost = self._solve(prev, obs, prev, anc, S, world)
         # stuck in a wrong pose? restart from this frame's triangulated joints and keep the better fit
         if self._median_residual(x, obs) > RECOVER_PX:
             fresh = params_from_joints(np.asarray(joints, float), self.shape)
             if fresh is not None:
-                x2, _ = self._solve(fresh, obs, fresh, anc, S)
+                x2, _ = self._solve(fresh, obs, fresh, anc, S, world)
                 if self._median_residual(x2, obs) < self._median_residual(x, obs):
                     x = x2
                     self._smooth.reset() if self._smooth is not None else None
```

Apply to `vision/pipeline.py`:

```diff
--- a/vision/pipeline.py
+++ b/vision/pipeline.py
@@ -12,6 +12,7 @@
 from vision.frames import world_to_mat
 from vision.triangulate import Triangulation, triangulate_pair
 from vision.select import PersonSelector
+from vision.mono import mono_joints
 from vision.tracker import ModelTracker
 from vision.types import LEFT_HIP, RIGHT_HIP, CameraPeople, FrameResult, PoseResult, swap_left_right
 
@@ -91,11 +92,16 @@
 class Pipeline:
     def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
                  min_vis: float = 0.5, max_reproj_px: float = 15.0, mat: MatConfig | None = None,
-                 anchor_feet: bool = True, tracker: str = "triangulate"):
+                 anchor_feet: bool = True, tracker: str = "auto", height_m: float | None = None):
         """tracker: "model" fits a body to both cameras (falls back to triangulation while the body is
         being measured or when a fit fails); "triangulate" is the triangulate-and-clean pipeline."""
         self.cams = cams
-        self._model = ModelTracker(cams) if tracker == "model" else None
+        calibrated = [n for n, c in cams.items() if c is not None and c.calibrated]
+        self._single = calibrated[0] if len(calibrated) == 1 else None  # one camera: 3D from floor + MediaPipe
+        if tracker == "auto":
+            tracker = "model" if self._single else "triangulate"
+        self._model = ModelTracker(cams, height_m=height_m) if tracker == "model" else None
+        self._last_xz = None
         self._anchor = FootAnchor() if anchor_feet else None
         self._selector = PersonSelector(cams, mat or MatConfig())
         self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
@@ -131,8 +137,18 @@
                 cost = _labelling_cost(tri2, [a2, b2], self.cams)
                 if cost < best:
                     best, swapped, a, b, tri = cost, name, a2, b2, tri2
-        usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
-        joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
+        if self._single is not None:  # one camera: shape from MediaPipe's 3D, position from the feet on the floor
+            pose = {"A": a, "B": b}.get(self._single)
+            scale = self._model.world_scale if self._model is not None else 1.0
+            mono = mono_joints(pose, self.cams[self._single], scale, self._last_xz) if pose is not None else None
+            if mono is None:
+                joints, conf, tracking = np.full((33, 3), np.nan), np.zeros(33), "lost"
+            else:
+                joints, conf, tracking = mono, pose.vis.copy(), f"single_cam_{self._single}"
+                self._last_xz = ((mono[23] + mono[24]) / 2)[[0, 2]]
+        else:
+            usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
+            joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
         feet_locked = None
         if self._anchor is not None:
             if tracking == "lost":
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (218 passed).

- [ ] **Step 5: Commit**

```bash
git add vision/mono.py vision/tests/test_mono.py vision/select.py vision/tracker.py vision/pipeline.py vision/tests/test_select.py
git commit -m "vision: one-camera 3D (floor placement, one-camera selection and model fit)"
```

---

### Task 3: One-camera tools: checks, debug view, calib_extrinsics, replay

**Files:**
- Modify: `vision/checks.py`, `vision/replay.py`, `vision/debug_view.py`, `vision/calib_extrinsics.py`
- Modify: `vision/tests/test_checks.py`, `vision/tests/test_debug_view.py`, `vision/tests/test_calib_extrinsics.py`

**Interfaces:**
- Consumes: `height_from` (Task 1), `Pipeline(height_m=, tracker="auto")` (Task 2).
- Produces: `checks.single_camera(cams) -> name | None` and the one-camera check set (frames tracked, body fit to the camera, jitter while still, knee angles, foot height, standing straight, estimated height, fit time, fps, pose time, camera not moved); `outputs_from_recording(path, cams, height_cm=None)`; `_live_outputs(..., height_cm=None)`.
- Produces: `replay_outputs(..., tracker="auto", height_m=None)`; `debug_view --tracker auto|triangulate|model` (default auto) and `--height-cm`; "not used (one-camera setup)" panel; `calib_extrinsics.pad_tiles(tiles)`, `lock_problems/save_problems(results, names=NAMES)`, works with the configured cameras only.

- [ ] **Step 1: Write the failing tests**

Apply to `vision/tests/test_checks.py`:

```diff
--- a/vision/tests/test_checks.py
+++ b/vision/tests/test_checks.py
@@ -129,3 +129,20 @@
     outs = [pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0) for k in range(20)]
     c = {c.name: c for c in run_checks(outs, cams, MAT)}["standing straight"]
     assert c.passed is True and c.unit == "°"
+
+
+def test_one_camera_checks_skip_two_camera_ones():
+    from vision.tests.test_mono import _cam_b
+    from vision.mono import stature
+
+    cam, X = _cam_b(), standing_skeleton()
+    h = stature(X - (X[23] + X[24]) / 2)
+    pipe = Pipeline({"B": cam}, mat=MAT, height_m=h)
+    rng = np.random.default_rng(0)
+    outs = [pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30, noise_px=0.5, rng=rng)) for k in range(90)]
+    checks = {c.name: c for c in run_checks(outs, {"B": cam}, MAT, height_cm=h * 100)}
+    assert {"frames tracked", "body fit to the camera", "jitter while still", "standing straight",
+            "estimated height", "fit time"} <= set(checks)
+    assert not {"median triangulation angle", "pair skew p95", "thigh_l length std"} & set(checks)
+    for name in ("frames tracked", "body fit to the camera", "jitter while still", "estimated height"):
+        assert checks[name].passed is True, (name, checks[name].value)
```

Apply to `vision/tests/test_debug_view.py`:

```diff
--- a/vision/tests/test_debug_view.py
+++ b/vision/tests/test_debug_view.py
@@ -151,3 +151,11 @@
             rec.write(pipe.process(1000 + k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0))
     outs = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, tracker="model"))
     assert outs[-1].tracker == "model"
+
+
+def test_one_camera_setup_says_the_other_panel_is_unused():
+    cams = {"B": target_cams()["B"]}
+    one = compose_view(cams, MatConfig(), {}, {}, None, None, None, None)
+    two = compose_view(target_cams(), MatConfig(), {}, {}, None, None, None, None)
+    assert one.shape == two.shape == (720, 1280, 3)
+    assert (one[:360, :640] != two[:360, :640]).any()  # "not used (one-camera setup)" instead of camera A
```

Apply to `vision/tests/test_calib_extrinsics.py`:

```diff
--- a/vision/tests/test_calib_extrinsics.py
+++ b/vision/tests/test_calib_extrinsics.py
@@ -34,3 +34,13 @@
     assert all(len(t) <= 66 for t, _ in ready)
     blocked = "\n".join(t for t, _ in legend_lines(["camera B has no marker solution"], ["camera B has no marker solution"]))
     assert "not yet" in blocked and "camera B" in blocked
+
+
+def test_one_camera_setup_saves_and_pads_the_layout():
+    from vision.calib_extrinsics import pad_tiles
+
+    assert save_problems({"B": _result()}, ("B",)) == []
+    assert lock_problems({"B": _result()}, ("B",)) == []
+    assert save_problems({}, ("B",))
+    tiles = pad_tiles([np.zeros((360, 640, 3), np.uint8)])
+    assert len(tiles) == 2 and np.hstack(tiles).shape == (360, 1280, 3)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest vision/tests/test_checks.py vision/tests/test_debug_view.py vision/tests/test_calib_extrinsics.py -q`
Expected: 3 FAIL — the one-camera checks test (missing check names, e.g. 'frames tracked'), the debug-view panel test (the one-camera view still says "not configured", so the panels may still differ — if it passes, note it and continue), and the calib_extrinsics test (`ImportError: cannot import name 'pad_tiles'`).

- [ ] **Step 3: Implement**

Apply to `vision/checks.py`:

```diff
--- a/vision/checks.py
+++ b/vision/checks.py
@@ -15,7 +15,8 @@
 import numpy as np
 
 from vision.anchor import FootAnchor
-from vision.config import CameraConfig, MatConfig, load_cameras, load_mat
+from vision.config import CameraConfig, MatConfig, height_from, load_cameras, load_mat
+from vision.frames import project
 from vision.pipeline import Pipeline, PipelineOutput
 from vision.triangulate import triangulate_points
 
@@ -122,6 +123,8 @@
                pose_ms: dict[str, float] | None = None) -> list[Check]:
     """`moved`: camera name -> (MoveMonitor status, shift px); `pose_ms`: camera name -> pose time per
     frame (ms); both from a live run."""
+    if single_camera(cams):
+        return _single_camera_checks(outputs, cams, fps, height_cm, moved, pose_ms)
     checks = []
     n3d = sum(o.frame.tracking == "3d" for o in outputs)
     frac = 100.0 * n3d / len(outputs) if outputs else None
@@ -177,6 +180,60 @@
     return checks
 
 
+def single_camera(cams: dict[str, CameraConfig]) -> str | None:
+    """The camera's name when exactly one camera is calibrated (one-camera setup)."""
+    names = [n for n, c in cams.items() if c is not None and c.calibrated]
+    return names[0] if len(names) == 1 else None
+
+
+def _single_camera_checks(outputs, cams, fps, height_cm, moved, pose_ms) -> list[Check]:
+    """One camera: no triangulation, so check the fitted body against the camera and for steadiness."""
+    name = single_camera(cams)
+    cam = cams[name]
+    checks = []
+    tracked = [o for o in outputs if o.frame.tracking != "lost"]
+    frac = 100.0 * len(tracked) / len(outputs) if outputs else None
+    checks.append(_check("frames tracked", frac, "%", "≥ 90", lambda v: v >= 90))
+    model = [o for o in tracked if o.tracker == "model"]
+    res = []
+    for o in model:
+        v = o.raw.get(name)
+        if v is not None and v.detected:
+            uv = project(o.frame.joints, cam.K, cam.dist, cam.R, cam.t)
+            ok = (v.vis >= 0.5) & np.isfinite(uv).all(axis=1)
+            res.extend(np.linalg.norm(uv[ok] - v.lm2d[ok], axis=1))
+    checks.append(_check("body fit to the camera", float(np.median(res)) if res else None, "px", "≤ 10",
+                         lambda v: v <= 10))
+    seq = np.stack([o.frame.joints for o in model]) if model else None
+    jitter = (float(np.nanmedian(np.linalg.norm(np.diff(seq, axis=0), axis=2))) * 100
+              if seq is not None and len(seq) > 1 else None)
+    checks.append(_check("jitter while still", jitter, "cm", "< 1.0", lambda v: v < 1.0))
+    for side in ("l", "r"):
+        vals = [knee_angle_deg(j, side) for j in seq] if seq is not None else []
+        vals = [v for v in vals if np.isfinite(v)]
+        checks.append(_check(f"standing knee angle {side}", float(np.median(vals)) if vals else None, "°", "170–180",
+                             lambda v: 170 <= v <= 180))
+    foot_h = foot_height_cm(seq) if seq is not None else None
+    checks.append(_check("foot height above mat", foot_h, "cm", "-1 to 5", lambda v: -1 <= v <= 5))
+    tilt = body_tilt_deg(seq) if seq is not None else None
+    checks.append(_check("standing straight", tilt, "°", "≤ 3 (else re-run calib_extrinsics)", lambda v: v <= 3))
+    if height_cm is not None:
+        est = estimate_height_cm(seq) if seq is not None else None
+        checks.append(_check("estimated height", est, "cm", f"{height_cm:.0f} ± 5%",
+                             lambda v: abs(v / height_cm - 1) <= 0.05))
+    fits = [o.fit_ms for o in model if o.fit_ms is not None]
+    checks.append(_check("fit time", float(np.median(fits)) if fits else None, "ms", "≤ 8", lambda v: v <= 8))
+    for n, v in sorted((fps or {}).items()):
+        checks.append(_check(f"capture fps {n}", v, "fps", "≥ 20", lambda x: x >= 20))
+    for n, v in sorted((pose_ms or {}).items()):
+        checks.append(_check(f"pose time {n}", v, "ms", "≤ 45", lambda x: x <= 45))
+    for n, (status, shift) in sorted((moved or {}).items()):
+        value = shift if status in ("ok", "moved") else None
+        checks.append(Check(f"camera {n} not moved", value, "px", "< 3",
+                            None if value is None else status != "moved"))
+    return checks
+
+
 def format_checks(checks: list[Check]) -> str:
     lines = [f"{'check':32s} {'value':>12s}  {'target':14s} result"]
     for c in checks:
@@ -186,16 +243,21 @@
     return "\n".join(lines)
 
 
-def outputs_from_recording(path, cams: dict[str, CameraConfig]) -> list[PipelineOutput]:
-    """Re-triangulate a recording with the current calibration, unsmoothed and unanchored so jitter and
-    floor errors stay visible."""
+def outputs_from_recording(path, cams: dict[str, CameraConfig], height_cm: float | None = None
+                           ) -> list[PipelineOutput]:
+    """Re-run a recording with the current calibration: two cameras unsmoothed and unanchored so jitter and
+    floor errors stay visible; one camera as the team gets it (the fitted body)."""
     from vision.replay import replay_outputs
 
+    if single_camera(cams):
+        return list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False,
+                                   height_m=None if height_cm is None else height_cm / 100))
     return list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False,
                                anchor_feet=False))
 
 
-def _live_outputs(cams, mat, model: str, seconds: float, num_poses: int | None = None):
+def _live_outputs(cams, mat, model: str, seconds: float, num_poses: int | None = None,
+                  height_cm: float | None = None):
     from vision.capture import mono_to_unix
     from vision.live import LiveSource
     from vision.pose import NUM_POSES
@@ -204,8 +266,10 @@
 
     outputs = []
     monitor = MoveMonitor(cams, MarkerDetector(mat), interval_s=0.25)
-    # smoothing and foot anchoring would hide the jitter and floor errors these checks measure
-    pipeline = Pipeline(cams, smoothing=False, mat=mat, anchor_feet=False)
+    if single_camera(cams):  # one camera: check the fitted body the team gets
+        pipeline = Pipeline(cams, mat=mat, height_m=None if height_cm is None else height_cm / 100)
+    else:  # smoothing and foot anchoring would hide the jitter and floor errors these checks measure
+        pipeline = Pipeline(cams, smoothing=False, mat=mat, anchor_feet=False)
     with LiveSource(cams, model, mat, num_poses=NUM_POSES if num_poses is None else num_poses) as src:
         problems = src.wait_ready()
         if problems:
@@ -237,11 +301,12 @@
                    help="people pose looks for per camera, live (default vision.pose.NUM_POSES)")
     args = p.parse_args()
     cams, mat = load_cameras(), load_mat()
+    height_cm = height_from(args.height_cm)  # remembered in config/person.yaml
     if args.from_path:
-        outputs, fps, moved, pose_ms = outputs_from_recording(args.from_path, cams), None, None, None
+        outputs, fps, moved, pose_ms = outputs_from_recording(args.from_path, cams, height_cm), None, None, None
     else:
-        outputs, fps, moved, pose_ms = _live_outputs(cams, mat, args.model, args.seconds, args.poses)
-    checks = run_checks(outputs, cams, mat, fps, args.height_cm, moved, pose_ms)
+        outputs, fps, moved, pose_ms = _live_outputs(cams, mat, args.model, args.seconds, args.poses, height_cm)
+    checks = run_checks(outputs, cams, mat, fps, height_cm, moved, pose_ms)
     print(format_checks(checks))
     return 0 if all(c.passed is not False for c in checks) else 1
 
```

Apply to `vision/replay.py`:

```diff
--- a/vision/replay.py
+++ b/vision/replay.py
@@ -53,11 +53,12 @@
 
 def replay_outputs(path, retriangulate: bool = False, cams: dict[str, CameraConfig] | None = None,
                    realtime: bool = True, speed: float = 1.0, smoothing: bool = True,
-                   anchor_feet: bool = True, tracker: str = "triangulate") -> Iterator[PipelineOutput]:
+                   anchor_feet: bool = True, tracker: str = "auto", height_m: float | None = None
+                   ) -> Iterator[PipelineOutput]:
     pipeline = None
     if retriangulate:
         pipeline = Pipeline(cams if cams is not None else load_cameras(), smoothing=smoothing, mat=load_mat(),
-                            anchor_feet=anchor_feet, tracker=tracker)
+                            anchor_feet=anchor_feet, tracker=tracker, height_m=height_m)
     start_t = start_wall = None
     for frame, raw, skew, people in iter_records(path):
         if realtime:
```

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -3,7 +3,7 @@
     python -m vision.debug_view                   # live cameras
     python -m vision.debug_view --replay FILE     # a recording (add --retriangulate for current calibration)
     python -m vision.debug_view --poses 1         # live, pose looking for one person per camera
-    python -m vision.debug_view --tracker model   # trial: fit a body model to both cameras
+    python -m vision.debug_view --height-cm 175   # one camera: your height, saved for next time
 
 Top: camera feeds with MediaPipe 2D (green) and the 3D result projected back (magenta).
 Bottom: top-down view of the mat and a low-poly 3D person (v switches to a flat front view);
@@ -22,7 +22,7 @@
 
 from vision.avatar import AvatarView, render_avatar
 from vision.capture import mono_to_unix
-from vision.config import CameraConfig, MatConfig, load_cameras, load_mat, mat_raw
+from vision.config import CameraConfig, MatConfig, height_from, load_cameras, load_mat, mat_raw
 from vision.draw import GREEN, MAGENTA, RED, WHITE, YELLOW, draw_points, draw_skeleton, draw_text, fit, pt
 from vision.frames import cam_center, project
 from vision.markers import MarkerDetector
@@ -193,7 +193,9 @@
             if msg:
                 draw_text(panel, msg, (8, y), RED, 0.45)
                 y += 18
-        if cam is None:
+        if cam is None and len(cams) == 1:
+            draw_text(panel, "not used (one-camera setup)", (8, y), WHITE, 0.45)
+        elif cam is None:
             draw_text(panel, "not configured: run python -m vision.cams", (8, y), RED, 0.45)
         elif not cam.calibrated:
             draw_text(panel, "not calibrated (2D only)", (8, y), YELLOW, 0.45)
@@ -244,14 +246,14 @@
 
 
 def run_live(cams: dict[str, CameraConfig], mat: MatConfig, model: str, num_poses: int | None = None,
-             tracker: str = "triangulate") -> None:
+             tracker: str = "auto", height_m: float | None = None) -> None:
     from vision.live import LiveSource  # imports MediaPipe; keep replay usable without it
     from vision.pose import NUM_POSES
 
     num_poses = NUM_POSES if num_poses is None else num_poses
 
     detector, show_markers = MarkerDetector(mat), False
-    pipeline = Pipeline(cams, mat=mat, tracker=tracker)
+    pipeline = Pipeline(cams, mat=mat, tracker=tracker, height_m=height_m)
     monitor = MoveMonitor(cams, detector)
     recorder: Recorder | None = None
     last: PipelineOutput | None = None
@@ -299,14 +301,14 @@
 
 
 def run_replay(path: Path, cams: dict[str, CameraConfig], mat: MatConfig, retriangulate: bool, speed: float,
-               tracker: str = "triangulate") -> None:
+               tracker: str = "auto", height_m: float | None = None) -> None:
     if tracker == "model" and not retriangulate:  # re-run the recording with its own calibration
         cams, retriangulate = cams_from_header(read_header(path)), True
     view_cams = cams if retriangulate else cams_from_header(read_header(path))
     view = _open_window()
     try:
         for out in replay_outputs(path, retriangulate=retriangulate, cams=cams, realtime=True, speed=speed,
-                                  tracker=tracker):
+                                  tracker=tracker, height_m=height_m):
             img = compose_view(view_cams, mat, {}, out.raw, out, None, None, f"replay {path.name}", view=view)
             cv2.imshow(WINDOW, img)
             key = cv2.waitKey(1) & 0xFF
@@ -323,16 +325,21 @@
     p.add_argument("--retriangulate", action="store_true")
     p.add_argument("--speed", type=float, default=1.0)
     p.add_argument("--model", default="full", choices=["full", "heavy"])
-    p.add_argument("--tracker", default="triangulate", choices=["triangulate", "model"],
-                   help="model: fit a body to both cameras (trial); triangulate: today's pipeline")
+    p.add_argument("--tracker", default="auto", choices=["auto", "triangulate", "model"],
+                   help="auto: the body model with one camera, triangulation with two; or force one")
+    p.add_argument("--height-cm", type=float, help="your height (saved in config/person.yaml); one camera needs it")
     p.add_argument("--poses", type=int, default=None,
                    help="people pose looks for per camera, live (default vision.pose.NUM_POSES)")
     args = p.parse_args()
     cams, mat = load_cameras(), load_mat()
+    h = height_from(args.height_cm)
+    height_m = None if h is None else h / 100
+    if len(cams) == 1 and height_m is None:
+        print("one-camera setup: pass --height-cm once (e.g. --height-cm 175) so the body has the right scale")
     if args.replay:
-        run_replay(args.replay, cams, mat, args.retriangulate, args.speed, args.tracker)
+        run_replay(args.replay, cams, mat, args.retriangulate, args.speed, args.tracker, height_m)
     else:
-        run_live(cams, mat, args.model, args.poses, args.tracker)
+        run_live(cams, mat, args.model, args.poses, args.tracker, height_m)
 
 
 if __name__ == "__main__":
```

Apply to `vision/calib_extrinsics.py`:

```diff
--- a/vision/calib_extrinsics.py
+++ b/vision/calib_extrinsics.py
@@ -36,9 +36,9 @@
 NAMES = ("A", "B")
 
 
-def _need_both(results: dict[str, tuple]) -> list[str]:
+def _need_both(results: dict[str, tuple], names=NAMES) -> list[str]:
     problems = []
-    for n in NAMES:
+    for n in names:
         if n not in results:
             problems.append(f"camera {n} has no marker solution (it needs to see at least 2 markers)")
             continue
@@ -46,14 +46,14 @@
     return problems
 
 
-def lock_problems(results: dict[str, tuple]) -> list[str]:
+def lock_problems(results: dict[str, tuple], names=NAMES) -> list[str]:
     """Why the current spots can't be locked: a missing camera or a nonsense pose (wrong marker IDs)."""
-    return _need_both(results)
+    return _need_both(results, names)
 
 
-def save_problems(results: dict[str, tuple]) -> list[str]:
+def save_problems(results: dict[str, tuple], names=NAMES) -> list[str]:
     """Why calibration can't be saved. A rough or assumed zoom is fine: calib_refine fine-tunes it."""
-    return _need_both(results)
+    return _need_both(results, names)
 
 
 GREY = (150, 150, 150)
@@ -81,6 +81,16 @@
     ]
 
 
+def pad_tiles(tiles: list[np.ndarray]) -> list[np.ndarray]:
+    """Two tiles across, so the layout is the same with one camera (the other slot says why it's empty)."""
+    tiles = list(tiles)
+    while len(tiles) < 2:
+        blank = np.full((TILE[1], TILE[0], 3), 32, np.uint8)
+        draw_text(blank, "one-camera setup", (10, 24), WHITE, 0.6)
+        tiles.append(blank)
+    return tiles
+
+
 def _status_panel(lines: list[tuple[str, tuple]]) -> np.ndarray:
     img = np.full((TILE[1], TILE[0], 3), 32, np.uint8)
     for k, (text, color) in enumerate(lines):
@@ -93,13 +103,13 @@
     p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
     args = p.parse_args()
     cams, mat = load_cameras(), load_mat()
-    missing = [n for n in NAMES if n not in cams]
-    if missing:
-        print(f"camera(s) {missing} are not configured: run python -m vision.cams first")
+    names = tuple(n for n in NAMES if n in cams)  # one camera is fine
+    if not names:
+        print("no camera is configured: run python -m vision.cams first")
         return 1
 
     detector = MarkerDetector(mat)
-    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in NAMES}
+    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in names}
     for c in cameras.values():
         c.start()
     history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
@@ -134,7 +144,7 @@
                                               mat.tol_position_m, mat.tol_angle_deg)
                     results[n] = (sol, obs, hint, warns, fit_report(sol.K, sol.R, sol.t, size, mat))
             centers = {n: cam_center(r[0].R, r[0].t) for n, r in results.items() if not r[3]}
-            locked = {n: cams[n].target_eye for n in NAMES if cams[n].target_eye is not None}
+            locked = {n: cams[n].target_eye for n in names if cams[n].target_eye is not None}
             stale = targets_stale(centers, locked)
 
             tiles = []
@@ -178,7 +188,7 @@
                 tiles.append(tile)
 
             map_cams = {}
-            for n in NAMES:
+            for n in names:
                 v = {"suggested": None if n in locked else mat.camera_targets.get(n), "target": locked.get(n)}
                 if n in results and not results[n][3]:
                     sol = results[n][0]
@@ -192,7 +202,7 @@
                 status.append(("locked spots are from another setup: press t to lock these", ORANGE))
             elif not locked:
                 status.append(("no spots locked: press t to remember these spots", WHITE))
-            status += [("", WHITE)] + legend_lines(save_problems(results), lock_problems(results)) + [note]
+            status += [("", WHITE)] + legend_lines(save_problems(results, names), lock_problems(results, names)) + [note]
             bottom = np.hstack([render_map(mat, map_cams, TILE), _status_panel(status)])
 
             if now - last_print > 1.0:
@@ -202,30 +212,30 @@
                     print(f"{n}: markers {sorted(obs)} rms {sol.rms:.2f}px {sol.zoom_msg} at "
                           f"({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f})" + (f" -> {hint['msg']}" if hint else "")
                           + "".join(f" | {m}" for m in warns + fits))
-            cv2.imshow("Mira extrinsics", np.vstack([np.hstack(tiles), bottom]))
+            cv2.imshow("Mira extrinsics", np.vstack([np.hstack(pad_tiles(tiles)), bottom]))
             key = cv2.waitKey(1) & 0xFF
             if key in (ord("q"), 27):
                 return 0
             if key == ord("t"):
-                problems_ = lock_problems(results)
+                problems_ = lock_problems(results, names)
                 if problems_:
                     note = ("cannot lock: " + problems_[0], RED)
                     print("cannot lock spots: " + "; ".join(problems_))
                     continue
-                for n in NAMES:
+                for n in names:
                     sol = results[n][0]
                     cams[n].target_eye, cams[n].target_aim = lock_in_place(sol.R, sol.t, mat.aim_point)
                 save_cameras(cams)
                 note = ("spots locked", GREEN)
                 print("locked camera spots: " + ", ".join(f"{n} {np.round(cams[n].target_eye, 2).tolist()}"
-                                                          for n in NAMES))
+                                                          for n in names))
             if key == ord("s"):
-                problems_ = save_problems(results)
+                problems_ = save_problems(results, names)
                 if problems_:
                     note = ("cannot save: " + problems_[0], RED)
                     print("cannot save: " + "; ".join(problems_))
                     continue
-                for n in NAMES:
+                for n in names:
                     sol, obs, _, _, _ = results[n]
                     cfg = cams[n]
                     cfg.K, cfg.dist, cfg.zoom_std, cfg.R, cfg.t = sol.K, np.zeros(5), sol.zoom_std, sol.R, sol.t
```

- [ ] **Step 4: Run the whole suite and import every tool**

Run: `.venv/bin/python -m pytest -q && for m in debug_view calib_extrinsics calib_refine checks cams live; do .venv/bin/python -c "import vision.$m" || echo FAIL $m; done`
Expected: PASS (221 passed), no `FAIL` lines.

- [ ] **Step 5: Commit**

```bash
git add vision/checks.py vision/replay.py vision/debug_view.py vision/calib_extrinsics.py vision/tests/test_checks.py vision/tests/test_debug_view.py vision/tests/test_calib_extrinsics.py
git commit -m "vision: one-camera checks, debug view, calib_extrinsics and replay"
```

---

### Task 4: README and the B-only comparison tool

**Files:**
- Modify: `vision/README.md`
- Create: `vision/mono_eval.py`

**Interfaces:**
- Consumes: Tasks 1–3.

- [ ] **Step 1: Add the tool**

Create `vision/mono_eval.py`:

```python
"""Camera-B-only replays of two-camera recordings, compared with the two-camera triangulation.

    python -m vision.mono_eval data/recordings/<file>.jsonl [...] [--height-cm H]
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from vision.mono import stature


def run(path, only: str | None, height_m=None):
    from vision.config import load_mat
    from vision.pipeline import Pipeline
    from vision.replay import cams_from_header, iter_records, read_header

    cams = cams_from_header(read_header(path))
    if only:
        cams = {only: cams[only]}
    pipe = Pipeline(cams, mat=load_mat(), height_m=height_m)
    outs, ms = [], []
    for frame, raw, skew, people in iter_records(path):
        get = (lambda n: people.get(n, [])) if people is not None else (lambda n: raw.get(n))
        a = get("A") if "A" in cams else None
        b = get("B") if "B" in cams else None
        t0 = time.perf_counter()
        outs.append(pipe.process(frame.t, a, b, skew))
        ms.append((time.perf_counter() - t0) * 1000)
    return outs, ms


def reference_height(tri_outs) -> float:
    hs = []
    for o in tri_outs:
        if o.tri is None or not o.tri.ok[[2, 5, 23, 24, 27, 28, 29, 30, 31, 32]].all():
            continue
        J = o.tri.points
        rel = J - (J[23] + J[24]) / 2
        u, v = J[23] - J[25], J[27] - J[25]
        if np.degrees(np.arccos(np.clip(u @ v / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))) > 165:
            hs.append(stature(rel))
    return float(np.median(hs)) if hs else 1.75


def compare(path, height_cm=None) -> str:
    tri, _ = run(path, None)
    h = height_cm / 100 if height_cm else reference_height(tri)
    mono, ms = run(path, "B", h)
    seen = [o.raw.get("B") is not None for o in mono]
    present = [np.isfinite(o.frame.joints[[23, 24]]).all() for o in mono]
    model = np.array([o.tracker == "model" for o in mono])
    J = np.array([o.frame.joints for o in mono])
    bones = [np.nanstd(np.linalg.norm(J[model][:, a] - J[model][:, b], axis=1)) * 1000
             for a, b in ((23, 25), (25, 27), (11, 13), (13, 15))] if model.any() else [np.nan]
    err = []
    for o_t, o_m in zip(tri, mono):
        if o_t.tri is not None and o_t.tri.ok[[23, 24]].all() and np.isfinite(o_m.frame.joints[[23, 24]]).all():
            ref = (o_t.tri.points[23] + o_t.tri.points[24]) / 2
            err.append(np.linalg.norm(((o_m.frame.joints[23] + o_m.frame.joints[24]) / 2 - ref)[[0, 2]]))
    hip = np.array([(o.tri.points[23] + o.tri.points[24]) / 2 if o.tri is not None and o.tri.ok[[23, 24]].all()
                    else [np.nan] * 3 for o in tri])
    v = np.linalg.norm(np.diff(hip, axis=0), axis=1)
    still = np.zeros(len(hip), bool)
    for i in range(len(v) - 10):
        if np.all(v[i:i + 10] < 0.01):
            still[i:i + 11] = True
    m = still[1:] & still[:-1]
    jit = np.nanmedian(np.linalg.norm(np.diff(J, axis=0), axis=2)[m]) * 100 if m.any() else np.nan
    est = []
    for o in mono:
        Jm = o.frame.joints
        if o.tracker == "model" and np.isfinite(Jm).all():
            est.append(stature(Jm - (Jm[23] + Jm[24]) / 2))
    e = np.array(err) * 100
    fits = [o.fit_ms for o in mono if o.fit_ms is not None]
    return (f"== {path.split('/')[-1]}: B sees someone {100 * np.mean(seen):.0f}%, body present when seen "
            f"{100 * np.mean([p for p, s in zip(present, seen) if s]):.0f}%, model {100 * model.mean():.0f}%\n"
            f"   hip position vs two-camera: median {np.median(e):.1f} cm, p90 {np.percentile(e, 90):.1f} cm (n {len(e)})\n"
            f"   bones std {np.nanmax(bones):.2f} mm, still jitter {jit:.2f} cm, fit {np.median(fits):.1f} ms "
            f"(p95 {np.percentile(fits, 95):.1f}), height {np.median(est) * 100:.0f} cm vs {h * 100:.0f} cm")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    p.add_argument("--height-cm", type=float)
    a = p.parse_args()
    for path in a.paths:
        print(compare(path, a.height_cm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Document the one-camera setup**

Apply to `vision/README.md`:

~~~diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -24,6 +24,27 @@
 down, printed top edge toward the back of the mat), set `marker_rotation_deg` in `config/mat.yaml`
 (0/90/180/270, clockwise seen from above with Mira at the top). The rig currently uses 180.
 
+## One-camera setup (current rig: camera B only)
+
+The rig now uses one webcam (B, front-diagonal ~45° to the mat). 3D comes from the floor and MediaPipe:
+rays through the planted foot meet the calibrated floor (where you stand, in metres), MediaPipe's own 3D
+estimate gives the body's shape and depth, and the body model (`vision/tracker.py`) is fitted to B's 2D
+points with that depth cue, feet on the floor and smooth motion. Your height sets the scale — enter it
+accurately once (a 3% error shifts the body ~6 cm in depth):
+
+```
+python -m vision.cams --remove A              # drop camera A from the config (or assign only B)
+python -m vision.calib_extrinsics             # B and the markers: the whole calibration
+python -m vision.debug_view --height-cm 175   # your height, saved in config/person.yaml
+python -m vision.checks                       # one-camera checks (uses the saved height)
+python -m vision.mono_eval FILE...            # B-only replay of a two-camera recording vs triangulation
+```
+
+Place B so it sees your whole body anywhere on the mat (head to feet) and at least two markers, ideally one
+at each end. `calib_refine` needs two cameras and says so. Limits: depth along B's line of sight is the weak
+direction (a few cm typically, ~15–35 cm in the worst 10% of frames); the side of the body facing away from
+B is partly estimated by MediaPipe.
+
 ## Workflow
 
 No lens calibration: each camera's zoom comes from the floor markers. These webcams (~48° wide) need
~~~

- [ ] **Step 3: Run the suite and the tool**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -c "import vision.mono_eval"`
Expected: PASS (221 passed). If recordings exist: `.venv/bin/python -m vision.mono_eval data/recordings/20261011-022822.jsonl --height-cm 165` prints a selected rate near 100% and a hip position median under 10 cm.

- [ ] **Step 4: Commit**

```bash
git add vision/README.md vision/mono_eval.py
git commit -m "vision: README for the one-camera setup; mono_eval"
```

---

