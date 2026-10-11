# One camera: a steadier body — Implementation Plan

**Goal:** Stop the one-camera body popping and shaking, with no real added lag, and tell the app when the body
can be trusted (`measuring` / `ready`). Two-camera behaviour stays identical.

**Spec:** `docs/superpowers/specs/2026-10-11-vision-one-camera-stability-design.md`, including its addendum
(decisions made while testing this plan's code).

**Architecture:** Four changes, all in one-camera mode:
- **Measure from standing frames.** `ModelTracker` measures body shape, scale and lean only from frames that
  pass `mono.is_standing`.
- **Feet report, don't pull.** The pipeline passes no foot anchors to the one-camera fit. `FootAnchor` runs on
  the fitted output only to report planted feet.
- **Gate inside the solve.** The 2D-motion gate becomes a per-number weight on the smooth-motion term inside
  the solve (`ModelTracker._hold`), replacing the post-fit blend (`_gate`). The angles' smooth-motion cost
  (`SINGLE_TEMPORAL_PX`) and the floor contact (`CONTACT_PX_PER_M`) are stronger.
- **Body state.** `FrameResult` carries `body` and `body_progress`.

**Tech stack:** Python 3.11, NumPy, pytest. No new dependencies.

**How this plan was checked:** All code below was run in a scratch copy of the branch at `4836bd5`.
- Task by task in this order, each task's new tests failed before its code and passed after.
- Totals: 236 tests before, 245 after.
- The numbers below are from that code.

## Measured (`python -m vision.stability_eval … --height-cm 170`)

| Session | Still p90 (cm) | Moving p90 (cm) | Jumps > 8 cm | Ankle p99 (cm) | Floating | 2D match (px) | Lag (frames) |
|---|---|---|---|---|---|---|---|
| `060807` base | 1.70 | 3.54 | 128 | 10.0 | 3 | 8.2 | 1.40 |
| `060807` after | **0.56** | **2.32** | **54** | **6.6** | **0** | **7.1** | 1.70 |
| `054226` base | n/a | 7.40 | 125 | 16.0 | 0 | 10.5 | 1.80 |
| `054226` after | n/a | **2.90** | **18** | **4.8** | 0 | **9.1** | 1.90 |
| `055039` base | 9.09 | 14.35 | 482 | 55.1 | 12 | 18.8 | 1.55 |
| `055039` after | **0.79** | **2.31** | **45** | **3.9** | **0** | **9.2** | 1.60 |

Also, after:
- **Restarts:** 0 in all three sessions; `055039` had 25 before.
- **Fit time:** 5.9–6.0 ms, up from 5.5–5.6.
- **Deepest knee:** 30° / 43° / 71°, against 33° / 42° / 39° before. `055039` reads much shallower now that its
  scale is right; reported, no target.
- **Time to first fit:** the fitted share is 93 / 91 / 90%, against 97 / 92 / 94% before, because measuring now
  waits for standing frames. The rough floor-placed body is shown meanwhile.
- **Lag:** +0.30 / +0.10 / +0.05 frames (≤ 10 ms); spec addendum, decision 1.
- **Two cameras:** recording `20261011-022822` gives identical frames before and after, in both triangulation
  and `--tracker model`.

## Global constraints

- Two-camera mode unchanged: every new term, weight and rule is behind `self.single` (tracker) or
  `single_model` (pipeline).
- **One-camera constants:**

  | Constant | Value | Notes |
  |---|---|---|
  | `GATE_FULL_PX` | 8.0 | unchanged |
  | `GATE_MIN_GAIN` | 0.08 | unchanged |
  | `GATE_ROOT_MIN` | 0.3 | new; replaces `GATE_PASS_M` |
  | `GATE_POWER` | 0.75 | new |
  | `SINGLE_TEMPORAL_PX` | 8.0 | new; angles only, the position keeps `TEMPORAL_PX` = 3.0 |
  | `CONTACT_PX_PER_M` | 600.0 | was 300.0; used only with one camera |

- **Measuring:** 40 frames (`FRAMES_NEEDED`) passing `is_standing`: both knees > 150° and
  shoulders–hips–knees > 160°.
- **Running tests:** `.venv/bin/python -m pytest -q` from the repo root.
- **Commits:** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit
  `config/cameras.yaml`, `config/person.yaml` or `data/`.

## Review focus (for the one final review)

1. **No anchor reaches the one-camera fit,** and `feet_locked` still lights up for planted feet. Two-camera
   anchoring is unchanged.
2. **The hold:**
   - recovery solves and the first frame after a gap run without it;
   - the gap reset happens before the solve;
   - nothing still reads `_gate_out`.
3. **Measuring:**
   - `is_standing` is the only gate on one-camera samples;
   - the scale and lean use the same samples;
   - reset (`FORGET_S`) starts over, and `progress` counts standing frames only.
4. **Body state:**
   - `FrameResult` defaults (`ready`, 1.0) keep old recordings and every other `FrameResult` constructor working;
   - the pipeline reads `self._model.ready` / `.progress`, so two-camera `--tracker model` also reports
     measuring.
5. **`stability_eval`:**
   - it never saves the height;
   - the still-frame test matches the spec (median 2D motion < 2.5 px for 15 frames);
   - lag is measured against B's own 2D hip height (addendum, decision 2).

---

### Task 1: `stability_eval`, a stability report for one-camera recordings

**Files:**
- Create: `vision/stability_eval.py`, `vision/tests/test_stability_eval.py`
- Modify: `vision/README.md`

- [ ] **Step 0: Save the two-camera reference (before any change).** Task 6 checks two-camera output against it.

```bash
.venv/bin/python - <<'EOF'
import numpy as np
from vision.config import load_mat
from vision.pipeline import Pipeline
from vision.replay import cams_from_header, iter_records, read_header
path = "data/recordings/20261011-022822.jsonl"
cams = cams_from_header(read_header(path))
for tr in ("triangulate", "model"):
    pipe, J = Pipeline(cams, mat=load_mat(), tracker=tr), []
    for fr, raw, skew, people in iter_records(path):
        get = (lambda n: people.get(n, [])) if people is not None else (lambda n: raw.get(n))
        o = pipe.process(fr.t, get("A"), get("B"), skew)
        J.append(np.r_[o.frame.joints.ravel(), [float(bool((o.feet_locked or {}).get("l")))]])
    np.save(f"/tmp/mira-twocam-{tr}.npy", np.array(J))
EOF
```

- [ ] **Step 1: Write the failing tests**

Create `vision/tests/test_stability_eval.py`:

```python
import numpy as np

from vision.config import MatConfig
from vision.mono import stature
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.stability_eval import _lag_frames, metrics, replay
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

K = np.array([[1484.0, 0, 640], [0, 1484.0, 360], [0, 0, 1]])


def test_lag_is_found_between_frames():
    t = np.arange(300)
    a = np.sin(t / 15.0)
    assert abs(_lag_frames(a, np.sin((t - 3) / 15.0)) - 3.0) < 0.05
    assert abs(_lag_frames(a, np.sin((t - 1.5) / 15.0)) - 1.5) < 0.1


def test_a_steady_synthetic_session_reports_steady(tmp_path):
    cam, X0 = make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    path, rng = tmp_path / "one.jsonl", np.random.default_rng(4)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    with Recorder(path, {"B": cam}, {"length_m": MatConfig().length_m}) as rec:
        for k in range(150):  # 3 s standing still, then 2 s walking sideways
            X = X0 + [0.01 * max(k - 90, 0), 0.0, 0.0]
            rec.write(pipe.process(1000 + k / 30, None, pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng)))
    outs, cam2, restarts = replay(str(path), h)
    m = metrics(outs, cam2, restarts)
    assert m["fitted_pct"] > 50 and m["still_frames"] > 30
    assert m["still_p90_cm"] < 1.0 and m["jumps"] == 0 and m["floating"] == 0
    assert m["reproj_px"] < 10.0  # the walk is followed a frame or two behind (smoothing)
    assert m["restarts"] == 0  # (no lag check: the hips never move up or down here)
```


- [ ] **Step 2: Run them and see them fail**

`.venv/bin/python -m pytest -q vision/tests/test_stability_eval.py`. Expected: a collection error,
`No module named 'vision.stability_eval'`.

- [ ] **Step 3: Write the tool**

Create `vision/stability_eval.py`:

```python
"""How steady the one-camera body is: replays of one-camera recordings (camera B, the recorded person).

    python -m vision.stability_eval data/recordings/<file>.jsonl [...] [--height-cm 170]

Per file: jitter while the person is still in B's picture, motion over all fitted frames, one-frame
jumps, ankle spikes, feet floating above the floor, how well the body matches B's 2D points, lag of the
hip height behind B's own 2D hip height, the deepest knee bend, restarts and fit time.
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np

BODY = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 31, 32]  # shoulders ... toes
ANKLES = [27, 28]
STILL_PX = 2.5  # median 2D motion of the body below this ...
STILL_FRAMES = 15  # ... for this many frames in a row: the person is still
JUMP_M = 0.08
FLOAT_M = 0.05
MAX_LAG = 10  # frames searched either way
LAG_STEP = 0.05  # frames


def replay(path, height_m: float | None, cam_name: str = "B"):
    """(pipeline outputs, camera config, restarts) for a one-camera replay of `path`."""
    from vision.config import load_mat
    from vision.pipeline import Pipeline
    from vision.replay import cams_from_header, iter_records, read_header

    cam = cams_from_header(read_header(path))[cam_name]
    pipe = Pipeline({cam_name: cam}, mat=load_mat(), height_m=height_m)
    outs = []
    for frame, raw, skew, _ in iter_records(path):
        p = raw.get(cam_name)
        args = (None, p) if cam_name == "B" else (p, None)
        outs.append(pipe.process(frame.t, *args, skew))
    restarts = pipe._model.recoveries if pipe._model is not None else 0
    return outs, cam, restarts


def _project(cam, X):
    R, t, K = np.asarray(cam.R, float), np.ravel(cam.t).astype(float), np.asarray(cam.K, float)
    Xc = X @ R.T + t
    return Xc[..., :2] / Xc[..., 2:] @ K[:2, :2].T + K[:2, 2]


def _knee(J, hip, knee, ankle):
    u, v = J[:, hip] - J[:, knee], J[:, ankle] - J[:, knee]
    c = np.sum(u * v, axis=1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1))
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def _lag_frames(a, b) -> float:
    """Frames b is behind a, to LAG_STEP: the shift of b (linearly interpolated between frames) whose
    correlation with a, over the frames where both are known, is highest."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    idx = np.arange(n, dtype=float)
    best, lag = -np.inf, 0.0
    for k in np.arange(-MAX_LAG, MAX_LAG + 1e-9, LAG_STEP):
        src = idx + k  # b shifted back by k frames lines up with a
        inside = (src >= 0) & (src <= n - 1)
        shifted = np.interp(src[inside], idx, np.nan_to_num(b))
        valid = np.interp(src[inside], idx, np.isfinite(b).astype(float)) > 0.999
        both = valid & np.isfinite(a[inside])
        if both.sum() < 10:
            continue
        c = float(np.corrcoef(a[inside][both], shifted[both])[0, 1])
        if c > best:
            best, lag = c, float(k)
    return round(lag, 2)


def metrics(outs, cam, restarts: int = 0) -> dict:
    J = np.array([o.frame.joints for o in outs])
    t = np.array([o.frame.t for o in outs])
    model = np.array([o.tracker == "model" for o in outs])
    seen = [o.raw.get(cam.name) for o in outs]
    lm = np.array([p.lm2d if (p is not None and p.detected) else np.full((33, 2), np.nan) for p in seen])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices where nobody is seen
        motion2d = np.nanmedian(np.linalg.norm(np.diff(lm, axis=0), axis=2)[:, BODY], axis=1)
        still = np.zeros(len(t), bool)
        for i in range(len(motion2d) - STILL_FRAMES + 1):
            if np.all(motion2d[i:i + STILL_FRAMES] < STILL_PX):
                still[i:i + STILL_FRAMES + 1] = True
        step = np.linalg.norm(np.diff(J, axis=0), axis=2)[:, BODY]
        ok = model[1:] & model[:-1] & (np.diff(t) < 0.1)
        st = ok & still[1:] & still[:-1]
        fitted = J[model]
        low = fitted[:, [29, 30, 31, 32], 1].min(axis=1) if model.any() else np.empty(0)
        knee = np.minimum(_knee(fitted, 23, 25, 27), _knee(fitted, 24, 26, 28)) if model.any() else np.empty(0)
        reproj = np.linalg.norm(_project(cam, fitted) - lm[model], axis=2)[:, BODY] if model.any() else np.empty(0)
        hip_px = np.where(model, (_project(cam, J[:, 23]) + _project(cam, J[:, 24]))[:, 1] / 2, np.nan)
        hip_2d = np.where(model, (lm[:, 23, 1] + lm[:, 24, 1]) / 2, np.nan)
        pct = lambda a, q: float(np.nanpercentile(a, q) * 100) if np.size(a) else float("nan")  # noqa: E731
        ank = step[ok][:, [BODY.index(k) for k in ANKLES]]
        fit_ms = [o.fit_ms for o in outs if o.fit_ms is not None]
        return {
            "frames": len(outs), "fitted_pct": float(100 * model.mean()), "still_frames": int(st.sum()),
            "still_med_cm": pct(step[st], 50), "still_p90_cm": pct(step[st], 90),
            "moving_p90_cm": pct(step[ok], 90),
            "jumps": int((np.nanmax(step[ok], axis=1) > JUMP_M).sum()) if ok.any() else 0,
            "ankle_p99_cm": pct(ank, 99),
            "floating": int((low > FLOAT_M).sum()),
            "reproj_px": float(np.nanmedian(reproj)) if reproj.size else float("nan"),
            "lag_frames": _lag_frames(hip_2d, hip_px),
            "deepest_knee_deg": float(np.nanpercentile(knee, 2)) if knee.size else float("nan"),
            "restarts": int(restarts),
            "fit_ms": float(np.median(fit_ms)) if fit_ms else float("nan"),
        }


def format_row(name: str, m: dict) -> str:
    return (f"{name}: fitted {m['fitted_pct']:.0f}%  still {m['still_med_cm']:.2f}/{m['still_p90_cm']:.2f} cm "
            f"({m['still_frames']} fr)  moving p90 {m['moving_p90_cm']:.2f} cm  jumps>8cm {m['jumps']}  "
            f"ankle p99 {m['ankle_p99_cm']:.1f} cm  floating {m['floating']}  reproj {m['reproj_px']:.1f} px  "
            f"lag {m['lag_frames']:.2f} fr  deepest knee {m['deepest_knee_deg']:.0f}°  restarts {m['restarts']}  "
            f"fit {m['fit_ms']:.1f} ms")


def main() -> None:
    from vision.config import load_person

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recordings", nargs="+")
    ap.add_argument("--height-cm", type=float, default=None, help="person's height (default: config/person.yaml; not saved)")
    ap.add_argument("--cam", default="B")
    args = ap.parse_args()
    cm = args.height_cm if args.height_cm is not None else load_person()
    h = cm / 100 if cm else None
    for path in args.recordings:
        outs, cam, restarts = replay(path, h, args.cam)
        print(format_row(path.split("/")[-1], metrics(outs, cam, restarts)))


if __name__ == "__main__":
    main()
```

Apply to `vision/README.md`:

```diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -38,6 +38,7 @@
 python -m vision.debug_view --height-cm 175   # your height, saved in config/person.yaml
 python -m vision.checks                       # one-camera checks (uses the saved height)
 python -m vision.mono_eval FILE...            # B-only replay of a two-camera recording vs triangulation
+python -m vision.stability_eval FILE... [--height-cm 170]  # how steady the one-camera body is (still, spikes, lag)
 ```
 
 Place B so it sees your whole body anywhere on the mat (head to feet) and at least two markers, ideally one
```


- [ ] **Step 4: Run the tests and the base report**

- `.venv/bin/python -m pytest -q` → 238 passed.
- `.venv/bin/python -m vision.stability_eval data/recordings/20261011-{060807,054226,055039}.jsonl --height-cm 170`
  → the "base" rows of the table above:
  - `060807`: still 0.32/1.70, moving 3.54, jumps 128, ankle 10.0, floating 3, 2D match 8.2, lag 1.40,
    restarts 0;
  - `054226`: moving 7.40, jumps 125, ankle 16.0, 2D match 10.5, lag 1.80;
  - `055039`: still 6.50/9.09, moving 14.35, jumps 482, ankle 55.1, floating 12, 2D match 18.8, lag 1.55,
    restarts 25.
- Check that `config/person.yaml` is unchanged (the tool must not save the height).

- [ ] **Step 5: Commit**

```bash
git add vision/stability_eval.py vision/tests/test_stability_eval.py vision/README.md
git commit -m "vision: stability_eval reports how steady the one-camera body is

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Measure the one-camera body only from standing frames

**Files:**
- Modify: `vision/mono.py` (`is_standing`; `upright_rotation` uses it)
- Modify: `vision/tracker.py` (standing-only samples; `ready` property)
- Modify: `vision/tests/test_mono.py`

- [ ] **Step 1: Write the failing test**

Append `_crouched` and the crouch test to `vision/tests/test_mono.py`:

Apply to `vision/tests/test_mono.py`:

```diff
--- a/vision/tests/test_mono.py
+++ b/vision/tests/test_mono.py
@@ -344,3 +344,31 @@
     out = tr._gate(x, (None, uv, w))
     assert abs(out[0] - 0.10) < 1e-9  # a lasting position change passes through
     assert abs(out[T.SPINE.start] - T.GATE_MIN_GAIN * 0.3) < 1e-9  # an angle is still held
+
+
+def _crouched():
+    """Knees bent ~90°: hips 0.35 m lower and back, shins forward (a squat)."""
+    X = standing_skeleton()
+    drop = np.array([0.0, -0.35, -0.2])
+    upper = list(range(25))  # head, arms, hips
+    X[upper] += drop
+    X[[25, 26]] += [0.0, -0.08, 0.15]
+    return X
+
+
+def test_one_camera_body_crouching_at_first_is_measured_at_the_right_scale_once_standing():
+    cam, X = _cam_b(), standing_skeleton()
+    h = stature(X - (X[23] + X[24]) / 2)
+    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
+    t = 0.0
+    for _ in range(60):  # 2 s crouched: nothing measured
+        out = pipe.process(t, None, pose_from_points(cam, _crouched(), t=t))
+        t += 1 / 30
+        assert out.tracker != "model"
+    for _ in range(60):
+        out = pipe.process(t, None, pose_from_points(cam, X, t=t))
+        t += 1 / 30
+    assert out.tracker == "model"
+    assert abs(pipe._model.world_scale - 1.0) < 0.02  # the synthetic world shape is already metric
+    J = out.frame.joints
+    assert abs(stature(J - (J[23] + J[24]) / 2) / h - 1) < 0.02
```


- [ ] **Step 2: Run it and see it fail**

`.venv/bin/python -m pytest -q vision/tests/test_mono.py -k crouching`. Expected:
`AssertionError: assert 'model' != 'model'`. Today the body is measured from the crouched frames.

- [ ] **Step 3: Implement**

Apply to `vision/mono.py`:

```diff
--- a/vision/mono.py
+++ b/vision/mono.py
@@ -76,25 +76,32 @@
     return _angle_deg(J[hip], J[knee], J[ankle])
 
 
+def is_standing(J) -> bool:
+    """Roughly standing straight: both knees above UPRIGHT_KNEE_DEG (MediaPipe's own knees rarely look
+    fully straight) and not bent at the waist (shoulders, hips and knees in line within UPRIGHT_HIP_DEG).
+    False when a needed joint is missing."""
+    J = np.asarray(J, float)
+    if not np.isfinite(J[[11, 12, 23, 24, 25, 26, 27, 28]]).all():
+        return False
+    if min(_knee_deg(J, 23, 25, 27), _knee_deg(J, 24, 26, 28)) <= UPRIGHT_KNEE_DEG:
+        return False
+    hip_mid = (J[23] + J[24]) / 2
+    return _angle_deg((J[11] + J[12]) / 2, hip_mid, (J[25] + J[26]) / 2) > UPRIGHT_HIP_DEG
+
+
 def upright_rotation(shapes, min_frames: int = UPRIGHT_MIN_FRAMES, max_deg: float = UPRIGHT_MAX_DEG
                      ) -> np.ndarray | None:
     """Rotation (tilt only, about a horizontal axis) that makes the median ankles-to-shoulders line of
-    `shapes` (bodies in the mat frame) vertical, from frames where the person is roughly standing (both
-    knees above UPRIGHT_KNEE_DEG, MediaPipe's own knees rarely look fully straight; and not bent at the
-    waist: shoulders, hips and knees in line within UPRIGHT_HIP_DEG). MediaPipe's world shape leans (mostly
-    toward the camera); this takes the lean out. None with fewer than `min_frames` such frames, or when they
-    disagree (median angle to their median up-direction above UPRIGHT_SPREAD_DEG); the tilt is capped at
-    `max_deg` so one odd session can't tip the body over."""
+    `shapes` (bodies in the mat frame) vertical, from frames where the person is roughly standing
+    (is_standing). MediaPipe's world shape leans (mostly toward the camera); this takes the lean out. None
+    with fewer than `min_frames` such frames, or when they disagree (median angle to their median
+    up-direction above UPRIGHT_SPREAD_DEG); the tilt is capped at `max_deg` so one odd session can't tip
+    the body over."""
     ups = []
     for J in shapes:
         J = np.asarray(J, float)
-        if not np.isfinite(J[[11, 12, 23, 24, 25, 26, 27, 28]]).all():
+        if not is_standing(J):
             continue
-        if min(_knee_deg(J, 23, 25, 27), _knee_deg(J, 24, 26, 28)) <= UPRIGHT_KNEE_DEG:
-            continue
-        hip_mid = (J[23] + J[24]) / 2
-        if _angle_deg((J[11] + J[12]) / 2, hip_mid, (J[25] + J[26]) / 2) <= UPRIGHT_HIP_DEG:
-            continue  # bent over: the trunk isn't over the legs
         v = (J[11] + J[12]) / 2 - (J[27] + J[28]) / 2
         n = float(np.linalg.norm(v))
         if n > 1e-6:
```

Apply to `vision/tracker.py`:

```diff
--- a/vision/tracker.py
+++ b/vision/tracker.py
@@ -312,6 +312,11 @@
         self._gate_t: float | None = None  # time of the last gated frame
 
     @property
+    def ready(self) -> bool:
+        """The body has been measured: the fit is in use."""
+        return self.shape is not None
+
+    @property
     def progress(self) -> float:
         return 1.0 if self.shape is not None else min(len(self._samples) / self.frames_needed, 1.0)
 
@@ -428,6 +433,10 @@
         self._last_seen = t
         if self.shape is None:
             usable = tracking == "3d" or (self.single and tracking.startswith("single_cam"))
+            if usable and self.single:  # one camera: measured only while standing straight (scale, lean)
+                from vision.mono import is_standing
+
+                usable = is_standing(joints)
             if usable and joints is not None and np.isfinite(joints[NEEDED]).all():
                 # measured at scale 1: one-camera joints come scaled by world_scale (1 until measured)
                 self._samples.append(np.array(joints, float) / (produced_at if self.single else 1.0))
```


- [ ] **Step 4: Run all tests**

`.venv/bin/python -m pytest -q` → 239 passed. The existing `upright_rotation` tests still pass with the
shared `is_standing`.

- [ ] **Step 5: Commit**

```bash
git add vision/mono.py vision/tracker.py vision/tests/test_mono.py
git commit -m "vision: one camera measures the body only from standing frames

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The 2D-motion gate moves inside the solve

**Files:**
- Modify: `vision/tracker.py`, `vision/tests/test_mono.py`

The post-fit `_gate` blend becomes `_hold`. `_hold` returns a weight per pose number for the smooth-motion
term of this frame's solve: `gain ** -GATE_POWER`, with the gain as before and the position's gain floored at
`GATE_ROOT_MIN`. In the same task:
- the angles' smooth-motion cost becomes `SINGLE_TEMPORAL_PX` = 8. The position keeps `TEMPORAL_PX` = 3, or
  walking toward B lags 9.5 cm, failing `test_one_camera_walking_toward_the_camera_keeps_up`.
- the floor contact becomes 600 px/m. At 300, feet float in 90 frames of `060807`, because the old anchor was
  what pinned them.

- [ ] **Step 1: Write the failing tests**

Replace `test_one_camera_gate_passes_a_held_back_position_but_still_holds_an_angle` with the two tests
below. `test_one_camera_held_body_keeps_its_feet_on_the_floor` already passes today. It is a guard, so
it will not fail first.

Apply to `vision/tests/test_mono.py`:

```diff
--- a/vision/tests/test_mono.py
+++ b/vision/tests/test_mono.py
@@ -332,20 +332,34 @@
     assert _walk_toward_camera_errors(1.2) < 0.12
 
 
-def test_one_camera_gate_passes_a_held_back_position_but_still_holds_an_angle():
+def test_one_camera_hold_frees_the_position_more_than_an_angle_and_moving_points_fully():
     import vision.tracker as T
 
     tr = T.ModelTracker({"B": _cam_b()})
     uv, w = np.zeros((33, 2)), np.ones(33)
-    tr._gate(np.zeros(T.N_PARAMS), (None, uv, w))  # first frame: taken as is
-    x = np.zeros(T.N_PARAMS)
-    x[0] = 0.10  # the hips 10 cm along the line of sight (barely moves in 2D)
-    x[T.SPINE.start] = 0.3  # a large one-off bend from a noisy depth guess, with nothing moving in 2D
-    out = tr._gate(x, (None, uv, w))
-    assert abs(out[0] - 0.10) < 1e-9  # a lasting position change passes through
-    assert abs(out[T.SPINE.start] - T.GATE_MIN_GAIN * 0.3) < 1e-9  # an angle is still held
+    assert tr._hold((None, uv, w)) is None  # first frame: nothing to hold to
+    still = tr._hold((None, uv, w))  # nothing moved in 2D
+    assert np.allclose(still[T.SPINE], T.GATE_MIN_GAIN ** -T.GATE_POWER)  # an angle held strongly
+    assert np.allclose(still[T.ROOT], T.GATE_ROOT_MIN ** -T.GATE_POWER)  # the position held less
+    moving = tr._hold((None, uv + T.GATE_FULL_PX, w))  # everything moved a full GATE_FULL_PX
+    assert np.allclose(moving, 1.0)
+
 
+def test_one_camera_held_body_keeps_its_feet_on_the_floor():
+    """The hold is part of the solve: a still body with depth noise never lifts a planted foot."""
+    cam, X = _cam_b(), standing_skeleton()
+    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X - (X[23] + X[24]) / 2))
+    rng = np.random.default_rng(5)
+    lows = []
+    for k in range(150):
+        v = pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng)
+        v.world = v.world + rng.normal(0, 0.03, v.world.shape)
+        out = pipe.process(k / 30, None, v)
+        if out.tracker == "model":
+            lows.append(np.min(out.frame.joints[[29, 30, 31, 32], 1]))
+    assert len(lows) > 50 and np.max(np.abs(lows)) < 0.03
 
