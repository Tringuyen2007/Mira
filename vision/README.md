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

1. `python -m vision.cams`: assign A (front-left) and B (front-right).
2. `python -m vision.debug_view`: check both feeds, skeletons, ≥ 20 fps each (2D only until calibrated).
3. `python -m vision.calib_intrinsics --cam A` then `--cam B`: before mounting, point each webcam at the
   on-screen checkerboard; target RMS < 0.5 px.
4. Mount the cameras. `python -m vision.calib_extrinsics`: follow the hints until both say OK, press s.
5. `python -m vision.calib_refine`: move around the mat for 20 s; saves only if the fit improves.
6. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
   left hand → it moves on the +x side (left in the top view, right in the front view).
7. `python -m vision.checks`: stand still for 5 s; everything should PASS.
8. Press r in the debug view to record; `python -m vision.replay FILE` or
   `python -m vision.debug_view --replay FILE [--retriangulate]` to play back.

If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.

### Left/right flips

Both cameras are at the same height, so a MediaPipe left/right flip in one camera still looks
geometrically consistent (it can't be caught by reprojection error). The pipeline instead compares
the normal and flipped labellings with the previous 3D frame and keeps the closer one; the debug
view says "undid a left/right flip in camera X" when that happens.

## For the team

```python
from vision.replay import replay
for frame in replay("data/recordings/<file>.jsonl"):
    frame.joints  # (33, 3) metres, mat frame; NaN where unknown
    frame.tracking  # "3d" | "single_cam_A" | "single_cam_B" | "lost"
```
