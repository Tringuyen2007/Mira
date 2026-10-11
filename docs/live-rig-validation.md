# Live rig validation — camera B

Use the current one-camera setup on `vision/cameras-to-3d`. The first pass is a 15–20 minute
engineering check with one consenting adult; repeat with 2–3 people after it works. A recording
contains skeleton data and calibration, **not video**. A phone video of the full person and mat is
the easiest way to check what actually happened, but it is optional. If you prefer not to film,
write down the movement order and approximate start/end times instead. Keep any phone video private;
you can provide timestamped labels without sharing the video.

## 1. Set and calibrate

- Use the measured height of the person in the recording (170 cm only if that person is 170 cm).
- Put all four markers at their measured mat positions and keep the mat clear. Make camera B see
  all four markers, the entire standing body, and both feet throughout a squat. If practical within
  the room/cable limits, raise B and aim it downward to improve the foot view.
- From the repository root, run `.venv/bin/python -m vision.calib_extrinsics`. Check that marker IDs
  **0, 1, 2 and 3** are detected before pressing `s` to save. Do not move the camera afterward.
- Run `.venv/bin/python -m vision.checks --height-cm 170 --seconds 10` with that person standing
  still, upright, facing Mira. Save the terminal output. Resolve scale, fit, visibility, and camera
  warnings before the movement trial.

## 2. Record one short labeled trial

Run `.venv/bin/python -m vision.debug_view --height-cm 170` and press `r` to start a JSONL recording.
If filming, start the phone video at about the same time. Make one clear clap in the phone video and
raise one hand in camera B's view to help align the two timelines. If not filming, note the recording
start time and movement order. Stand still for 5 seconds, then perform:

1. Three slow, ordinary squats, with a full upright pause between reps.
2. Three deliberately shallow squats, then three with an obvious controlled left/right knee or
   weight shift. Say which kind aloud if filming, or write it in your notes.
3. Three glute bridges and two single-leg holds (one on each leg). Allow a clear pause between
   movements and say the exercise aloud.
4. While upright and not exercising: raise a hand, cross arms, step off the mat for 3 seconds, and
   return. Leave at least 3 seconds between actions.
5. Lie down slowly for a bridge-style floor position. This is a **false-alarm check** for fall
   detection. Do not attempt an uncontrolled fall to test the system.
6. Stand still for 5 seconds and press `r` again. Note the JSONL path printed in the terminal.

Keep the whole body and feet visible. If the viewer says `measuring`, stay upright until it reaches
`ready`; movement during measurement cannot be judged. Note any visible jump, foot float, missing
angle/load readout, or camera-moved warning.

## 3. Label what happened

Make a simple table using phone-video elapsed seconds, or approximate seconds since pressing `r` if
not filming. One row per attempted rep, hold, or action:

| video start–end (s) | exercise/action | expected result | observed issue |
| --- | --- | --- | --- |
| 12.4–14.6 | squat | clean rep | none |
| 25.1–27.0 | squat | shallow rep; should flag depth | left foot occluded |
| 62.0–65.0 | off mat | pause after 2 s | none |

Use labels such as `clean`, `shallow`, `knee_collapse_left`, `weight_shift_left`, `bridge`,
`single_leg_left`, `raised_hand`, `crossed_arms`, `off_mat`, and `slow_lying_down`. The exact labels
can be refined after seeing the footage; timing and honest notes matter more than perfect wording.

## 4. Evaluate in the repo

After capture, share the JSONL filename, the `checks` output, timestamped labels (and the phone video
only if you choose), the person's measured height, and any camera/marker changes. We will replay with the saved
calibration and current pipeline, inspect motion stability and missing/held frames, then compare
Engine events and rep/form flags against the labels. We will report rep recall, extra counts, form
flag hits and false alarms, gesture/presence timing, estimated load response, and movement-to-event
latency. We will tune only from labeled evidence and keep a second person's recording for a fresh
check after tuning.

Current limits: foot loading is a camera-based estimate without force-plate ground truth; a visible
weight shift can test direction and response, not absolute percent accuracy. Saved JSONL has no
camera images, so replay cannot reproduce a camera-moved alarm; that must be observed live.
