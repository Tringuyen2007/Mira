# Vision: cameras → 3D skeleton (design)

Date: 2026-10-10 · Owner: Eric · Branch: `vision/cameras-to-3d`
Source docs: `Mira-MVP.pdf` (spec v1.4, sections 3, 5, 6) and `ERIC.md` (hours 0–10). This design deviates from them where noted.

## Goal

Turn the two USB webcams into a calibrated, inspectable live 3D skeleton in the mat frame, and give Eric the tools to judge whether the tracking is good: a live debug view, numeric sanity checks, and record/replay so calibration can be iterated on recorded data.

**In scope:** camera selection, capture, per-camera MediaPipe pose, frame pairing, lens calibration (screen checkerboard), floor-marker extrinsics, body-based bundle-adjustment refinement, triangulation with cleanup and fallbacks, One Euro smoothing, `FrameResult` contract, recorder, replay (with re-triangulation), debug viewer, sanity checks.

**Out of scope (later passes):** joint angles, COM/physics/weight split, rep engine, rules, gestures, on-mat/arrival, fall detection, bone-length locking, `Engine` start/stop/events API, server integration.

## Success criteria

- Both webcams stream simultaneously at ≥ 20 fps each at 1280×720 on the M4 Pro, pose running on both.
- Lens calibration RMS < 0.5 px per camera.
- After refinement: mean body reprojection error ≤ ~5 px; triangulated marker-centre distances within 2 cm of the known geometry (ID0↔ID2 1.52 m, ID0↔ID1 0.40 m).
- Standing still: thigh and shin length std < 1 cm; standing knee angle 170–180°.
- Debug view makes bad 3D obvious (reprojected 3D joints drawn over the 2D detections).

## Decisions and deviations from the source docs

1. **No ChArUco board.** Replaced by body-based bundle adjustment (option B): floor markers give the initial camera poses, the frame, and the scale; ~20 s of the person moving on the mat refines both poses jointly.
2. **Lens calibration uses a checkerboard shown on a screen** (laptop/iPad/monitor), square size measured with a ruler.
3. **Mat frame is right-handed** (the spec's "x right +" is ambiguous and, read as patient's right, would be left-handed, which mirrors skeletons in Three.js/ARKit):
   - origin: mat centre, on the mat surface; **+y up**; **+z toward Mira** (front); **+x toward the patient's left** (= Mira's/cameras' right), i.e. the ID 0/2 side.
   - "Front-left"/"back-left" in the marker PDFs = patient's left when standing facing Mira = +x. Camera A (front-left) is on the +x side.
   - The team must be told this when the contract is posted.
4. **Bone-length locking deferred:** it would mask the errors this pass exists to expose.
5. **Cameras are chosen by index via a tiled picker** (`vision.cams`), because the built-in camera and iPhone Continuity Camera also enumerate and shift indices.
6. **Exposure lock:** OpenCV's AVFoundation backend generally ignores exposure/focus/FOURCC properties. We request them, then measure and display the *actual* fps; if fps sags, lock exposure with the vendor tool (Logitech) instead.

## Geometry (config/mat.yaml)

Mat 1.73 m (z) × 0.61 m (x). Markers: ArUco `DICT_4X4_50`, 0.17 m black square, inset 0.02 m from both edges → centre 0.105 m from each edge. Marker top edge points toward +z.

| ID | Corner | Centre (x, z) m |
|---|---|---|
| 0 | front-left | (+0.20, +0.76) |
| 1 | front-right | (−0.20, +0.76) |
| 2 | back-left | (+0.20, −0.76) |
| 3 | back-right | (−0.20, −0.76) |

ArUco returns corners in the marker's own order TL, TR, BR, BL. Seen from above with +z at the top, +x is to the left, so with h = 0.085: TL (cx+h, 0, cz+h), TR (cx−h, 0, cz+h), BR (cx−h, 0, cz−h), BL (cx+h, 0, cz−h).

Target camera placement (for placement hints): A at (+1.98, 0.85, +1.98), B at (−1.98, 0.85, +1.98) (2.8 m from centre, ±45°), both aimed at (0, 0.8, 0). OK when within ±10 cm and ±5°.

## Coordinate conventions (vision/frames.py)

- Extrinsics stored per camera as `R`, `t` mapping **mat → camera**: `X_cam = R @ X_mat + t` (OpenCV convention: x right, y down, z forward).
- Camera centre in mat frame: `C = −Rᵀ t`.
- MediaPipe world landmarks (hip-centred, metres) use camera-aligned axes (x right, y down, z away from camera), so `Rᵀ @ world` gives mat-frame offsets.
- One tested function set: `project(points_mat, cam) → px`, `cam_center(cam)`, `world_to_mat(world_lm, cam)`, `look_at_pose(eye, target)` (used by tests and placement hints).

## Components

