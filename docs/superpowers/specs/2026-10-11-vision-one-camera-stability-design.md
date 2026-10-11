# Vision: steadier one-camera body (design)

Date: 2026-10-11 · Owner: Eric · Branch: `vision/cameras-to-3d`
Follows `2026-10-11-vision-single-camera-design.md` and the steady-one-camera plan.

## Why

Replays of the three newest one-camera sessions (`20261011-054226`, `-055039`, `-060807`) show the body
popping and shaking much more than MediaPipe's own noise explains. Three causes in our pipeline, each
measured on those replays:

1. **The body is measured from whatever happens in the first ~1.3 s.** `ModelTracker` takes scale, bone
   lengths and lean from the first 40 frames, standing or not. In `055039` the person was crouching
   (knee ~91°): scale 1.50 instead of ~1.06 from standing frames. In `060807`: 1.20 instead of ~1.05. The
   floor-placed body (`mono_joints`) then sits 51–275 px (median) away from B's 2D points; the fitted body
   7–17 px.
2. **The foot anchor pulls the fit toward that wrong body.** Anchors come from the floor-placed joints:
   9 / 21 / 36 cm (median) from the fitted ankle. Those joints wobble more than the 3 cm release
   distance, so the anchor switches ~3 times a second per foot (~300 times in 52 s). Each switch moves the
   fitted ankle 4.5 cm (median) against 1.3 cm without one. Restarts from the floor-placed joints
   (`RECOVER_PX`) snap the body by up to 1 m (25 restarts in `055039`).
3. **The 2D-motion gate breaks the kinematic chain.** It blends each of the 32 pose numbers separately
   after the fit, so the hips can move while the leg angles are held: the foot swings with the pelvis (up
   to 37 cm from the smoothed pose, sometimes 12 cm off the floor). For ankles the gate makes spikes worse
   (p99 8.3 → 10.0 cm).

A monkeypatched prototype of the three fixes below (in replays) cut one-frame jumps above 8 cm from
128 / 125 / 482 to 93 / 22 / 68 with no added lag and no extra fit time.

## Goal

The one-camera body holds still when the person is still and stops popping, with no added lag. Two-camera
behaviour is unchanged.

## Changes

### 1. Measure the body only from standing frames (`vision/tracker.py`, `vision/mono.py`)

- New `mono.is_standing(J) -> bool`: the test `upright_rotation` already uses (both knees above
  `UPRIGHT_KNEE_DEG` = 150°, shoulders–hips–knees above `UPRIGHT_HIP_DEG` = 160°). `upright_rotation`
  calls it, so the test lives in one place.
- One camera only: while the shape is unmeasured, `ModelTracker.update` keeps a frame as a measuring
  sample only when `is_standing` holds for it. Shape, `upright` and `world_scale` all come from the same
  40 standing samples. Two cameras keep measuring from every usable frame, as now.
- Until 40 standing frames have been seen, the pipeline shows the floor-placed body, as it does while
  measuring now. There is no timeout: someone who never stands straight is never measured.
- `debug_view` (one camera, not measured yet) shows `stand straight to measure... N%` from
  `ModelTracker.progress`.
- Reset (`FORGET_S`, a new person) starts measuring again.

### 2. Planted feet no longer pull the one-camera fit (`vision/pipeline.py`)

- One camera with the model tracker: no anchors are passed to `ModelTracker.update`, and `FootAnchor` no
  longer edits the floor-placed joints before the fit. The fit already has its own floor-contact term.