+
 def _crouched():
     """Knees bent ~90°: hips 0.35 m lower and back, shins forward (a squat)."""
     X = standing_skeleton()
```


- [ ] **Step 2: Run them and see them fail**

`.venv/bin/python -m pytest -q vision/tests/test_mono.py`. Expected: `AttributeError: 'ModelTracker' object has
no attribute '_hold'`.

- [ ] **Step 3: Implement**

Apply to `vision/tracker.py`:

```diff
--- a/vision/tracker.py
+++ b/vision/tracker.py
@@ -44,12 +44,14 @@
 MIN_VIS = 0.3
 ROBUST_PX = 10.0
 TEMPORAL_PX = 3.0  # cost of moving one "sigma" away from the previous frame, in pixels
+SINGLE_TEMPORAL_PX = 8.0  # ... one camera, for the angles: depth is noisier and one view pins less, so they cost more
+# (the body's position keeps TEMPORAL_PX: walking toward or away from the camera must not lag)
 SIGMA_POS_M, SIGMA_ROT = 0.04, 0.25
 LIMIT_PX_PER_RAD = 200.0
 FLOOR_PX_PER_M = 400.0
 ANCHOR_PX_PER_M = 200.0
 WORLD_PX_PER_M = 150.0  # one camera: pull toward MediaPipe's own 3D shape (the depth cue), px per metre
