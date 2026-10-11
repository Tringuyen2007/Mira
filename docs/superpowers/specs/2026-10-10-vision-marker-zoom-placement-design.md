# Vision: zoom from markers, placement overlay, camera-moved alarm (design)

Date: 2026-10-10 · Owner: Eric · Branch: `vision/cameras-to-3d`
Builds on: `2026-10-10-vision-cameras-to-3d-design.md` (mat frame, markers, pipeline unchanged).

## Why

Rig testing showed three problems:

1. **Checkerboard lens calibration is impractical.** The webcams are wired to the laptop that shows the board; carrying the laptop in front of mounted cameras for 25 views is slow and knocks the cameras over.
2. **Cameras were placed by eye.** There is no way to put them at a repeatable, symmetric spot. The webcams (Logitech C270 + Brio 101, ~48° × 28° at 1280×720) are narrow, so the planned 2.8 m spot cannot see a whole person; they were moved to ~4.5 m by hand, and the existing hints still point to 2.8 m.
3. **A bumped camera goes unnoticed.** Calibration is a one-time measurement; if a camera moves afterwards the skeleton shifts silently.

## Goal

Calibrate with no checkerboard and no tape measure: mount the cameras roughly, let the floor markers measure each camera's zoom and pose, follow an on-screen overlay to a saved symmetric target, refine with ~20 s of movement, and get warned if a camera moves later.

**In scope:** removing checkerboard lens calibration; focal length from markers; focal refinement in the bundle adjustment; placement overlay (crosshair, aim dot, ghost mat, move/turn instructions, A/B comparison); lock-target key; camera-moved alarm in the live tools; height check.

**Out of scope:** estimating lens distortion or principal point; automatic re-calibration when a camera moves; portrait camera mounting.

## Success criteria

- Focal length from a single averaged marker view (all 4 markers, 0.3 px corner noise, camera ~4.5 m away) within 3% of truth on synthetic data.
- Bundle adjustment recovers a focal length that starts 8% wrong to within 2% on synthetic data.
- On the rig: placement instructions converge to green on both cameras; A and B distances from mat centre and heights agree within 5 cm after following them.
- Bumping a camera by ≥ ~0.2° raises the alarm within 5 s; a person walking over markers does not.
- `checks --height-cm H` estimates the person's height within 5%.

## Decisions

1. **Zoom (focal length) from the markers.** Model: one focal length f (fx = fy), principal point at the image centre, zero distortion. These webcams are narrow (~48° wide) with little distortion, so the expected cost versus a checkerboard is ≲ 1 cm in joint position, below MediaPipe jitter.
2. **Checkerboard calibration is removed.** The markers are the only source of K. Deleted: `vision/calib_intrinsics.py`, `vision/tests/test_calib_intrinsics.py`, the `intrinsics_rms` field and the "lens RMS" checks, and the README step. Any K/dist already stored in `config/cameras.yaml` (camera A has one from a checkerboard run) is ignored and replaced on the next `calib_extrinsics` save; `calib_extrinsics` always estimates K itself.
3. **Exact placement does not affect accuracy** (calibration measures where the cameras actually are). Placement targets exist for consistency between sessions and left/right symmetry. Tolerance tightens to **5 cm and 2°** (`config/mat.yaml`).
4. **Targets are locked from the current camera positions,** not typed in. Locked targets live in `config/cameras.yaml` (local to the room), per camera as `target_eye`. Until locked, `mat.yaml`'s `camera_targets` are used.
5. **Alarm warns, never auto-corrects.** Re-solving from partly occluded markers mid-session could make the skeleton jump; re-running `calib_extrinsics` takes ~30 s.
6. **Height is a check, not a scale source.** MediaPipe has no head-top landmark and typed heights are ±3–5 cm, while the markers are exact to the millimetre.

## Components

### 1. Focal length from markers — `vision/markers.py`

`estimate_focal(obs, mat, image_size) -> (FocalEstimate | None, reason | None)`

