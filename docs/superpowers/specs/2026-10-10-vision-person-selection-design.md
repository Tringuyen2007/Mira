# Vision: track only the person at the mat (design)

Date: 2026-10-10 · Owner: Eric · Branch: `vision/cameras-to-3d`
Builds on: `2026-10-10-vision-cameras-to-3d-design.md` and `2026-10-10-vision-marker-zoom-placement-design.md`.

## Why

Rig testing with `debug_view` and `checks` showed two detection problems:

1. **A hand near a camera produces a skeleton.** MediaPipe runs on each camera alone; a hand close to the lens looks like a large body part and gets a pose. That camera's junk is then triangulated with the other camera's good detection.
2. **With several people in view, the wrong one can be tracked.** MediaPipe is set to `num_poses=1` and returns whoever is most prominent, not the person at the mat.

## Goal

Track exactly one person: the one on or next to the mat. Ignore people elsewhere, and ignore a camera whose detection disagrees with the other camera (hand over the lens, wrong person), carrying on with the good camera.

**In scope:** cropping each camera's image to the mat area before pose; up to 3 people per camera; choosing the person at the mat with stickiness; dropping a disagreeing camera per frame; debug view and recording changes.

**Out of scope:** identity across sessions (face/re-ID); hand landmark tracking; changing the triangulation, flip handling, cleanup or smoothing stages.

## Decisions

