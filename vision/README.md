# vision

Two webcams → calibrated 3D skeleton in the mat frame. Design: `docs/superpowers/specs/2026-10-10-vision-cameras-to-3d-design.md`.

## Setup

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r vision/requirements.txt
python -m vision.models.download
python -m pytest
```

macOS asks for Camera permission for your terminal on first use.

## Mat frame

Origin at mat centre on the mat surface; +y up; +z toward Mira; +x toward the patient's left
(Mira's right), the side with markers 0 and 2. Right-handed, same as Three.js/ARKit. Joints in
`FrameResult.joints` are metres in this frame.

Markers sit in the corners as in the spec. If the sheets are all turned the same way (e.g. upside
down, printed top edge toward the back of the mat), set `marker_rotation_deg` in `config/mat.yaml`
(0/90/180/270, clockwise seen from above with Mira at the top). The rig currently uses 180.

## Workflow

No lens calibration: each camera's zoom comes from the floor markers. These webcams (~48° wide) need
to be about 4.5 m from the mat centre to see a whole person.

1. `python -m vision.cams`: assign A (front-left, near marker 0) and B (front-right, near marker 1).
2. `python -m vision.debug_view`: check both feeds, skeletons, ≥ 20 fps each (2D only until calibrated).
3. Mat empty: `python -m vision.calib_extrinsics`.
   - Top: each camera with the markers it sees (yellow) and a box for the space a standing person takes
     up above the mat: green = fits, red = cut off (the text says what to do, e.g. "feet cut off: tilt
     down or move back"). Each camera needs to see at least 2 markers.
   - Bottom left, bird's-eye map (Mira at the top): each camera's position, height and field of view;
     grey rings are suggested spots, coloured rings are your locked spots.
   - Zoom is measured from the markers when they are big enough; otherwise the zoom saved from an
     earlier session or a typical-webcam value is used (orange), and step 4 fine-tunes it. These webcams
     can't zoom, so once refined it is remembered for every later setup. You can always save.
   - Press `s` to save. Press `t` to lock the current camera spots if you want the overlay to guide you
     back to exactly these spots later at this location (one big instruction and arrow at a time, plus a
     green ghost of the mat). At a new location the old spots are ignored; press `t` again there.
4. `python -m vision.calib_refine`: move around the mat for 20 s; refines poses and zoom; saves only if
   the fit improves.
5. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
   left hand → it moves on the +x side (left in the top view, right in the front view).
6. `python -m vision.checks --height-cm <your height>`: stand still for 5 s; everything should PASS.
7. Press r in the debug view to record; `python -m vision.replay FILE` or
   `python -m vision.debug_view --replay FILE [--retriangulate]` to play back.

Every session after (or after moving): step 3, then step 4. Zoom is remembered, so this takes a minute.

**Camera moved?** The live tools re-check the markers about once a second. If a camera has been
bumped, the debug view shows "Camera A has moved (… px): re-run calib_extrinsics", `calib_refine`
refuses to start, and `checks` fails "camera A not moved". Re-run steps 3–4. "Markers hidden" only
means someone is standing over them.

If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.

### 3D view

The debug view's bottom-right panel shows a low-poly 3D person rebuilt every frame from the joints
(left side orange, right side blue, a shadow on the floor, anchored feet outlined white). Drag in it
to orbit; keys: j/l orbit, i/k tilt, +/- zoom, 0 reset, v switch to the flat front view. Works on
replays too.

### Who gets tracked

Only the person on the mat or within 1 m of it. Each camera's pose detection searches only the area
around the mat (the cyan "tracking area" box in the debug view; the full picture is still shown) and
finds up to 3 people. A person counts as the same in both cameras when their hip, shoulder and ankle
midpoints line up in 3D; of those near the mat, the one closest to the mat centre is tracked, and
tracking only switches to someone else when they are 30 cm closer to the centre for half a second.
Everyone else is drawn grey and labelled "ignored". If the two cameras disagree (e.g. a hand over one
lens), the camera whose person stands at the mat and is person-sized (0.8–2.4 m from feet to head,
judged from how far away they stand) is kept and the other is ignored for that frame ("ignoring camera A
(disagrees)"). With only one camera, tracking never switches to someone nearer the centre: it stays
with whoever is closest to where the tracked person was. The tracked person's position is remembered
through up to 1 s of frames with nobody, so a dropout doesn't hand tracking to someone else.
Recordings keep everyone found, so `--retriangulate` re-runs the selection. `calib_refine` uses each
camera's person as found whenever each camera sees exactly one person, so a calibration that is off
still gets data to fix it.

Pose cost grows with the number of people it looks for. `--poses N` (debug view live mode and
`checks`; default 3) changes it; `checks` reports "pose time A/B" (target ≤ 45 ms per frame).

### Foot anchoring

A planted foot (heel and toe within 12 cm of the floor, barely moving for 0.2 s) is held still at its
average position (it is not snapped down to the mat surface, so a floor error stays visible), so
MediaPipe's wobble doesn't make the feet jitter while standing or squatting. It releases on a real step or lift (the detected foot more
than 5 cm away, or the heel off the floor, for 2 frames) and fades back to the live position over
0.1 s. The debug view's top view draws a white square under an anchored foot.
`Pipeline(..., anchor_feet=False)` (and `replay_outputs(..., anchor_feet=False)`) turns it off. `checks`
measures unanchored joints: it reports "feet anchored while standing" (how often both feet would
anchor) and "foot height above mat" (median height of the lowest heel/toe, target -1 to 5 cm; off
means the floor or calibration is off).

### Left/right flips

Both cameras are at the same height, so a MediaPipe left/right flip in one camera still looks
geometrically consistent (reprojection error can't catch it). Every frame the pipeline also tries
flipping camera A, camera B and both, and keeps the labelling whose triangulated body best matches
each camera's own 3D estimate, with the nose in front of the shoulders. The debug view says
"undid a left/right flip (A/B/AB)" when that happens.

## For the team

```python
from vision.replay import replay
for frame in replay("data/recordings/<file>.jsonl"):
    frame.joints  # (33, 3) metres, mat frame; NaN where unknown
    frame.tracking  # "3d" | "single_cam_A" | "single_cam_B" | "lost"
```