All in package `vision/`, Python 3.11 venv at `.venv`, deps in `vision/requirements.txt` (opencv-contrib-python, mediapipe, numpy, scipy, pyyaml, pytest). MediaPipe `.task` models are downloaded by `vision/models/download.py` into `vision/models/` (gitignored).

| Module | Responsibility |
|---|---|
| `types.py` | `FrameResult` (full contract from ERIC.md; physics/rep fields default `None`/0/`"idle"`), `PoseResult` (per-camera: `cam`, `t`, `lm2d` (33,2) px, `vis` (33,), `world` (33,3) or None-when-no-person). |
| `config.py` | Load/save `config/mat.yaml` and `config/cameras.yaml` (index, name hint, resolution, `K`, `dist`, `R`, `t`, calibration metadata). Saving backs up the previous file to `config/backups/`. |
| `frames.py` | Conventions above. |
| `capture.py` | `Camera` thread: `cv2.VideoCapture(index, CAP_AVFOUNDATION)`, request MJPG 1280×720@30, grab loop, stamp `time.monotonic()` at grab, keep only latest frame + sequence number, measure actual fps. |
| `pose.py` | `PoseWorker` thread per camera: MediaPipe PoseLandmarker (VIDEO mode, `full` default, `--model heavy` option), consumes the latest frame, emits `PoseResult`, measures inference ms. |
| `pairing.py` | `Pairer`: keeps the last few `PoseResult`s per camera; for each new A result, picks the nearest-time B result; pairs if skew ≤ 33 ms, else emits singles. Running skew stats (median, p95, drop rate). |
| `triangulate.py` | Undistort (`cv2.undistortPoints` with `P=K`), DLT via `cv2.triangulatePoints`, per-joint: skip if vis < 0.5 in either view; reproject, reject if error > 15 px; triangulation angle per joint. |
| `cleanup.py` | Fallbacks per joint: (1) triangulated; (2) single camera's world landmark mapped to the mat frame and anchored at the triangulated hip midpoint; (3) last good value ≤ 0.3 s; else NaN with conf 0. Sets `tracking` = `3d` / `single_cam_A` / `single_cam_B` / `lost`. Whole-body single-camera mode (only one camera sees the person, or no calibration yet): world landmarks mapped to the mat frame with feet put on y = 0 (uncalibrated: rotation is just the y/z flip). |
| `filters.py` | One Euro filter (vectorised per coordinate; min_cutoff 1.0, beta 0.007, d_cutoff 1.0), NaN-safe. |
| `pipeline.py` | Composes a pose-result source (live cameras or replay) → pairer → triangulate → cleanup → filter → `FrameResult`s, with `raw` per-camera results attached for the recorder. |
| `markers.py` | ArUco detection of IDs 0–3, corner refinement (subpix), mat-frame corner table, PnP per camera from all visible corners (`SOLVEPNP_IPPE` init on one marker → `SOLVEPNP_ITERATIVE` over all corners), averaged over a capture window. |
| `bundle.py` | Body + marker bundle adjustment (below). |
| `checks.py` | Sanity-check computations and the CLI. |
| `record.py` / `replay.py` | JSONL writer and reader (below). |

## Tools (`python -m vision.<tool>`)

1. **`cams`** — opens indices 0–5, tiles live previews labelled by index and resolution; press `a`/`b` then a digit to assign; writes the index (and name hint) to `config/cameras.yaml`.
2. **`calib_intrinsics --cam A|B`** — shows a 10×7-square checkerboard (9×6 inner corners) in its own window (can be moved full-screen to any display) plus a camera preview window. Asks for the measured width of the whole 10-square row in mm. Auto-captures a view when corners are found (`findChessboardCornersSB`) and the view differs enough from previous captures (centroid, scale, tilt); 25 views; `cv2.calibrateCamera`; prints RMS and per-view errors, drops the worst outliers once, saves `K`, `dist`, resolution.
3. **`calib_extrinsics`** — runs both cameras for ~3 s, detects markers, solves each camera's pose, prints per camera: markers seen, reprojection RMS, camera position, and placement hint vs target (`dx`, `dy`, distance, yaw; "Move 15 cm left, raise 5 cm"). Warns if a camera is below the floor or behind the mat (wrong marker orientation/IDs). Saves `R`, `t`.
4. **`calib_refine`** — records ~20 s of the person moving on the mat (walk around, squat, raise arms, turn), then runs the bundle adjustment, prints before/after metrics, and saves only if the result passes the guards. `--from recording.jsonl` refines from an existing recording.
5. **`debug_view`** — 1280×720 window: top row the two camera feeds (scaled to 640×360) with MediaPipe 2D skeleton (green), reprojected filtered 3D joints (magenta), detected markers (outline); bottom row a top-down (x–z) panel with the mat outline, marker positions, camera positions, and skeleton, and a front (x–y) panel. HUD: per-camera capture fps and pose ms, pairing skew median/p95, `tracking`, mean reprojection error. Keys: `r` record on/off (to `data/recordings/<timestamp>.jsonl`), `q` quit. `--replay file.jsonl` runs the same view without cameras (no images, panels only).
6. **`checks`** — 5 s live (or `--from recording`) capture standing still in view of both cameras and the markers. Prints PASS/FAIL: per-camera lens RMS (from config), mean body reprojection error, thigh and shin length std (L/R), standing knee angle (L/R), median triangulation angle, marker-centre distances (triangulated from both cameras) vs known, pairing skew p95, per-camera fps.
7. **`replay`** — `python -m vision.replay file.jsonl [--retriangulate] [--speed 1.0]` yields/prints `FrameResult`s at recorded speed; library function `replay(path, retriangulate=False)` is the team's dev source.

