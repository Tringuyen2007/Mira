# Context for branch `vision/cameras-to-3d`

Read this first in a new session. It summarises what this branch is for, where it stands, the decisions
behind it and what is open. Owner: Eric (vision lead for Mira). Last updated 2026-10-11.

## What the branch is for

Mira's vision stack: turn webcam video of a person on a floor mat into a trustworthy 3D skeleton
(`FrameResult.joints`, 33 MediaPipe landmarks in metres, mat frame) for the rest of the team (reps,
form rules, weight estimate, gestures, falls), plus tools to calibrate, inspect and check it.

Source docs (outside the repo): `ERIC.md` and `Mira-MVP.pdf` in Eric's iCloud Downloads. The MVP's
movement priorities: squat/lunge depth and knees, rep counting and tempo, arm presses and raises,
balance / on-mat / falls.

## Where it stands now (one camera)

- **The rig uses ONE webcam: camera B** (Logitech, ~48° × 28° at 1280×720), front-diagonal (~45°) to the
  mat, ~4 m from the mat centre, currently 0.83 m high. Camera A was dropped (see history).
- **3D from one camera**:
  1. Rays through the planted foot meet the calibrated floor: position and scale (`vision/mono.py`,
     `select.floor_feet` trusts the foot nearer the camera, since a lifted foot's ray lands far away).
  2. MediaPipe's world landmarks give shape and depth, rotated into the mat frame. Their lean toward the
     camera is removed by `mono.upright_rotation`: learned from upright frames, at least 15 of them, capped
     at 20°.
  3. A body model with measured proportions (`vision/tracker.py`, 32 pose parameters, robust LM solver,
     ~5 ms/frame) is fitted to B's 2D points, with a world-shape prior, floor contact, joint limits and
     smoothing.
  4. **2D-motion gate** (one camera only): a pose parameter only changes as fast as its landmarks move in
     B's picture. Still in 2D means still in 3D. The root position still passes a lasting change above
     6 cm (walking toward/away from B).
- **Height** sets the scale: `config/person.yaml` (`height_cm: 170.0`), set with `--height-cm` on
  `debug_view`/`checks`. A 3% error shifts the body ~6 cm in depth.
- `Pipeline(cams, tracker="auto")`: model tracker with one calibrated camera, triangulation with two.
- **Measured (B-only replays):**
  - position vs two-camera triangulation: 5–6 cm median;
  - bones fixed (0 mm);
  - still jitter: 0.75 / 3.1 cm (median / p90) on `20261011-054226`, 0.40 / 1.5 cm on `022822`;
  - walking toward B lags about 5–6 cm;
  - knees read ~3–12° more bent than MediaPipe's own (side effect of the lean correction).
- **Biggest remaining limit:** camera B is low (0.83 m) and far. It looks down at the feet at only 12°,
  so feet-on-floor depth wobbles >6 cm in some frames. **Next physical step: raise B to ~1.6–2 m, tilt it
  down, re-run `calib_extrinsics` seeing a far marker.** Not done yet.
- 236 tests pass (`.venv/bin/python -m pytest -q`). Nothing merged or pushed; the branch is 90+ commits
  ahead of `main`.

## How to run (repo root, `source .venv/bin/activate`)

```
python -m vision.cams [--remove A]            # assign / drop cameras (one camera is fine)
python -m vision.calib_extrinsics             # markers -> zoom + camera pose; s save, t lock spots, q quit
python -m vision.debug_view [--height-cm 170] # live: camera view, top view, low-poly 3D avatar; r records
python -m vision.debug_view --replay data/recordings/<f>.jsonl [--retriangulate]
python -m vision.checks [--height-cm 170]     # one-camera check set (fit, jitter, tilt, height, fps...)
python -m vision.mono_eval <f>... --height-cm 165   # B-only replay of two-camera recordings vs triangulation
python -m vision.tracker_eval <f>...          # model tracker vs triangulation (two-camera recordings)
python -m vision.avatar_eval <f>...           # avatar body-rule metrics
```

The agent shell has no camera permission, so live tools must be run by Eric in his terminal.
Recordings (JSONL, skeletons only, no video) are in `data/recordings/` (git-ignored). The newest
one-camera sessions are `20261011-054226`, `-055039` and `-060807`.

## Setup facts that matter

- **Mat frame:** origin at the mat centre on the mat surface, +y up, +z toward Mira, +x toward the patient's
  left. Right-handed. Mat 1.73 × 0.61 m.
- **Markers:** ArUco 4x4_50, 17 cm, IDs 0–3 in the corners. The sheets on the rig are **upside down**:
  `config/mat.yaml` has `marker_rotation_deg: 180`.
- **Local, untracked configs:** `config/cameras.yaml` (camera index, K, pose, locked spots, `zoom_std`) and
  `config/person.yaml`. Calibration backups are in `config/backups/`.
- **Camera indexes shift when a camera is unplugged.** Also enumerated: the MacBook camera and iPhone
  Continuity Camera. If a feed is black, check the index with `vision.cams` and edit `index:` in
  `cameras.yaml` (saving from `cams` clears calibration).
- **Zoom:** measured from the markers (no checkerboard; removed at Eric's request). `calib_refine`
  (two cameras only) keeps an already-measured zoom fixed.

## History and decisions (why things are the way they are)

1. **Two webcams + triangulation (original plan).** Built: capture, pairing, MediaPipe (up to 3 people,
   tracking-area crop), person selection (on/within 1 m of the mat), left/right flip handling,
   triangulation, cleanup, One Euro, foot anchoring (soft), recording/replay, debug view, checks,
   camera-moved alarm.
2. **Calibration pain.** Narrow lenses needed ~4.5 m; cable and room limits; floor markers only, with each
   camera seeing mostly the near pair. Result: cameras tilted 5–7°, body leaning 4–8.5°, the two views
   disagreeing on shoulders and head (72% of shoulder frames rejected). `calib_refine` (body bundle
   adjustment) helped but could run away with zoom (now fixed).
3. **3D avatar:** low-poly person in `debug_view` (`vision/avatar.py`), rigged with fixed proportions
   (`vision/rig.py`) and anatomical rules (`vision/constraints.py`). Two-camera mode only, unless the model
   tracker is on.
4. **Model tracker** (fit a body to both cameras) was tried. It fixed popping and bone stretching but
   could not overcome the weak calibration.
5. **Decision (Eric): drop camera A, keep B.** A was cutting off the person's head in 53% of frames in one
   session.
6. **Eric explicitly does NOT want:** a checkerboard or a calibration board held above the floor, or
   camera placement that ignores cable/room limits. Work within the constraints.
7. **Considered, not chosen (yet):** a single iPhone with ARKit body tracking instead of webcams.
   Recommended earlier as lower-hassle; Eric chose to stay with webcams.

## Open issues / next steps

- Raise camera B and recalibrate (above). Then record a session and measure it: `mono_eval` style
  numbers, plus `checks`.
- One-camera "standing straight" check may still fail. The residual lean is from MediaPipe, not the
  calibration.
- Deferred minors (from reviews):
  - with two people on the mat, one-camera selection switches to whoever is nearer the centre after 0.5 s;
  - the `--height-cm` reminder is only printed to the console;
  - a leftover uncalibrated A in `cameras.yaml` is still opened by `LiveSource`;
  - `tracker="triangulate"` with one camera ignores height;
  - the `checks` "jitter while still" uses all frames;
  - one dropped frame ends the feet-hidden hold;
  - slow sideways sway lags ~1 cm more with the gate.
- Possibly gitignore `config/cameras.yaml` and `config/person.yaml` (asked, not answered).
- Branch not merged; Eric wants to keep it as is for now.

## How we've been working (keep doing this)

- Superpowers workflow: brainstorming → short spec → prototype measured on Eric's recordings → plan with
  tested code → build with subagents. **No per-task reviews; one final review, one fix wave, one scoped
  re-review** (Eric's instruction).
- TDD: every change has a test seen failing first. Measure on real recordings before claiming improvement,
  and report honestly (numbers, tradeoffs, what is not fixed).
- Specs: `docs/superpowers/specs/`. Plans: `docs/superpowers/plans/`. Each has addenda with decisions made
  after review. `vision/README.md` documents setup and tools.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't commit
  `config/cameras.yaml`, `config/person.yaml` or `data/`.