-CONTACT_PX_PER_M = 300.0  # one camera: the lowest foot point is pulled onto the floor
+CONTACT_PX_PER_M = 600.0  # one camera: the lowest foot point is pulled onto the floor
 
 
 def _unit(v, fallback=None):
@@ -255,11 +257,13 @@
 SMOOTH_MIN_CUTOFF = 0.7  # Hz: One Euro on the pose numbers (0 = off)
 SMOOTH_BETA = 6.0  # two cameras: how much faster it follows when the pose changes quickly
 ROOT_MIN_CUTOFF = 3.0  # Hz: two cameras pin the body's position well, so it is smoothed less
-SINGLE_SMOOTH_TOO = True  # one camera: One Euro on the pose numbers before the 2D-motion gate
-GATE_FULL_PX = 8.0  # one camera: a landmark moving this many px per frame passes its change through fully
-GATE_MIN_GAIN = 0.08  # ... a landmark still in 2D lets only this fraction of a depth change through per frame
-GATE_PASS_M = 0.06  # ... but a position held back this far (m) passes through fully (walking along the line of sight);
-# angles get no such pass: MediaPipe's depth noise alone moves them by more than any useful threshold
+SINGLE_SMOOTH_TOO = True  # one camera: One Euro on the pose numbers after the gated solve
+# one camera, the 2D-motion gate (inside the solve): a pose number is held to the previous frame's value as
+# strongly as its landmarks are still in the camera's picture, so depth noise doesn't move a still body
+GATE_FULL_PX = 8.0  # a landmark moving this many px per frame frees its pose numbers fully (gain 1)
+GATE_MIN_GAIN = 0.08  # ... gain of a pose number whose landmarks are still in 2D
+GATE_ROOT_MIN = 0.3  # ... the body's position never below this gain: walking along the line of sight is followed
+GATE_POWER = 0.75  # ... the hold multiplies the smooth-motion cost by gain ** -GATE_POWER
 GATE_GAP_S = 0.2  # ... after a gap longer than this (s) the gate and smoother start afresh
 SINGLE_BETA = 2.0  # one camera: noisier depth, so follow speed-ups more cautiously
 SINGLE_ROOT_MIN_CUTOFF = 1.0  # one camera: the floor position wobbles ~2 cm, smooth it more
