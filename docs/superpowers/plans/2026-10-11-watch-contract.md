# Apple Watch — wire contract (shared by the server and watch/iPhone plans)

Spec: `docs/superpowers/specs/2026-10-11-apple-watch-design.md`. This file is the single source of truth for payload shapes.
Every JSON block below is also committed as a sample in `server/samples/` (file name in the heading) and copied into
`device/ios/Tests/Fixtures/` for the Swift codec tests. Server events carry the usual `seq`, `t`, `session_id` envelope
fields added by `Hub.emit`; they are shown here for completeness.

Captions: no new event. Watch captions come from the existing `cue.text` / `greeting.text` events and `live.caption`
(the phone relays them as `caption` to the watch). The spec's `caption` event is dropped in favour of these.

## Client → server (`/ws/events`, phone sends on behalf of the watch)

### `ws_events_client_watch_button_resume.json`
```json
{"type": "watch_button", "name": "resume"}
```
`name` ∈ `ready` (→ `command start`), `stop` (→ `command stop`), `resume` (→ `command start`). All carry `payload.source="watch"`.

### `ws_events_client_wrist.json` (extended — existing keys unchanged)
```json
{"type": "wrist", "t": 1791662259.706, "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}
```
`rotation_deg` (optional, degrees, signed; 0 = start position zeroed on the watch) is the twist the watch measured.
Phone sends ≤ 10 Hz and only while a rotation set or sync wave is active.

### `ws_events_client_watch_sync.json`
```json
{"type": "watch_sync", "t0": 1791662259.000, "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9, 1.3, 0.8, 0.2]}
```
One window per second (≤ 50 samples, 50 Hz), magnitudes of user acceleration in g, `t0` = watch wall clock of sample 0.

## Server → clients (`/ws/events`)

### `ws_events_haptic.json` — `pattern` ∈ `good_rep | fix_form | set_done | check_in | countdown_tick | breather`
```json
{"type": "haptic", "pattern": "countdown_tick", "seq": 1022, "t": 1791662259.706, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```

### `ws_events_breather.json`
```json
{"type": "breather", "state": "start", "bpm": 128, "limit": 120, "seq": 1100, "t": 1791662300.1, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```
`state` ∈ `start | end`. `end` carries the latest `bpm` and `resolution` ∈ `hr_recovered | escalated | stopped`.

### `ws_events_hr_recovery.json`
```json
{"type": "hr_recovery", "set_index": 2, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22, "seq": 1200, "t": 1791662360.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```

### `ws_events_rotation_rep.json`
```json
{"type": "rotation_rep", "exercise_id": "forearm_rotation", "set_index": 1, "rep": 4, "reps_target": 10, "angle_deg": 68.0, "goal_deg": 80.0, "reached": false, "seq": 1300, "t": 1791662400.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```

### `ws_events_rotation_set.json`
```json
{"type": "rotation_set", "exercise_id": "forearm_rotation", "set_index": 1, "angles": [62.0, 70.5, 78.0, 82.0, 75.0], "best_deg": 82.0, "avg_deg": 73.5, "goal_deg": 80.0, "seq": 1310, "t": 1791662460.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```

### `ws_events_sync_progress.json`
```json
{"type": "sync_progress", "progress": 0.6, "locked": false, "seq": 1400, "t": 1791662250.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}
```
When locked: `"locked": true, "progress": 1.0, "offset_ms": -42, "method": "xcorr" | "rtt"`.

### `ws_events_limits_changed.json`
```json
{"type": "limits_changed", "patient_id": "maya", "hr_limit": 120, "set_by_name": "Dr. Lee", "seq": 1500, "t": 1791662000.0}
```

### `ws_events_watch_features_changed.json`
```json
{"type": "watch_features_changed", "patient_id": "maya", "tremor_monitoring": true, "seq": 1501, "t": 1791662001.0}
```

Form-fix cues on the watch use the existing `rep` (`quality: "fix_form"`, `flags`) and `cue` (`text`) events. Elbow drift is a
`cue` with `"id": "elbow_drift", "text": "Keep your elbow at your side"` plus `haptic fix_form`.

## REST

