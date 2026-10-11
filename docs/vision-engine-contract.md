# Vision engine boundary for Tri

`from vision import Engine` exposes the vision-side session adapter. Construct it
with `config_dir` and `rules_dir`, then call `start(exercise_id, reps_target,
sets, patient)`. Iterate `frames()` on the vision worker and drain `events`
with `get_nowait()` on the server worker. `stop()` closes live capture. A server
can call `command("pause" | "resume" | "skip" | "stop", t=...)`; timestamps
should use the last received frame's Unix seconds. A new `start()` clears all
detector, physics, session, and event state. For one-camera body scale,
`patient["height_cm"]` takes precedence over `config/person.yaml`; both live
processing and optional replay retriangulation use the chosen height.

Each frame is a `FrameResult`; serialize with `frame.to_dict()`. Joint positions
are metres in the mat frame. The active one-camera rig produces
`single_cam_B`. `on_mat`, `phase`, `rep`, `flags`, angles and load estimates are
updated on this same stream. Load and quality are estimates, not clinical
measurements. Lost, measuring and held poses cannot advance reps or detectors.

Each event is a JSON-compatible dict with `type` and `t` (Unix seconds):

| Type | Additional fields |
| --- | --- |
| `arrived` | — |
| `awaiting_gesture` | `gesture: "raised_hand"` |
| `gesture` | `name: "raised_hand"` |
| `countdown` | `n: 3, 2, 1` |
| `cue` | `id` (form rule ID) |
| `rep` | `index`, `quality`, `flags`, `rom`, `peak_angle`, `asym_avg`, `asym_max`, `smoothness`, `duration_s`, `set` |
| `set_done` | `set`, `reps`, `avg_quality` |
| `paused` | `reason: "off_mat" | "stop_gesture" | "phone"` |
| `resumed` | — |
| `fall` | `stage: "suspected" | "confirmed"` |
| `camera_moved` | `camera` |

`rep.rom` is degrees for dynamic exercises and `null` for a stance hold.
`asym_avg`, `peak_angle` and `smoothness` are `null` until those metrics
are implemented and validated. Please agree on this payload before binding DB
or WebSocket schemas. Fatigue detection is also pending labeled sessions.

The frame clock drives arrival, gesture, countdown, rest, and set progression.
Arrival requires a standing person facing Mira on the mat for 1.5 s. The
raised-hand gesture starts a three-second countdown. A missed gesture prompts
once more after 10 s and returns to idle after another 10 s. A completed set
enters a 10 s rest, then counts down for the next set. Off-mat and crossed-arm
pauses preserve completed reps but discard an incomplete attempt. Phone resume
is required after pause. `skip` ends the current set with its completed reps.

For camera-free fallback, use `Engine(replay_path=path, replay_realtime=True)`.
Saved `FrameResult` recordings pass through the same detectors, counter and
session logic as live frames. Set `replay_realtime=False` for tests or demos
that do not need real-time pacing. `retriangulate=True` opts into recomputing
poses from raw detections and requires the saved/current calibration.

`placement_status()` returns the vision-side camera map with movement hints,
marker status, board-refined status and pending body check. The active rig only
lists configured camera B. The server-owned `engine_bridge.py` should own
threading, queues and broadcasting; `api/calibration.py` can expose this status.
Neither endpoint is modified here.

Camera-moved events depend on saved marker observations and visible markers
in the first two seconds of a live session. Blind or uncalibrated cameras are
reported in placement status, not as a moved alarm. The branch's current
camera B rig still needs hardware/labeled-footage validation.