@@ -308,7 +312,7 @@
         root, beta = (SINGLE_ROOT_MIN_CUTOFF, SINGLE_BETA) if self.single else (ROOT_MIN_CUTOFF, SMOOTH_BETA)
         cutoff = np.r_[[root] * 3, [SMOOTH_MIN_CUTOFF] * (N_PARAMS - 3)]
         self._smooth = OneEuroFilter(cutoff, beta) if SMOOTH_MIN_CUTOFF else None
-        self._gate_uv = self._gate_w = self._gate_out = None
+        self._gate_uv = self._gate_w = None  # one camera: the previous frame's 2D points, for the gate
         self._gate_t: float | None = None  # time of the last gated frame
 
     @property
@@ -320,7 +324,7 @@
     def progress(self) -> float:
         return 1.0 if self.shape is not None else min(len(self._samples) / self.frames_needed, 1.0)
 
-    def _residuals(self, p, obs, prev, anchors, world=None):
+    def _residuals(self, p, obs, prev, anchors, world=None, hold=None):
         X = forward(p, self.shape)
         parts = []
         for cam, uv, w in obs:
@@ -330,7 +334,8 @@
             rel = X - (X[23] + X[24]) / 2
             parts.append((WORLD_PX_PER_M * (rel - W) * ww[:, None]).ravel())
             parts.append(np.array([CONTACT_PX_PER_M * np.min(X[[29, 30, 31, 32], 1])]))
