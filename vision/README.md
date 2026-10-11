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

## Workflow

No lens calibration: each camera's zoom comes from the floor markers. These webcams (~48° wide) need
to be about 4.5 m from the mat centre to see a whole person.

1. `python -m vision.cams`: assign A (front-left, near marker 0) and B (front-right, near marker 1).
2. `python -m vision.debug_view`: check both feeds, skeletons, ≥ 20 fps each (2D only until calibrated).
3. Mat empty: `python -m vision.calib_extrinsics`. Each camera needs to see markers at both ends of the
   mat so its zoom can be measured.
   - First time (or a new room): put the cameras roughly in place and press `t` to lock the current
     spots as a mirror-symmetric target (saved in `config/cameras.yaml`).
   - Follow the instructions: position first ("Move 20 cm back"), then aim ("Turn right 2°, tilt down
     1°, rotate clockwise 1°"), until both say Position OK and Aim OK. The green dashed ghost shows
     where the mat should appear; the red aim dot should sit on the crosshair. The strip at the bottom
     compares A and B (distance, height, angle). Press `s` to save.
4. `python -m vision.calib_refine`: move around the mat for 20 s; refines poses and zoom; saves only if
   the fit improves.
5. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
   left hand → it moves on the +x side (left in the top view, right in the front view).
6. `python -m vision.checks --height-cm <your height>`: stand still for 5 s; everything should PASS.
7. Press r in the debug view to record; `python -m vision.replay FILE` or
   `python -m vision.debug_view --replay FILE [--retriangulate]` to play back.

Every session after: step 3 without `t`, then step 4.

**Camera moved?** The live tools re-check the markers about once a second. If a camera has been
bumped, the debug view shows "Camera A has moved (… px): re-run calib_extrinsics", `calib_refine`
refuses to start, and `checks` fails "camera A not moved". Re-run steps 3–4. "Markers hidden" only
means someone is standing over them.

If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.

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
