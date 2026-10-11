# Vision: model-based tracker (fit a body to both cameras) (design)

Date: 2026-10-11 · Owner: Eric · Branch: `vision/cameras-to-3d`
Builds on: cameras-to-3d, person selection, foot anchoring, avatar rig/constraints specs.

## Why

Measured on Eric's recordings (`20261011-022822`, `refine-20261011-024917`):

- The pieces are fine: camera timing skew 2 ms median / 4 ms p95; MediaPipe 2D jitter 2–3 px per frame;
  3D jitter of triangulated joints while standing still 0.6–1.6 cm.
- The two cameras **disagree**, more with height: reprojection error ankles 2.5 px, knees 5.5, hips 8,
  wrists 8, shoulders 12.6, nose 11.3. Above the 15 px cut-off a joint is rejected: shoulders 72% of
  frames, nose 43%, hips 21%, wrists 15%. Rejected joints fall back to one camera's world landmarks or
  are held — the visible popping/freezing.
- Two causes: (1) calibration is right at the floor (markers) but tilted higher up (each camera sees
  only the two near markers; a body-only fit moves the cameras 30–56 cm / 5–7° and halves shoulder
  disagreement); (2) MediaPipe's semantic joints (shoulders, head, wrists) are estimated differently from
  two viewpoints (~9 px shoulders, ~15 px nose even with a body-fitted calibration).
- Constraints (fixed): camera placement is limited by cable length and room size (cameras ~39° apart,
  not perfectly aimed); no calibration board above the floor.

## Goal

A tracker that uses all the evidence from both cameras, tolerates their disagreement and an imperfect
calibration, and outputs a smooth, anatomically plausible body for everyone (`FrameResult.joints`),
without hiding real movement.

**In scope:** per-frame fitting of a kinematic body model to both cameras' 2D detections; body
measurement; soft priors (temporal, joint ranges, floor, planted feet); pipeline integration
(`tracker="model"` default, `"triangulate"` for comparison); avatar draws the model; evaluation tool;
`--model heavy` switch on `checks`/`debug_view`/`calib_refine` with pose time and per-joint disagreement
in `checks`.

**Out of scope:** changing camera placement or calibration procedure; self-calibration of the cameras
during tracking; hands/fingers beyond MediaPipe's hand points as rigid offsets; multiple tracked people.

## Model

- **Skeleton:** pelvis (position 3 + rotation 3), spine/chest rotation (3), neck/head rotation (3),
  per side: shoulder ball joint (3), elbow hinge (1), wrist/hand direction (2), hip ball joint (3), knee
  hinge (1), foot direction (2). ≈ 36 parameters. Rotations as rotation vectors relative to the parent.
- **Measured body:** bone lengths (upper arm, forearm, thigh, shin, shoulder width, hip width, torso,
  neck, heel, foot) as in the rig (median over ~40 good triangulated frames, left/right averaged),
  plus rigid offsets of face points (0–10) in the head frame and hand points (17–22) in the hand frame,
  measured over the same frames. Re-measure on a new person (5 s with nobody tracked) or `b`.
- **Forward kinematics:** parameters → all 33 MediaPipe landmarks in the mat frame.

## Fit (per frame)

Minimise, with `scipy.optimize.least_squares` (TRF, robust `soft_l1`, warm-started from the previous
frame's parameters, few iterations):

- **2D evidence:** for each camera and landmark: (projected model landmark − detected 2D point),
  weighted by MediaPipe visibility × a per-landmark trust (lower for nose/eyes/shoulders, whose
  definition varies with viewpoint). Robust scale ~10 px so a far-off point counts for less.
- **Temporal prior:** parameters stay close to the previous frame's (weight higher for joints whose
  detections are low-visibility), so motion is smooth but real movement wins.
- **Joint ranges (soft):** elbows/knees 0–150°, one way; thigh and spine within realistic ranges;
  penalties grow outside.
- **Floor:** heel/toe not below the mat surface.
- **Planted feet:** when the FootAnchor says a foot is planted, a pull toward its planted position
  (replaces locking the joints).
- **One camera only:** the same fit with one camera's evidence plus the priors (depth carried from the
  previous frame) — replaces the world-landmark fallback.

Until the body is measured, the output is the current triangulation pipeline.

## Integration

- `Pipeline(cams, ..., tracker="model")` (default) or `"triangulate"` (current behaviour). Person
  selection, flip handling (left/right labelling) and pairing are unchanged and run before the fit.
- `FrameResult.joints`: model landmarks (same 33 × 3 format, mat frame). `PipelineOutput.tri` keeps the
  raw triangulation; recordings keep the 2D detections, so replays can re-run either tracker.
- `checks` keeps measuring the raw triangulation (bone stds, reprojection, standing straight) so
  calibration problems stay visible, and adds model-fit residual per joint group and fit time.
- The 3D avatar draws the model output directly; `c` switches to the old rig for comparison.
- `--model heavy` (MediaPipe heavy) on `checks`, `debug_view`, `calib_refine`; `checks` reports pose time
  and per-joint camera disagreement so full vs heavy can be compared on the rig.

## Evaluation and success criteria

A tool (`python -m vision.tracker_eval FILE...`) replays recordings with each recording's own
calibration and prints, for the model tracker vs the current triangulation:

- per-joint-group 2D residual (px) in each camera and the fraction of frames with a usable joint;
- 3D jitter while still (cm), target **< 1 cm** for all groups;
- lag on fast motion (cross-correlation peak of wrist/ankle velocity vs unsmoothed triangulation),
  target **≤ 2 frames**;
- bone-length std, target **< 1 mm**;
- frames with no body while tracking, target **0** beyond genuinely lost frames;
- fit time, target **< 8 ms/frame** (median), p95 < 15 ms.

Targets are checked on the four recordings at prototype time; numbers that can't be met are reported
with the trade-off before the plan is written.

## Error handling

- Fit fails or diverges (non-finite, residual far above the previous frame's): fall back to the
  previous pose carried by the pelvis (one frame), else to triangulation for that frame.
- Body not measured / new person: triangulation output while measuring.
- Both cameras empty: tracking "lost" as now.