-        parts.append((p - prev) / SIGMA * TEMPORAL_PX)
+        temporal = np.r_[[TEMPORAL_PX] * 3, [SINGLE_TEMPORAL_PX] * (N_PARAMS - 3)] if self.single else TEMPORAL_PX
+        parts.append((p - prev) / SIGMA * temporal * (1.0 if hold is None else hold))
         lim = []
         for s in SIDE:
             for q in (p[ARM[s][1]], p[LEG[s][1]]):
@@ -364,11 +369,11 @@
         d = np.concatenate(d) if d else np.empty(0)
         return float(np.median(d)) if d.size else 0.0
 
-    def _solve(self, p0, obs, prev, anchors, S: np.ndarray, world=None) -> tuple[np.ndarray, float]:
+    def _solve(self, p0, obs, prev, anchors, S: np.ndarray, world=None, hold=None) -> tuple[np.ndarray, float]:
         """Robust (soft-L1) Levenberg-Marquardt from p0, Jacobian by grouped finite differences."""
         p = p0.copy()
         lam = 1e-3
-        r = self._residuals(p, obs, prev, anchors, world)
+        r = self._residuals(p, obs, prev, anchors, world, hold)
 
         def robust(res):
             z = (res / ROBUST_PX) ** 2
@@ -381,14 +386,14 @@
             for g in FD_GROUPS:
                 dp = np.zeros(N_PARAMS)
                 dp[g] = h[g]
-                dr = (self._residuals(p + dp, obs, prev, anchors, world) - r)
+                dr = (self._residuals(p + dp, obs, prev, anchors, world, hold) - r)
                 for j in g:
                     J[:, j] = np.where(S[:, j], dr / h[j], 0.0)
             Jw, rw = J * sw[:, None] * SIGMA, r * sw  # scaled parameters: steps measured in sigmas
             A, g_ = Jw.T @ Jw, Jw.T @ rw
             for _ in range(4):
                 step = -np.linalg.solve(A + lam * (np.diag(np.diag(A)) + 1e-6 * np.eye(N_PARAMS)), g_) * SIGMA
-                r_new = self._residuals(p + step, obs, prev, anchors, world)
+                r_new = self._residuals(p + step, obs, prev, anchors, world, hold)
                 cost_new, sw_new = robust(r_new)
                 if cost_new < cost:
                     p, r, cost, sw = p + step, r_new, cost_new, sw_new
@@ -478,7 +483,14 @@
         t0 = time.perf_counter()
         world = self._world(views)
         S = self._sparsity(obs, anc, world).toarray().astype(bool)
-        x, cost = self._solve(prev, obs, prev, anc, S, world)
+        hold = None
+        if self.single:
+            if self._gate_t is not None and t - self._gate_t > GATE_GAP_S:  # back after a gap: start afresh
+                self._gate_uv = self._gate_w = None
+                self._smooth.reset() if self._smooth is not None else None
+            self._gate_t = t
+            hold = self._hold(obs[0])
+        x, cost = self._solve(prev, obs, prev, anc, S, world, hold)
         # stuck in a wrong pose? restart from this frame's triangulated joints and keep the better fit
         if self._median_residual(x, obs) > RECOVER_PX:
             fresh = params_from_joints(np.asarray(joints, float), self.shape)
@@ -486,8 +498,7 @@
                 x2, _ = self._solve(fresh, obs, fresh, anc, S, world)
                 if self._median_residual(x2, obs) < self._median_residual(x, obs):
                     x = x2