## Bundle adjustment (vision/bundle.py)

- **Parameters:** for each camera a Rodrigues vector + translation (mat → camera), 12 total; plus one 3D point per selected (frame, joint) observation pair.
- **Body observations:** paired frames with skew ≤ 33 ms; joints {nose, shoulders, elbows, wrists, hips, knees, ankles, heels, foot index} with vis ≥ 0.8 in both views; frames subsampled to ≤ 400 spread over the capture. Initial 3D points triangulated with the marker poses; frames whose median initial reprojection error > 40 px are dropped (left/right swaps, mis-detections).
- **Marker observations:** time-averaged detected corners per camera with their known mat-frame 3D positions (fixed, not parameters). Weighted (default ×5 per residual) so they pin the frame and scale.
- **Solve:** `scipy.optimize.least_squares`, `loss="soft_l1"`, `f_scale=2.0` px, sparse `jac_sparsity`, `x_scale="jac"`. Intrinsics fixed.
- **Guards (refuse to save):** final body RMS not lower than initial; either camera moved > 0.5 m or rotated > 15° from the marker solution; marker RMS after > 2× marker RMS before.
- **Report:** body RMS before/after, marker RMS per camera before/after, camera centres, baseline length, median triangulation angle, number of frames and observations used.

## Recording format (JSONL)

- Line 1: `{"type": "header", "version": 1, "created": iso, "cameras": <cameras.yaml contents>, "mat": <mat.yaml contents>}`.
- Then one line per pipeline output: `{"type": "frame", ...FrameResult fields (arrays as lists, NaN as null)..., "raw": {"A": PoseResult|null, "B": PoseResult|null}}`.
- Never images or video.
- Replay without `--retriangulate` yields the recorded `FrameResult`s; with it, feeds the `raw` results through the current pipeline (pairing skipped: recorded pairs are used as-is) using the **current** config.

## Error handling

- Camera fails to open or stops delivering: the debug view shows it as "no signal"; pipeline continues single-camera.
- No calibration in `cameras.yaml`: pipeline runs single-camera world-landmark mode (`tracking="single_cam_A"`), so the viewer works from minute one.
- No person: `tracking="lost"`, joints NaN, conf 0.
- Config writes always back up the previous file.

## Testing

Pytest (`python -m pytest vision/tests`), no hardware:

- `frames`: mat↔camera round trips; `look_at_pose` gives a camera whose centre and forward axis match; MediaPipe world → mat mapping on a known rotation.
- `markers`: corner table matches the geometry above (front markers at +z, ID0 at +x); synthetic projection of the corners through a target camera → `solve` recovers the pose within 1 mm / 0.1°.
- `triangulate`: synthetic cameras at the target poses, random points → exact recovery; noise → bounded error; low vis → skipped; outlier → rejected by reprojection.
- `cleanup`: each fallback branch and the 0.3 s expiry; `tracking` labels.
- `filters`: constant input stays constant; step response converges; NaN passes through without poisoning state.
- `pairing`: nearest match, skew limit, no reuse of the same A result.
- `bundle`: synthetic body sequence + markers, perturb camera poses (10 cm, 3°) → recovered within 1 cm / 0.3°; guards trip on a deliberately bad case.
- `record`/`replay`: write → read round trip equality (including NaN); retriangulate path produces 3D.
- `checks`: computations on synthetic skeletons (known bone lengths, known knee angle).

Manual hardware checklist (run in order, results noted in the PR):

1. `cams` → assign A and B. 2. `debug_view` uncalibrated → both feeds, skeletons, fps ≥ 20 each. 3. `calib_intrinsics` for A and B → RMS < 0.5 px. 4. Mount cameras, `calib_extrinsics` → both cameras see ≥ 3 markers; placement hints sensible. 5. `calib_refine` → guards pass, body RMS drops. 6. `debug_view` → magenta reprojections sit on the green skeletons; the top-down panel shows you on the mat; raise your left hand → the +x side. 7. `checks` → all PASS (or numbers recorded). 8. Record a squat set and replay it with `--retriangulate`.
