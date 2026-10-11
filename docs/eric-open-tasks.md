# Eric requirements audit — 2026-10-11

Audited against `/Users/ericlee/Downloads/ERIC.md` v1.4 and the current
`vision/cameras-to-3d` checkout. This is a branch audit, not evidence about work
on teammates' branches or physical hardware that is not represented here.

## Current agreed demo scope

- Camera B is the active one-camera rig. The original two-camera and upright
  board requirements were superseded by earlier setup decisions.
- Eric has confirmed that the latest live squat counting/cheat-rejection change
  works. Demonstrating cheating squats being rejected is the exercise-rule goal.
- Strict rulebooks for other exercises are deferred for this hackathon demo.
  Floor exercises are excluded because floor joint fitting remains unreliable.
- These changes reduce demo scope; they do not mean every item in the original
  assignment is implemented or validated.

## Overall result

The core local squat demonstration is ready according to Eric's latest live
confirmation. The full original checklist is **not complete**. The main remaining
work is integration with the team's app/device, a rehearsed fallback, any safety
sequence included in the pitch, and the final video. Hardware and publication
items need confirmation from Eric; this checkout cannot establish them.

## Original judging checklist, with current disposition

| Original requirement | Status on this branch | Evidence / remaining work |
| --- | --- | --- |
| Two-camera skeleton at ≥20 FPS, bone/mat sanity checks | Superseded in demo scope; original requirement not met | Active setup is camera B. Capture, calibration, model tracking, avatar, and checks exist. Recent recording averaged about 30 processed frames/s, but this is not an end-to-end latency measurement; standing 3D knees failed the original straight-leg sanity target. |
| Live weight split responds to a deliberate shift within ~0.5 s | Implemented, incompletely validated | COM/COP/load estimator and HUD exist. Earlier recording showed load changing but clipping at low confidence. Accuracy and response time are not measured. |
| Squat, bridge, stance counts and cheats | Squat demo confirmed; other strict rules deferred | Three confirmed clean squats accepted and nine later shallow/shift attempts rejected in the labeled replay. Eric confirms the revised live behavior works. Bridge and stance APIs exist but their live correctness has not been established. |
| Arrival → greeting → gesture → countdown → set → off-mat / crossed-arms pause | Vision flow implemented; full product flow not verified | Engine/session/presence/gesture code and synthetic integration tests exist; recorded arrival, gestures, and off-mat events were observed. Spoken greeting/cues need server/device integration; full noisy-room rehearsal remains unverified. |
| Staged fall confirmed; bridge does not trigger it | Detector implemented; live safety demonstration unverified | Synthetic fall/suppression tests exist. No confirmed staged-fall live result or app/SMS escalation demonstrated here. |
| Per-exercise detection and false-alarm table | Not complete | One labeled development recording plus Eric's live confirmation. No independent multi-person validation table; other exercise validation is outside the reduced demo scope. |
| Replay fallback tested | Partial | Recording/replay and Engine replay tests exist. The existing labeled recording performs squats before the start gesture, so it does not drive a complete guided session from the beginning. The preview runner has no replay CLI flag. No camera-to-server/device fallback rehearsal is verified. |
| Demo video uploaded | Unknown | The supplied phone clip is validation footage. No finished export/upload link is recorded here. |

## Implemented vision deliverables

- Capture with latest-frame buffers, pose detection, mat calibration, pairing,
  triangulation and single-camera fitting, smoothing, person selection and camera
  movement monitoring.
- Camera/debug preview, 3D avatar, COM/COP/estimated load display, skeleton-only
  JSONL recording and real-time replay.
- Joint measurements, physics estimator, exercise counters, timed form rules,
  patient threshold overrides and a heuristic quality score.
- Presence, raised hand, crossed arms, fall detector and session flow, assembled
  through `vision.Engine` with frame and event interfaces.
- `vision.run_engine` preview with accepted-rep banner and optional `--record`.
- Latest executed vision suite: **319 passing tests**. Tests support software
  behavior; they do not establish physical accuracy or end-to-end latency.

## Remaining contract and implementation differences

These are differences from ERIC.md, not requests to expand the demo again.

- `server/engine_bridge.py`, `server/api/calibration.py`, `server/main.py`,
  `server/hub.py`, and the checked cue/safety server modules are empty in this
  checkout. The vision-side adapter exists; the server connection is not present
  here. Coordinate with Tri's actual branch before duplicating his work.
- Frame angle keys differ from the assignment (`left_knee` versus `knee_l`, for
  example), as do phase and form-flag names. The implemented contract is in
  `docs/vision-engine-contract.md`; consumers need to use it or translate it.
- Rep events still return `null` for `peak_angle`, `asym_avg`, and `smoothness`.
  Eccentric/concentric time, stance sway path/ellipse, and fatigue events are not
  implemented. The quality formula is a heuristic and acceptance is not the
  original “quality ≥80 and no major flag” definition.
- Rejecting a squat currently prevents a rep increment. It does **not** emit a
  dedicated `weight_shift` or `too_shallow` cue/rejection event. The current rule
  file provides valgus, trunk lean and knee asymmetry cues. If the demo needs an
  explicit “Even out your weight” message, that is additional work.
- Bridge uses hip angle instead of the assigned hip-height metric. The floor
  physics path returns no load split, and the floor body fit remains unreliable.
  These are deferred with floor exercises.
- Thumbs-up hand detection is absent; raised hand is the supported start gesture.
- Automatic exposure/focus locking, the original board/lens calibration workflow,
  two-camera T-pose checks and exact physics algorithm/thresholds are not all
  implemented as specified. Current branch choices and limits are documented in
  `docs/vision-branch-context.md` and the validation report.

## What to finish for the reduced hackathon demo

1. **Connect the actual product:** verify Tri's server consumes Engine frames and
   events, and that the phone/dashboard/device display the same count and state.
   Confirm field names, calibration status, greeting and cue audio.
2. **Rehearse the whole shown sequence:** start gesture, countdown, clean squat,
   rejected cheat, finished set, and any pause/resume behavior shown in the pitch.
   The preview runner currently has no resume control after a pause; Engine has a
   resume API for the app/server. Resolve that if pausing is part of the demo.
3. **Prepare and test fallback:** save one successful guided squat session with
   `--record`, confirm it replays through the actual product, and rehearse switching
   when live cameras are unavailable.
4. **Check claims against evidence:** measure latency if quoting “under 200 ms.”
   Do not present same-recording tuning as a ≥90%/≤10% independent validation
   result. If showing fall escalation, validate that complete sequence separately.
5. **Confirm physical/media items:** Pi power/cooling, LCD, speaker reconnect and
   cue playback, kiosk autostart, hotspot, health ping, final rig rehearsal, edited
   2–3 minute video and upload link. No completion evidence is available here.

Related evidence: `docs/validation-20261011-082404.md`,
`docs/vision-engine-contract.md`, `docs/live-rig-validation.md`.