-                    self._smooth.reset() if self._smooth is not None else None
-                    self._gate_out = None  # a recovered pose snaps into place instead of being gated
+                    self._smooth.reset() if self._smooth is not None else None  # a recovered pose snaps into place
                     self.recoveries += 1
         self.last_ms = (time.perf_counter() - t0) * 1000
         if not np.isfinite(x).all():
@@ -495,28 +506,24 @@
         self._p = x
         self.last_cost = float(cost)
         # smooth the pose numbers (not the joints): steady when still, quick when moving, bones exact
-        if self.single:  # adaptive smoothing first, then the 2D-motion gate
-            if self._gate_t is not None and t - self._gate_t > GATE_GAP_S:  # back after a gap: start afresh
-                self._gate_uv = self._gate_w = self._gate_out = None
-                self._smooth.reset() if self._smooth is not None else None
-            self._gate_t = t
-            smoothed = self._smooth(x, t) if (self._smooth is not None and SINGLE_SMOOTH_TOO) else x
-            return forward(self._gate(smoothed, obs[0]), self.shape)
+        if self.single:  # the gate was part of the solve; adaptive smoothing after it
+            return forward(self._smooth(x, t) if (self._smooth is not None and SINGLE_SMOOTH_TOO) else x, self.shape)
         return forward(self._smooth(x, t) if self._smooth is not None else x, self.shape)
 
-    def _gate(self, x: np.ndarray, ob) -> np.ndarray:
-        """One camera: a pose number may only change as fast as the landmarks it moves are moving in the
-        camera's 2D view. The camera sees side-to-side and up-down motion precisely; only depth is noisy,
-        so joints that are still in 2D are held still in 3D, and real movement (which shows in 2D) passes.
-        A lasting position change that barely shows in 2D (walking toward the camera) builds up against the
-        held value and passes too: the further it is held back, the more of it comes through (fully at
-        GATE_PASS_M). Angles have no such pass."""
+    def _hold(self, ob) -> np.ndarray | None:
+        """One camera: how strongly each pose number is held to the previous frame in this frame's solve
+        (multiplies the smooth-motion cost), or None for no extra hold (the first frame, after a gap).
+
+        A pose number may only change as fast as the landmarks it moves are moving in the camera's 2D view.
+        The camera sees side-to-side and up-down motion precisely; only depth is noisy, so a number whose
+        landmarks are still in 2D is held (gain GATE_MIN_GAIN), and real movement (which shows in 2D) frees
+        it (gain 1). The body's position keeps at least GATE_ROOT_MIN, so walking along the line of sight
+        is followed. Being part of the solve, the hold never breaks floor contact, joint limits or the 2D fit."""
         _, uv, w = ob
-        prev_uv, prev_w, prev_out = self._gate_uv, self._gate_w, self._gate_out
+        prev_uv, prev_w = self._gate_uv, self._gate_w
         self._gate_uv, self._gate_w = uv.copy(), w.copy()
-        if prev_out is None or prev_uv is None:
-            self._gate_out = x.copy()
-            return x
+        if prev_uv is None:
+            return None
         ok = (w > 0) & (prev_w > 0)
         motion = np.where(ok, np.linalg.norm(uv - prev_uv, axis=1), np.nan)
         gain = np.ones(N_PARAMS)
@@ -525,6 +532,5 @@
             m = m[np.isfinite(m)]
             if m.size:
                 gain[j] = float(np.clip(np.median(m) / GATE_FULL_PX, GATE_MIN_GAIN, 1.0))
-        gain[ROOT] = np.maximum(gain[ROOT], np.clip(np.abs(x[ROOT] - prev_out[ROOT]) / GATE_PASS_M, 0.0, 1.0))
-        self._gate_out = prev_out + gain * (x - prev_out)
-        return self._gate_out
+        gain[ROOT] = np.maximum(gain[ROOT], GATE_ROOT_MIN)
+        return gain ** -GATE_POWER
```


- [ ] **Step 4: Run all tests**

`.venv/bin/python -m pytest -q` → 240 passed. These existing tests must pass unchanged:
- depth shake held when still in 2D;
- real sideways movement;
- walking toward the camera, at 0.5 and 1.2 m/s;
- catching up after a gap;
- scale the same after a reset.

- [ ] **Step 5: Commit**

```bash
git add vision/tracker.py vision/tests/test_mono.py
git commit -m "vision: one camera holds still pose numbers inside the fit instead of blending them after it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Planted feet no longer pull the one-camera fit

**Files:**
- Modify: `vision/pipeline.py`, `vision/tests/test_mono.py`

- [ ] **Step 1: Write the failing test**

Append to `vision/tests/test_mono.py`:

Apply to `vision/tests/test_mono.py`:

```diff
--- a/vision/tests/test_mono.py
+++ b/vision/tests/test_mono.py
@@ -386,3 +386,25 @@
     assert abs(pipe._model.world_scale - 1.0) < 0.02  # the synthetic world shape is already metric
     J = out.frame.joints
     assert abs(stature(J - (J[23] + J[24]) / 2) / h - 1) < 0.02
+
+
+def test_one_camera_planted_feet_never_pull_the_fit_but_still_show_as_locked():
+    import vision.tracker as T
+
+    cam, X = _cam_b(), standing_skeleton()
+    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X - (X[23] + X[24]) / 2))
+    seen = []
+    real = pipe._model.update
+
+    def spy(t, joints, tracking, views, anchors=None):
+        seen.append(anchors)
+        return real(t, joints, tracking, views, anchors)
+
+    pipe._model.update = spy
+    rng = np.random.default_rng(1)
+    for k in range(90):
+        out = pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng))
+    assert all(not a for a in seen)  # no anchor ever reaches the one-camera fit
+    assert out.tracker == "model" and out.feet_locked == {"l": True, "r": True}
+    J = out.frame.joints
+    assert abs(np.linalg.norm(J[25] - J[27]) - pipe._model.shape.lengths["shin"]) < 1e-6  # nothing moved
```


- [ ] **Step 2: Run it and see it fail**

`.venv/bin/python -m pytest -q vision/tests/test_mono.py -k planted`. Expected: `assert False` on
`all(not a for a in seen)`. Today the anchors reach the fit.

- [ ] **Step 3: Implement**

Apply to `vision/pipeline.py`:

```diff
--- a/vision/pipeline.py
+++ b/vision/pipeline.py
@@ -31,7 +31,7 @@
     people: dict[str, list[PoseResult]] | None = None  # everyone found per camera (multi-person input)
     ignored: dict[str, list[PoseResult]] | None = None  # people not tracked, per camera
     dropped: str | None = None  # camera ignored this frame because it disagreed with the other
-    feet_locked: dict[str, bool] | None = None  # "l"/"r" -> foot held by the FootAnchor this frame
+    feet_locked: dict[str, bool] | None = None  # "l"/"r" -> foot planted (held by the FootAnchor; one camera: shown only)
     tracker: str = "triangulate"  # which tracker produced frame.joints ("model" or "triangulate")
     fit_ms: float | None = None  # model fit time this frame
 
@@ -152,8 +152,11 @@
         else:
             usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
             joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
+        # one camera with the model: the floor-placed joints are too rough to pin feet to, and the fit has its
+        # own floor contact, so the anchor only reports planted feet on the output (below) and moves nothing
+        single_model = self._single is not None and self._model is not None
         feet_locked = None
-        if self._anchor is not None:
+        if self._anchor is not None and not single_model:
             if tracking == "lost":
                 self._anchor.reset()
             else:
@@ -167,6 +170,11 @@
         if fitted is not None:
             joints, used = fitted, "model"
             conf = np.maximum(conf, _evidence({"A": a, "B": b}))
+        if single_model and self._anchor is not None:
+            if tracking == "lost":
+                self._anchor.reset()
+            else:
+                _, feet_locked = self._anchor.update(t, joints)  # report only: joints are not moved
         if used != "model" and self._filter is not None:  # the model smooths its own pose numbers
             joints = self._filter(joints, t)
         frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
```