- Input: averaged marker corners (`average_detections`, as today) for one camera.
- Requires markers at both ends of the mat (one of IDs 0/1 and one of 2/3); otherwise returns a reason ("need markers at both ends of the mat to measure zoom"). Markers at one end only give almost no perspective, so f is unreliable.
- Method: start from a nominal 55° diagonal field of view and the IPPE marker pose, then least-squares over (f, rotation, translation) with the principal point fixed at the image centre and no distortion. The standard deviation of f comes from the fit's covariance (verified on synthetic data to match the observed spread).
- Output: `FocalEstimate(K, f, f_std, hfov_deg, vfov_deg, rms)`. Rejected (reason) if `f_std / f > 3%` or the horizontal field of view falls outside 30–100°.

### 2. `calib_extrinsics` changes — `vision/calib_extrinsics.py`

- No longer requires prior lens calibration.
- Every solve (every 0.5 s), per camera:
  - `estimate_focal` on the averaged corners; on success use its K with zero dist; on failure fall back to the last good estimate this session, else the nominal 55° guess, labelled "zoom guessed (see more markers)". Poses solved with the guess are shown for placement but cannot be saved.
  - then `solve_camera_pose` as today.
- Display per camera: `zoom 48.4° × 28.4°` (or `zoom guessed`).
- On `s`: requires both cameras solved with a non-guessed K. Saves K, dist (zeros), R, t, `marker_obs`, `extrinsics_source = "markers"`.

### 3. Placement overlay — `vision/placement.py` (new), drawn by `calib_extrinsics`

Pure functions (testable without cameras) plus a draw function.

- **Target pose:** `target_pose(target_eye, aim) = look_at_pose(target_eye, aim)` (level, no roll).
- **Crosshair** at the principal point (image centre).
- **Aim dot:** `mat.aim_point` projected with the camera's *current* pose. Dot on crosshair = aimed correctly.
- **Ghost mat:** mat outline and the four marker squares projected with the *target* pose and the current K, drawn green and dashed. When the camera is at the target, the ghost coincides with the detected markers (yellow).
- **Instructions** (`placement_hint`, extended), in the terms of someone standing behind the camera, as two lines:
  - position: "Move 20 cm back · Move 10 cm left · Raise 5 cm" (thresholds 2 cm), green "Position OK" within tolerance;
  - aim: "Turn right 2° · Tilt down 3° · Rotate clockwise 1°" (thresholds 0.5°), green "Aim OK" within tolerance. **Roll is new:** the angle of the camera's image x-axis to the horizontal, so the camera can be levelled.
  - The position line is shown first; fix position before aim (moving changes the aim).
- **Comparison strip** (bottom of the window): for A and B: floor distance from mat centre, height, bearing around the mat (`atan2(x, z)`, A ≈ +45°, B ≈ −45°); plus differences `|dA − dB|`, `|hA − hB|`, `||bearingA| − |bearingB||`.

### 4. Lock target — key `t` in `calib_extrinsics`

- Requires both cameras solved.
- `lock_targets(C_A, C_B) -> (eye_A, eye_B)`: mirror B across the mat's centreline (x → −x), average with A, `eye_A = mean`, `eye_B = mirror(mean)`. The result is exactly symmetric.
- Saved immediately to `config/cameras.yaml` as each camera's `target_eye` (with the usual backup); printed in the terminal. Pressing `t` again re-locks.
- Target resolution: `cams[n].target_eye` if set, else `mat.camera_targets[n]`.

### 5. Focal refinement in the bundle adjustment — `vision/bundle.py`