1. **Who to track:** a person whose feet are on the mat or within **1 m** of it (so tracking works while walking up; the MVP's arrival/on-mat detection needs this). If several qualify, the one **closest to the mat centre**, with stickiness: switch only when another qualifying person is at least **0.3 m** closer to the centre for **0.5 s**.
2. **Crop to the mat area.** Each calibrated camera's image is cropped to the projection of a box: the mat footprint grown by 1 m on every side, from the floor to 2.1 m, plus 10% padding, clipped to the image. Computed once from the calibration (MediaPipe's VIDEO-mode tracking prefers a steady input). Landmarks are mapped back to full-frame pixels, so everything downstream is unchanged. The crop is internal to pose detection only: every view (debug view, calib tools, recordings' frame sizes) keeps showing the full camera picture, with the attended area drawn as an outlined box. Uncalibrated cameras use the full frame.
3. **Up to 3 people per camera** (`num_poses=3`, configurable). If `checks` shows capture or pose fps below 20, the default drops to 2.
4. **Same person across cameras = consistent midpoints.** A pairing of a person in A with a person in B is consistent when the triangulated hip, shoulder and ankle **midpoints** reproject within **25 px** (median) in both views. Midpoints are unaffected by MediaPipe's left/right flips, which the pipeline resolves later.
5. **Disagreeing camera is dropped, not averaged.** If no consistent pairing is at the mat, each camera's candidates are placed on the floor on their own (feet ray ∩ floor) and the camera whose candidate is at the mat and nearest the current track is kept; the other camera is ignored for that frame. The pipeline's existing single-camera fallback then takes over.
6. **Recordings keep every person found**, so selection can be re-tuned by replaying. Old recordings (one person per camera) still replay.

## Components

### 1. Mat crop — `vision/roi.py` (new)

- `mat_roi(cam: CameraConfig, mat: MatConfig, margin_m=1.0, height_m=2.1, pad=0.10) -> (x0, y0, x1, y1) | None`: integer full-frame pixel box; None if the camera is uncalibrated or the box is not in front of the camera (then: full frame).
- `crop(frame, roi) -> (crop_img, (x0, y0))` and `uncrop(lm2d, offset) -> lm2d` (add the offset; MediaPipe's normalised coordinates are scaled by the crop size first).

### 2. Pose estimator — `vision/pose.py`

- `PoseEstimator(kind, num_poses=3)`; `detect(frame, t, cam, roi=None) -> list[PoseResult]` (empty list when nobody is found); each PoseResult in full-frame pixels. `PoseResult` itself is unchanged (one person).
- `PoseWorker(camera, out, model, roi=None, num_poses=3)` puts a `CameraPeople(cam, t, people: list[PoseResult])` on the queue (`cam`/`t` let the existing `Pairer` pair them unchanged). `.latest` becomes `(frame, CameraPeople)`.
- `LiveSource` computes each camera's ROI from the calibration and passes it to its worker; `stats()` gains `"roi"` per camera.

### 3. Person selection — `vision/select.py` (new)

`PersonSelector(cams, mat, vicinity_m=1.0, pair_px=25.0, switch_m=0.3, switch_s=0.5)` with
`select(t, a_people, b_people) -> Selection(a: PoseResult | None, b: PoseResult | None, ignored: dict[str, list[PoseResult]], dropped: str | None, position: np.ndarray | None)`.

- **Floor position of one camera's candidate:** rays through the visible (vis ≥ 0.5) ankle and heel pixels (27–30) intersected with the planes y = 0.08 m (ankles) and y = 0.04 m (heels); median (x, z). None without visible feet. (No hip fallback: with cameras at about hip height the hip ray is nearly parallel to a hip-height plane and the position is meaningless. A single plane for ankles and heels was 20 cm off at the rig's low camera angle.)
- **Distance to the mat:** 0 inside the mat rectangle, else the distance to its edge, on the floor.
- **Steps:**
  1. Uncalibrated A or B → return the first candidate per camera (today's behaviour).
  2. All A×B pairings: triangulate the midpoints; keep consistent pairings (decision 4); position = triangulated hip midpoint (x, z).
  3. Qualifying = position within `vicinity_m` of the mat. Choose with stickiness (decision 1): the qualifying candidate nearest the current track position (≤ 0.5 m) is "current"; a challenger replaces it after being ≥ `switch_m` closer to the centre for `switch_s`.
  4. No qualifying consistent pairing → single-camera candidates at the mat (floor position), nearest the current track first; return that camera's person and `dropped` = the other camera if it had any candidates.
  5. Nothing at the mat → `Selection(None, None, ...)` (tracking becomes "lost").
- `ignored` lists every candidate not chosen, per camera, for the debug view.

### 4. Pipeline — `vision/pipeline.py`

- `Pipeline.process(t, a, b, skew_s=None)` accepts per camera a `PoseResult`, a `CameraPeople`, a list of PoseResults, or None. Lists go through the `PersonSelector` first; a single PoseResult is used as before (old recordings, tests).
- `PipelineOutput` gains `ignored` and `dropped` (from the Selection) and `people` (all candidates, for recording).

### 5. Recording and replay — `vision/record.py`, `vision/replay.py`

- Frame lines gain `"people": {"A": [PoseResult dicts], "B": [...]}`; `"raw"` keeps the selected person per camera (back-compatible).
- `replay_outputs(..., retriangulate=True)` re-runs selection when `people` is present; otherwise uses `raw` as today.

### 6. Debug view — `vision/debug_view.py`

- The full camera picture is always shown (never cropped). The attended area is drawn as a clearly visible outlined box (cyan, 2 px, labelled "tracking area"), so it is obvious what the camera is paying attention to.
- The chosen person per camera as today (green 2D, magenta 3D); ignored people drawn thin grey with "ignored".
- HUD: "ignoring camera A (disagrees)" when `dropped` is set; "N people seen, tracking 1".

## Error handling

- ROI cannot be computed (uncalibrated, box behind camera) → full frame, as today.
- No one at the mat → "lost", as today when nobody is detected.
- Old recordings without `people` → replayed with the stored single person per camera.

## Success criteria (synthetic tests, then the rig)

- A second person 2.5 m from the mat is never tracked; a person on the mat is.
- Two people on the mat: the one nearer the centre is tracked; 0.2 s of jitter does not switch; a person who becomes 0.5 m closer for 0.6 s takes over.
- A junk "hand" detection in camera A only (no consistent pairing, no feet on the floor at the mat): camera A is dropped, B's person is returned, `dropped == "A"`.
- `uncrop(crop(...))` maps landmarks back to the exact full-frame pixels; ROI covers the projected box.
- Old recordings replay unchanged; new recordings replay with re-selection.
- Rig: with a hand over one lens, the 3D skeleton stays on the person (single-camera mode) instead of jumping; a bystander beside the mat is ignored; `checks` still reports ≥ 20 fps per camera.