The one-camera anchor goes from editing the joints before the fit to reporting on the fitted joints. When the
body isn't measured yet, it reports on the floor-placed body. Either way it never moves a joint.

- [ ] **Step 4: Run all tests**

`.venv/bin/python -m pytest -q` → 241 passed. `vision/tests/test_anchor.py` (two cameras) is unchanged and
passes.

- [ ] **Step 5: Commit**

```bash
git add vision/pipeline.py vision/tests/test_mono.py
git commit -m "vision: one-camera planted feet are shown but no longer pull the fit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Measuring / ready state for the app

**Files:**
- Modify: `vision/types.py` (`FrameResult.body`, `body_progress`, saved and loaded)
- Modify: `vision/pipeline.py` (sets them)
- Modify: `vision/debug_view.py` ("stand straight to measure... N%")
- Modify: `vision/README.md` (the contract for the team)
- Modify: `vision/tests/test_mono.py`, `vision/tests/test_types.py`, `vision/tests/test_debug_view.py`

- [ ] **Step 1: Write the failing tests**

Append the state tests to `vision/tests/test_mono.py`, the round-trip test to `vision/tests/test_types.py`,
and the hint test to `vision/tests/test_debug_view.py`:

Apply to `vision/tests/test_mono.py`:

```diff
--- a/vision/tests/test_mono.py
+++ b/vision/tests/test_mono.py
@@ -408,3 +408,43 @@
     assert out.tracker == "model" and out.feet_locked == {"l": True, "r": True}
     J = out.frame.joints
     assert abs(np.linalg.norm(J[25] - J[27]) - pipe._model.shape.lengths["shin"]) < 1e-6  # nothing moved
+
+
+def test_one_camera_body_state_measuring_then_ready_and_after_a_long_loss_measuring_again():
+    from vision.tracker import FORGET_S
+
+    cam, X = _cam_b(), standing_skeleton()
+    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X - (X[23] + X[24]) / 2))
+    t, states = 0.0, []
+    for _ in range(10):  # crouched: progress does not rise
+        out = pipe.process(t, None, pose_from_points(cam, _crouched(), t=t))
+        t += 1 / 30
+        states.append((out.frame.body, out.frame.body_progress, out.tracker))
+    assert all(s == ("measuring", 0.0, "triangulate") for s in states)
+    states = []
+    for _ in range(60):
+        out = pipe.process(t, None, pose_from_points(cam, X, t=t))
+        t += 1 / 30
+        states.append((out.frame.body, out.frame.body_progress, out.tracker))
+    first_ready = next(i for i, s in enumerate(states) if s[0] == "ready")
+    assert states[first_ready][1] == 1.0 and states[first_ready][2] == "model"  # ready on the measured frame
+    progress = [p for b, p, _ in states[:first_ready]]
+    assert progress == sorted(progress) and 0 < progress[-1] < 1
+    for _ in range(int(30 * (FORGET_S - 1))):  # a short loss: still ready
+        out = pipe.process(t, None, None)
+        t += 1 / 30
+        assert out.frame.tracking == "lost" and out.frame.body == "ready"
+    for _ in range(60):  # longer than FORGET_S in all: forgotten
+        out = pipe.process(t, None, None)
+        t += 1 / 30
+    out = pipe.process(t, None, pose_from_points(cam, X, t=t))
+    assert out.frame.body == "measuring" and out.frame.body_progress < 0.1
+
+
+def test_triangulation_is_always_ready():
+    from vision.tests.synth import target_cams
+
+    cams, X = target_cams(), standing_skeleton()
+    out = Pipeline(cams).process(0.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
+    assert out.frame.body == "ready" and out.frame.body_progress == 1.0
+    assert Pipeline(cams).process(0.0, None, None).frame.body == "ready"
```

Apply to `vision/tests/test_types.py`:

```diff
--- a/vision/tests/test_types.py
+++ b/vision/tests/test_types.py
@@ -29,3 +29,13 @@
     g = FrameResult.from_dict(json.loads(json.dumps(f.to_dict(), allow_nan=False)))
     np.testing.assert_array_equal(g.joints, joints)
     assert g.tracking == "3d" and g.flags == ["x"] and g.com is None and g.phase == "idle"
+
+
+def test_frame_result_body_state_round_trip_and_old_files_load_as_ready():
+    f = FrameResult(t=1.0, joints=np.zeros((33, 3)), conf=np.zeros(33), body="measuring", body_progress=0.4)
+    back = FrameResult.from_dict(f.to_dict())
+    assert back.body == "measuring" and back.body_progress == 0.4
+    old = f.to_dict()
+    del old["body"], old["body_progress"]
+    back = FrameResult.from_dict(old)
+    assert back.body == "ready" and back.body_progress == 1.0
```

Apply to `vision/tests/test_debug_view.py`:

```diff
--- a/vision/tests/test_debug_view.py
+++ b/vision/tests/test_debug_view.py
@@ -159,3 +159,16 @@
     two = compose_view(target_cams(), MatConfig(), {}, {}, None, None, None, None)
     assert one.shape == two.shape == (720, 1280, 3)
     assert (one[:360, :640] != two[:360, :640]).any()  # "not used (one-camera setup)" instead of camera A
+
+
+def test_one_camera_view_says_stand_straight_while_measuring():
+    from dataclasses import replace
+
+    cams, X = target_cams(), standing_skeleton()
+    one = {"B": cams["B"]}
+    out = Pipeline(one).process(1.0, None, pose_from_points(cams["B"], X))
+    assert out.frame.body == "measuring"
+    measuring = compose_view(one, MatConfig(), {}, out.raw, out, None, None, None)
+    out.frame = replace(out.frame, body="ready", body_progress=1.0)
+    ready = compose_view(one, MatConfig(), {}, out.raw, out, None, None, None)
+    assert (measuring != ready).any()  # the "stand straight to measure" line
```


- [ ] **Step 2: Run them and see them fail**

`.venv/bin/python -m pytest -q`. Expected: 4 failures:
- the state test, triangulation ready, and the debug-view hint:
  `AttributeError: 'FrameResult' object has no attribute 'body'`;
- the round trip: `TypeError: ... unexpected keyword argument 'body'`.

- [ ] **Step 3: Implement**

Apply to `vision/pipeline.py`:

```diff
--- a/vision/pipeline.py
+++ b/vision/pipeline.py
@@ -177,7 +177,9 @@
                 _, feet_locked = self._anchor.update(t, joints)  # report only: joints are not moved
         if used != "model" and self._filter is not None:  # the model smooths its own pose numbers
             joints = self._filter(joints, t)
-        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
+        ready = self._model is None or self._model.ready
+        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking, body="ready" if ready else "measuring",
+                            body_progress=1.0 if ready else self._model.progress)
         return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped,
                               people=people, ignored=ignored, dropped=dropped, feet_locked=feet_locked,
                               tracker=used, fit_ms=fit_ms)