- For each camera, one extra parameter: a focal scale s (K' = K with fx, fy multiplied by s; principal point fixed).
- Sparsity updated: body and marker residuals of that camera depend on its focal column.
- New guard: focal change ≤ 15% per camera. `BundleReport` gains `focal_before`, `focal_after` per camera and reports them in `summary()`.
- `calib_refine` saves the refined K along with R and t.

### 6. Camera-moved alarm — `vision/moved.py` (new)

`MoveMonitor(cams, detector, interval_s=1.0, threshold_px=3.0, confirm=3, blind_s=10.0)`

- `update(name, image, t)`: at most once per `interval_s` per camera, detects markers and compares each marker seen now and in the saved `marker_obs` by median corner displacement in pixels; the camera's shift is the median over common markers.
- **Moved** when the shift exceeds `threshold_px` on `confirm` consecutive checks (a person partly covering a marker gives one-off outliers or no detection, not a steady shift). Clears when it falls back under threshold on `confirm` consecutive checks.
- **Blind** when no saved marker has been seen for `blind_s` (e.g. someone standing over all visible markers): shown as a grey "can't check camera A (markers hidden)", not an alarm.
- `status(name) -> "ok" | "moved" | "blind" | "unknown"` and `shift_px(name)`.
- Used by:
  - `debug_view` (live): red banner "Camera A has moved (6 px) — re-run calib_extrinsics";
  - `calib_refine`: before the countdown, checks both cameras over ~3 s; refuses to capture if either has moved (`--force` overrides);
  - `checks` (live): a check "camera A not moved" / "camera B not moved" (shift px, target "< 3"); n/a for recordings.
- Threshold rationale: at f ≈ 1425 px, 3 px ≈ 0.12°, ≈ 1 cm at 4.5 m.

### 7. Height check — `vision/checks.py`

- `--height-cm H` (optional). Estimate = median over 3D frames of mean eye height (landmarks 2, 5; y above the mat surface) / 0.936.
- Check "estimated height": value cm, target `H ± 5%`. Omitted when `--height-cm` is not given.
- The "lens RMS" checks are removed.

## Data / config changes

- `CameraConfig`: add `target_eye: np.ndarray | None` (round-trips through `to_dict`/`from_dict`); remove `intrinsics_rms` (ignored if present in an old file). `has_intrinsics` stays (K and dist present) for the downstream tools.
- `config/mat.yaml`: `placement_tolerance: {position_m: 0.05, angle_deg: 2.0}`; `camera_targets` stay as defaults.
- Recording header already carries the cameras dict; new fields ride along.

## Workflow (README)

First time:
1. Mount the cameras roughly where wanted (~4.5 m from mat centre for these webcams), mat empty.
2. `python -m vision.calib_extrinsics`; press `t` to lock targets; follow the instructions/ghost until both say Position OK and Aim OK; press `s`.
3. `python -m vision.calib_refine` (move around for 20 s).
4. `python -m vision.debug_view`, `python -m vision.checks --height-cm <yours>`.

Every session after: step 2 without `t`, then step 3. If the debug view shows "Camera X has moved", repeat steps 2–3.

There is no lens-calibration step.

## Error handling

- Too few markers for zoom → reason shown on that camera's tile; guessed zoom labelled; saving blocked.
- Lock or save with one camera unsolved → message, nothing written.
- Bundle focal guard tripped → REJECTED with the reason, nothing saved (unless `--force`).
- Alarm with no saved `marker_obs` (never ran `calib_extrinsics`) → status "unknown", nothing shown.

## Testing (synthetic, no hardware)

- `estimate_focal`: markers projected with known K/pose at ~4.5 m, 0.3 px noise → f within 3%; only one end visible → reason; very noisy two-marker view → "too uncertain".
- `lock_targets`: output exactly mirror-symmetric; equals inputs when inputs already symmetric.
- `placement_hint`: camera displaced left → "move right"; too far → "closer"; rolled clockwise → "rotate counter-clockwise"; at target → both OK.
- Ghost: projecting the target-pose ghost with the camera at the target equals the projected markers (< 0.01 px).
- Bundle: focal starting 8% wrong recovers within 2%; 20% focal change rejected by the guard.
- `MoveMonitor`: steady 5 px shift → "moved" after 3 checks; single-check outliers and missing markers → not moved; all hidden 10 s → "blind"; rate limit respected.
- Height estimate on a synthetic skeleton of known height within 1%.
- Config round-trip of `target_eye`; an old file with `intrinsics_rms` still loads.