- `FootAnchor` runs on the fitted output, **report only**: it sets `feet_locked` (the white squares in
  `debug_view`, the avatar rig's foot lock) but does not move any joint, so bone lengths stay exact.
- Recovery restarts still start from the floor-placed joints. With the scale fixed those joints are
  closer, and the number of restarts is reported (below).
- Two cameras (triangulation, and the trial `--tracker model`) are unchanged: anchors still edit the
  triangulated joints and feed the fit.

### 3. Gate inside the solver (`vision/tracker.py`, one camera only)

- Before each solve, a gain `g_j` is worked out for each pose number from B's 2D motion, as now: the
  median motion of the landmarks it moves, divided by `GATE_FULL_PX`, clipped to `[GATE_MIN_GAIN, 1]`.
  The root position gets a floor: `g_root ≥ GATE_ROOT_MIN` (prototype 0.3). This replaces the
  `GATE_PASS_M` pass-through, so walking toward B is still followed.
- The temporal-prior residual of number `j` is multiplied by `g_j ** -GATE_POWER` (prototype 0.5). A
  number whose landmarks are still in 2D is held strongly; one that moves is free. Because the hold is
  part of the solve, floor contact, joint limits and the 2D fit all still apply to the result.
- The post-fit `_gate` blend is removed. One Euro smoothing after the solve stays (`SINGLE_SMOOTH_TOO`).
- Starts with no hold: the first frame, after a gap longer than `GATE_GAP_S`, and recovery solves.
- **Tuning:** `GATE_MIN_GAIN`, `GATE_POWER`, `GATE_ROOT_MIN`, and for one camera `TEMPORAL_PX` and the
  One Euro cutoffs, are tuned on the three recordings to meet both the still-jitter and the spike
  targets below. If no setting meets both, implementation stops and the best tradeoffs go to Eric.
  Targets are not loosened silently.

### 4. Stability report (`vision/stability_eval.py`, new)

`python -m vision.stability_eval <f>... [--height-cm 170]` replays one-camera recordings (B only, the
recorded selected person) and prints, per file:

- **still jitter**, median / p90 per-frame motion (cm) of the body joints in frames where B's 2D landmarks
  are still: median motion < 2.5 px for 15 frames in a row;
- **moving p90**, per-frame motion over all fitted frames;
- **jumps**, frames where a body joint moves more than 8 cm in one frame;
- **ankle p99**, per-frame ankle motion;
- **floating**, frames whose lowest foot point is more than 5 cm above the floor;
- **reprojection**, median 2D distance (px) between the output joints and B's landmarks;
- **lag**, of hip height against a reference run (`--against` another setting), from cross-correlation;
- **deepest knee**, the 2nd percentile of the smaller knee angle;
- **restarts**, and **fit time** (median ms).

"Body joints" means shoulders, elbows, wrists, hips, knees, ankles and toes. This is the measure for the
success criteria; `mono_eval` (comparison with two-camera triangulation) stays as it is.

## Success criteria

Measured with `stability_eval` at `--height-cm 170`. "Base" is the branch before this change.

| Session | Still p90 (cm) | Moving p90 (cm) | Jumps > 8 cm | Ankle p99 (cm) |
|---|---|---|---|---|
| `060807` | ≤ 0.6 (base 1.70) | ≤ 2.7 (3.54) | ≤ 95 (128) | ≤ 7.5 (10.0) |
| `054226` | n/a (never still) | ≤ 3.8 (7.40) | ≤ 30 (125) | ≤ 7.0 (16.0) |
| `055039` | ≤ 2.0 (9.09) | ≤ 3.2 (14.35) | ≤ 85 (482) | ≤ 7.0 (55.1) |

Also:

- **Reprojection** is no worse than base in every session; `055039` ≤ 10.5 px (base 18.8).
- **Floating** frames are no more than base in every session.
- **Lag:** hip height 0 frames behind base.
- **Fit time:** median ≤ 8 ms per frame.
- **Synthetic tests** pass with the new gate: depth shake held when still in 2D, real sideways movement
  followed, walking toward B keeps up (0.5 and 1.2 m/s), catching up after a gap, scale the same after a
  reset.
- **New synthetic tests:** a body that crouches for its first 40 frames, then stands, is measured at the
  right scale. One-camera anchors never reach the fit. A planted foot still shows as locked.
- **Two cameras:** a two-camera recording in triangulation mode gives identical frames before and after.
- **Full suite:** all tests pass, except the direct `_gate` unit test, which is replaced with a test of the
  in-solver hold.

**Reported, no target:**

- **Deepest knee:** the prototype read `055039`'s deepest squat shallower (39° → 64°) once the scale was
  fixed. Without ground truth it is unknown which is right.
- **Restarts** per session.

## Out of scope

- Sliding-window optimisation, a Kalman filter, learned smoothing (SmoothNet), temporal 3D lifters, and
  WHAM / GVHMR (options 2–6 of the research).
- Filtering the 2D points before the fit (tried; mixed results).
- Raising camera B.
- Ground-truth squat depth.

## Open

- The context doc says B is 0.83 m high. Calibrations from `055039` on put it at ~1.2–1.25 m. Update the
  doc with this work.
