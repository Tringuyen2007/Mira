# Vision: single camera (camera B only) (design)

Date: 2026-10-11 · Owner: Eric · Branch: `vision/cameras-to-3d`
Supersedes the two-camera setup for the MVP. Two-camera code stays in the repo (unused by default).

## Why

Two-camera triangulation is not paying off under the rig's constraints (cable length, room size, narrow
~48° webcams): calibration from floor markers alone leaves the cameras tilted (4–8.5° body lean), the
two views disagree on semantic joints (shoulders rejected 72% of frames), and in the latest session
camera A, at 3.1 m, cut off the person's head in 53% of frames so half the session was one camera anyway.
Eric's decision: keep camera B, drop camera A.

Camera B: front-diagonal (~45°) to the mat. Movements that matter: squat/lunge depth and knees, rep
counting and tempo, arm presses and raises, balance / on-mat / falls.

## Goal

Every tool works with camera B alone, and produces a smooth, plausible 3D body in the mat frame from one
view, with honest limits (depth along B's line of sight is estimated).

## How one camera gives 3D

1. **Floor fixes position and scale.** B's calibration gives the floor; rays through the feet meet it, so
   the person's (x, z) and scale are known in metres (feet-on-floor, `select.floor_position`).
2. **MediaPipe's world landmarks give shape and depth.** Each frame MediaPipe estimates a metric,
   hip-centred 3D pose; rotated into the mat frame with B's rotation (`world_to_mat`) it is the depth cue.
3. **The body-model fit** (`vision/tracker.py`) poses the measured body to B's 2D points (as now) plus a
   new soft prior pulling each joint toward MediaPipe's world-landmark shape (relative to the hips), plus
   the floor contact, joint ranges and smooth motion. Depth comes from the world-landmark prior and the
   floor; image-plane accuracy from the 2D points.
4. **Body proportions** come from the person's height (entered once: `--height-cm`, saved in
   `config/person.yaml`) scaling the median world-landmark bone lengths over the first ~40 frames
   (world landmarks are roughly metric but their scale drifts with distance; the height fixes it).

## Changes

- **Camera set:** tools accept a single configured camera. `vision.cams` can assign just B; `LiveSource`,
  `Pairer` (already supports `other=None`), person selection (single camera: candidates by floor
  position near the mat; the two-camera pairing path skipped), `debug_view` (one camera panel, 3D view
  and top view), `checks`, `calib_extrinsics` work with one camera. `calib_refine` explains it needs two
  cameras and exits.
- **Pipeline:** with one calibrated camera, `tracker="model"` is the default and uses the single-camera
  fit; the triangulation stages are skipped. Cleaner's single-camera fallback is used only until the body
  is measured.
- **ModelTracker:** `measure_from_world(...)` (world landmarks + height) as an alternative to measuring
  from triangulation; a world-landmark prior term; works with one camera's evidence.
- **checks (single camera):** drop two-camera checks (triangulation angle, pair skew, marker distances,
  body reprojection across cameras); keep fps, pose time, standing straight, foot height, height estimate,
  camera-not-moved; add "fit residual (px)" and "depth jitter while still (cm)".
- **Avatar:** draws the model output (as with `--tracker model`).

## Placement guidance for B (for Eric)

Whole body visible anywhere on the mat (head to feet, the green person box in `calib_extrinsics`), and at
least two markers — ideally one at each end — for a solid calibration and zoom.

## Success criteria (measured on camera-B-only replays of existing recordings, then a new session)

- Body present in ≥ 99% of frames where B sees the person.
- Bone lengths fixed (< 1 mm std).
- Jitter while still < 1 cm (all joints).
- No lag on fast moves (synthetic swing, as in the tracker prototype): wrist within 5 cm.
- Fit time < 8 ms/frame.
- Position on the mat (hip x, z) agrees with the two-camera triangulation, where both exist, within
  ~10 cm median (an honest check of the single-camera depth estimate).
- Height estimate within 5% of the entered height.

## Out of scope

iPhone/ARKit input; re-adding a second camera; hands/fingers.