### `rest_get_me_watch.json` — `GET /me/watch` (patient token)
```json
{
  "first_name": "Maya",
  "hr_limit": 120,
  "hr_limit_set_by_name": "Dr. Lee",
  "therapist_name": "Dr. Lee",
  "tremor_monitoring": false,
  "prefs": {"watch_taps": "strong", "watch_arm": "right", "watch_double_tap": true, "watch_captions": true},
  "plan": [
    {"exercise_id": "squat", "name": "Mini squats", "sets": 3, "reps": 12, "done_sets": 2, "watch": false},
    {"exercise_id": "forearm_rotation", "name": "Forearm turns", "sets": 3, "reps": 10, "done_sets": 0, "watch": true, "goal_deg": 80}
  ]
}
```
Pref defaults when unset: `watch_taps` `"normal"` (`normal|strong`), `watch_arm` `"right"` (`left|right`), `watch_double_tap` `true`,
`watch_captions` `true`. `done_sets` counts today's (patient-local day = UTC day for MVP) completed sets of that exercise.

### `rest_post_me_tremor.json` — `POST /me/tremor` (patient token) request → response
```json
{"days": [{"day": "2026-10-11", "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0, "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}]}
```
```json
{"stored": 1}
```
`dyskinesia` ∈ `none | few | some | many`; `source` ∈ `device | simulated`; ≤ 14 days; 403 `{"detail":"tremor monitoring off"}` when the
patient's `tremor_monitoring` is false.

### `rest_patch_patient_limits.json` — `PATCH /patients/{id}/limits` (full token; therapist-page contract)
```json
{"therapist_id": "lee", "hr_limit": 120}
```
```json
{"patient_id": "maya", "hr_limit": 120}
```
`hr_limit` 40–220 (422 otherwise); 403 if `therapist_id` doesn't own the patient; 404 unknown patient.

### `rest_patch_patient_watch_features.json` — `PATCH /patients/{id}/watch-features` (full token)
```json
{"tremor_monitoring": true}
```
```json
{"patient_id": "maya", "tremor_monitoring": true}
```

### `rest_get_patient_watch.json` — `GET /patients/{id}/watch` (full token)
```json
{
  "patient_id": "maya",
  "hr_recovery": [{"session_id": "219b…", "set_index": 2, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22, "time": "2026-10-11T09:01:00Z"}],
  "rotation_sets": [{"session_id": "219b…", "exercise_id": "forearm_rotation", "set_index": 1, "angles": [62.0, 82.0], "best_deg": 82.0, "avg_deg": 72.0, "goal_deg": 80.0, "time": "2026-10-11T09:05:00Z"}],
  "tremor": [{"day": "2026-10-11", "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0, "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}]
}
```

## Phone ⇄ watch (WatchConnectivity, `WatchWire`) — flat dictionaries with `"type"`

Phone → watch (application context unless noted "message"):
- `snapshot` (existing) + new optional keys `exerciseId`, `goodReps`, `restRemaining` (s), `countdown` (Int), `isWatchExercise` (Bool).
- `setup` `{firstName, hrLimit, hrLimitSetBy?, therapistName?, tremorMonitoring, prefs:{taps, arm, doubleTap, captions}, plan:[{id, name, sets, reps, doneSets, watch, goalDeg?}]}` — from `/me/watch`.
- message `haptic {pattern}` (existing, new patterns above).
- message `breather {state, bpm, limit}`; `recovery {setIndex, peakBpm, bpm60, drop}`; `rotationRep {exerciseId, rep, repsTarget, angle, goal, reached}`;
  `rotationSet {exerciseId, setIndex, angles, best, avg, goal}`; `sync {progress, locked}`; `caption {text}`; `formCue {text, rep?}`.

Watch → phone (message):
- `button {name: ready|stop|resume}` (existing + resume); `heartRate` (existing); `status` (existing).
- `wrist {t, quat, accel, deg?}` (phone downsamples to 10 Hz and sends `wrist`); `syncWindow {t0, dtMs, mags}` (→ `watch_sync`);
- `tremor {days:[…same keys as POST /me/tremor, camelCase: noneMin, slightMin, mildMin, strongMin, unknownMin, dyskinesia, source, day]}`;
- `prefs {taps?, arm?, doubleTap?, captions?}` (phone merges into `PUT /me` `device_prefs` as `watch_*` keys);
- `setupDone {}` (phone stores `watchSetupDone` locally only).