```

Apply to `vision/types.py`:

```diff
--- a/vision/types.py
+++ b/vision/types.py
@@ -89,6 +89,8 @@
     rep: int = 0
     flags: list[str] = field(default_factory=list)
     tracking: str = "lost"  # "3d" | "single_cam_A" | "single_cam_B" | "lost"
+    body: str = "ready"  # "measuring" (joints are a rough placement; don't count reps) | "ready"
+    body_progress: float = 1.0  # 0..1 while measuring: share of the standing frames collected
 
     def to_dict(self) -> dict:
         return {
@@ -107,6 +109,8 @@
             "rep": self.rep,
             "flags": list(self.flags),
             "tracking": self.tracking,
+            "body": self.body,
+            "body_progress": self.body_progress,
         }
 
     @classmethod
@@ -127,6 +131,8 @@
             rep=d.get("rep", 0),
             flags=d.get("flags") or [],
             tracking=d.get("tracking", "lost"),
+            body=d.get("body", "ready"),
+            body_progress=d.get("body_progress", 1.0),
         )
```

Apply to `vision/debug_view.py`:

```diff
--- a/vision/debug_view.py
+++ b/vision/debug_view.py
@@ -203,6 +203,8 @@
     hud = []
     if frame is not None:
         hud.append(f"tracking: {frame.tracking}")
+        if frame.body == "measuring" and frame.tracking != "lost":
+            hud.append(f"stand straight to measure... {frame.body_progress * 100:.0f}%")
     if skew:
         hud.append(f"pair skew median {skew['median_ms']:.0f} ms  p95 {skew['p95_ms']:.0f} ms  unpaired {skew['unpaired_pct']:.0f}%")
     if out is not None and out.tri is not None and out.tri.ok.any():
@@ -226,7 +228,7 @@
         else:
             body = (view.rig.update(frame.t, frame.joints, frame.tracking, frame.conf, feet)
                     if frame is not None else None)
-            if view.rig.progress < 1:
+            if view.rig.progress < 1 and (frame is None or frame.body != "measuring"):
                 hud = hud + [f"measuring body... {view.rig.progress * 100:.0f}% (stand on the mat)"]
             if not view.rig.constraints:
                 hud = hud + ["body rules off (c to turn on)"]
```

Apply to `vision/README.md`:

```diff
--- a/vision/README.md
+++ b/vision/README.md
@@ -166,4 +166,13 @@
 for frame in replay("data/recordings/<file>.jsonl"):
     frame.joints  # (33, 3) metres, mat frame; NaN where unknown
     frame.tracking  # "3d" | "single_cam_A" | "single_cam_B" | "lost"
+    frame.body  # "measuring" | "ready"
+    frame.body_progress  # 0..1 while measuring
 ```
+
+With one camera the body is measured first, from about 40 frames (1.3 s) of the person standing
+straight; crouching or moving frames don't count. Until then `frame.body` is `"measuring"` and
+`frame.joints` are only a rough placement: fine for showing someone is there, not for reps, depth or
+form rules. Prompt the person to stand straight while it says `"measuring"` and a person is tracked;
+start counting when it turns `"ready"`. It stays `"ready"` through a short loss and goes back to
+`"measuring"` for a new person (after 5 s with nobody). Two-camera triangulation is always `"ready"`.
```


The rig's own "measuring body..." line is hidden while the one-camera body is measuring, so the view shows one
hint, not two.

- [ ] **Step 4: Run all tests**

`.venv/bin/python -m pytest -q` → 245 passed.

- [ ] **Step 5: Commit**

```bash
git add vision/types.py vision/pipeline.py vision/debug_view.py vision/README.md vision/tests/test_mono.py \
        vision/tests/test_types.py vision/tests/test_debug_view.py
git commit -m "vision: FrameResult says whether the body is measuring or ready, for the app to prompt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Verify on the recordings and update the docs

**Files:**
- Modify: `docs/vision-branch-context.md`

- [ ] **Step 1: Run the full suite**

`.venv/bin/python -m pytest -q` → 245 passed.

- [ ] **Step 2: Stability report**

`.venv/bin/python -m vision.stability_eval data/recordings/20261011-{060807,054226,055039}.jsonl --height-cm 170`.
Expected: the "after" rows of the table above, within rounding.

| Session | Still p90 | Moving p90 | Jumps | Ankle p99 | Floating | 2D match | Lag |
|---|---|---|---|---|---|---|---|
| `060807` | 0.56 | 2.32 | 54 | 6.6 | 0 | 7.1 | 1.70 |
| `054226` | n/a | 2.90 | 18 | 4.8 | 0 | 9.1 | 1.90 |
| `055039` | 0.79 | 2.31 | 45 | 3.9 | 0 | 9.2 | 1.60 |

Restarts are 0 everywhere. Fit time is about 6 ms. Compare with the spec's success criteria and the addendum;
report any miss as a miss.

- [ ] **Step 3: Two cameras identical**

```bash
.venv/bin/python - <<'EOF'
import numpy as np
from vision.config import load_mat
from vision.pipeline import Pipeline
from vision.replay import cams_from_header, iter_records, read_header
path = "data/recordings/20261011-022822.jsonl"
cams = cams_from_header(read_header(path))
for tr in ("triangulate", "model"):
    pipe, J = Pipeline(cams, mat=load_mat(), tracker=tr), []
    for fr, raw, skew, people in iter_records(path):
        get = (lambda n: people.get(n, [])) if people is not None else (lambda n: raw.get(n))
        o = pipe.process(fr.t, get("A"), get("B"), skew)
        J.append(np.r_[o.frame.joints.ravel(), [float(bool((o.feet_locked or {}).get("l")))]])
    ref = np.load(f"/tmp/mira-twocam-{tr}.npy")
    print(tr, "identical" if np.array_equal(ref, np.array(J), equal_nan=True) else "DIFFERENT")
EOF
```

Expected: `triangulate identical`, `model identical`.

- [ ] **Step 4: Update `docs/vision-branch-context.md`**

- **Camera B:** it is at ~1.2–1.25 m in the calibrations from `055039` on, not 0.83 m.
- **One-camera pipeline description:**
  - the body is measured from 40 standing frames;
  - the 2D-motion gate is a hold inside the solve;
  - planted feet are shown only;
  - `FrameResult.body` / `body_progress`.
- **Measured numbers:** replace them with the table above.
- **Tools:** add `python -m vision.stability_eval` to "How to run".
- **Open issues:** add that the calibrations' scale disagrees between sessions. Measured from the picture with
  the feet on the floor, the same person is 1.77 m tall in `060807` and 1.45–1.47 m in `054226` / `055039`.
  The entered height can match only one of them; this is the next thing to look at, a calibration (zoom)
  problem.
- **Test count:** 245.

- [ ] **Step 5: Commit**

```bash
git add docs/vision-branch-context.md
git commit -m "docs: branch context after the steadier one-camera body

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then one final review against the review focus above, one fix wave, and one scoped re-review.
