# Wire the patient iPhone app to the Mira backend — design

Date: 2026-10-11 · Branch: `wire-tri` · Source of truth for scope: `Mira-MVP.pdf` v1.4 §12 (patient app),
§10 (AI coach), §16 (interfaces).

## 1. Goal

The iOS app (`device/ios`) is a complete SwiftUI shell with an excellent networking layer (three WebSockets,
Keychain token, 15 REST methods, contract tests against `server/samples`) but only the Live screen uses it.
Every other screen shows a "not connected yet" card. The server has live streams, per-set AI summaries, a
Safety Guardian, Coach, Copilot and Insight, but **no patient identity, no session-end detection and no
end-of-session analysis**.

This work makes every patient-app screen in §12 real against the backend, adds the missing backend pieces,
and shows the end-of-session result on the LCD too. Vision and camera calibration stay Eric's; the 3D
movement view stays a placeholder until his coordinate contract lands.

Nothing that exists today is removed or changed in behaviour: the admin token, device token, LCD kiosk,
dashboard-facing routes, mock engine demo loop (now with real session ends), agents and notifications all
keep working exactly as before. New behaviour is additive.

## 2. Non-goals

- Vision engine, camera calibration, AR mat detection (Eric). `GET /calibration/status` stays a placeholder.
- 3D skeleton rendering on the phone. A placeholder card stays until the engine's coordinate contract exists.
- Therapist dashboard UI (`device/dashboard` is empty; out of scope). Messaging from the therapist side is
  API-only.
- How-to videos: no assets exist; the Care → Videos screen keeps its placeholder.
- TLS on Vultr (user chose plain HTTP on 2026-10-10).

## 3. Architecture overview

```
iPhone app ──REST (patient token)──▶ FastAPI ──▶ Tiger/Timescale
    │                                  │
    ├──/ws/live /ws/events /ws/pose────┤  (patient token; filtered to the patient's session)
    │                                  │
Apple Watch ──WatchConnectivity──▶ iPhone (WatchBridge relays hr / watch_button; receives haptic)
                                       │
LCD kiosk ──/ws/live /ws/events────────┘  (device token, + new `session_summary` event)
```

Three token scopes (`server/security.py`):

| scope | who | how | can |
|---|---|---|---|
| `full` | dashboard, ops, tests | `MIRA_API_TOKEN` (exists) | everything |
| `device` | LCD kiosk | `MIRA_DEVICE_TOKEN` (exists) | `/ws/live`, `/ws/events` (allow-list), send `device_health` |
| `patient` | iPhone | **new** per-phone token from `POST /pair` | `/me/*`, own sessions, `/ws/*` for own session, send `command`/`hr`/`wrist`/`watch_button` |

## 4. Identity and pairing (Plan 1)

### 4.1 Schema (migration `001_patient_identity.sql`)

```sql
CREATE TABLE IF NOT EXISTS pairing_codes (
  code          text PRIMARY KEY,                 -- 6 chars, A-Z0-9, no 0/O/1/I
  patient_id    text NOT NULL REFERENCES patients(id),
  therapist_id  text REFERENCES therapists(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz,                      -- NULL = never (demo code)
  used_at       timestamptz
);
CREATE TABLE IF NOT EXISTS patient_devices (
  token_hash    text PRIMARY KEY,                 -- sha256 hex of the bearer token
  patient_id    text NOT NULL REFERENCES patients(id),
  device_name   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen     timestamptz,
  revoked_at    timestamptz
);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS emergency_contact_relationship text;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS alert_therapist boolean NOT NULL DEFAULT true;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS next_visit_at timestamptz;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS share_by_default boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS device_prefs jsonb NOT NULL DEFAULT '{}';
ALTER TABLE therapists ADD COLUMN IF NOT EXISTS clinic text;
```

Seed: `K7P4M2` → Maya (never expires; re-usable while `PAIRING_DEMO_CODE_REUSABLE=1`, default on so the demo
phone can re-pair), Dr. Okafor gets `clinic = 'Lakeside Rehab'`, Maya gets `next_visit_at` = next Tuesday
10:30 local.

Migrations live in `server/db/migrations/NNN_name.sql`, are idempotent, and run (a) as step 1b of
`server/db/setup.py`, (b) via `make db-migrate DSN=…`, (c) at server start when `DB_AUTO_MIGRATE=1`
(default on; a failed migration logs and continues so a schema hiccup never blocks the live demo). The same
statements are appended to `schema.sql` for fresh databases.

### 4.2 Routes

| route | auth | body → response |
|---|---|---|
| `POST /pair` | none, 5/min/IP | `{code, device_name?, profile{name, height_cm, mass_kg, affected_side?, condition}, emergency_contact{name, phone, relationship?}, alert_therapist}` → `{patient_id, token, me}` |
| `GET /me` | patient | `MeResponse` (below) |
| `PUT /me` | patient | partial `{profile?, emergency_contact?, alert_therapist?, share_by_default?, device_prefs?}` → `MeResponse` |
| `DELETE /me` | patient | purges the patient's sessions, reps, pose_frames, form_events, heart_rate, alerts, memory_events, messages, session_analyses, patient_devices; keeps the patient row and prescriptions (the therapist's plan is theirs) with profile fields nulled. Returns `{deleted: true}`. The token is revoked. |
| `GET /me/export` | patient | JSON of everything `DELETE /me` would delete (for "Download my data") |
| `POST /patients/{id}/pairing-code` | full | mints a new code (24 h) → `{code, expires_at}` |
| `DELETE /patients/{id}/devices` | full | revoke all phones |

`MeResponse`:
```json
{"patient": {"id","name","first_name","height_cm","mass_kg","affected_side","condition","hr_limit",
             "emergency_contact": {"name","phone","relationship"}, "alert_therapist", "share_by_default",
             "device_prefs", "next_visit_at"},
 "therapist": {"id","name","clinic","phone"},
 "prescriptions": [ {exercise_id, exercise_name, sets, reps, targets, limits, version} ],
 "lcd": {"online": bool, "last_seen": float|null},
 "calibration": {"status": "unknown", "calibrated": false}}
```

`exercise_name` comes from a small `server/exercises.py` catalog (id → display name, main metric label,
unit) shared by the LCD's `exercise_name` enricher and the new routes. Existing `LiveState.exercise_name`
switches to the same catalog (same strings, so the kiosk is unaffected).

### 4.3 Security changes (`server/security.py`)

- `Principal(scope: Literal["full","device","patient"], patient_id: str | None)`.
- `resolve_token(token) -> Principal | None`: constant-time compare against the two static tokens, else
  sha256 lookup in `patient_devices` (cached in-process for 60 s, `revoked_at IS NULL`; updates `last_seen`
  at most once a minute).
- `require_token` is unchanged for existing routers (full only). New dependencies: `require_patient()`
  (patient or full) and `require_patient_match(patient_id)` for `/patients/{id}/…` and `/sessions/{id}/…`
  routes that patients may call (full always passes; patient must own the resource).
- WebSockets: `ws_scope()` returns the `Principal`. Patient sockets receive `/ws/live` and `/ws/events`
  frames only while `registry.current.patient_id == principal.patient_id` (otherwise the socket stays open and
  receives pings — the phone shows "Mira isn't running your session"). `/ws/pose` same. Patient clients may
  send `command`, `hr`, `wrist`, `watch_button` (not `device_health`). Rate limits as for full.
- No DB → `/pair` and `/me` return 503 like other DB routes; the app shows "Mira's server is offline".

### 4.4 iOS

- `AppState` gains `patientID`, `me: MeResponse?`, `pairingState`. Token still lives in `KeychainTokenStore`;
  `patientID` in UserDefaults (`patientID.v1`). Debug token entry remains for admin use.
- Onboarding Therapist step: on "Continue" calls `APIClient.pair(...)` with the draft (profile, emergency
  contact, code). Success stores token + patient id, sets `me`, advances. Failure shows the server message
  inline (bad code / expired / offline) and allows "Save code for later" (existing behaviour) so onboarding
  never blocks. Device step: shows real LCD status from `me.lcd` with a Refresh; "Set up later" remains.
  Review step shows the linked therapist and plan from `me`.
- Server address default stays `localhost:8000` for dev; the Vultr address is offered as a one-tap preset in
  the debug panel and is the default when built with `-DMIRA_DEMO`.

## 5. Session lifecycle and end-of-session analysis (Plan 2)

### 5.1 Session end, unified

`SessionRegistry.stop()` becomes the single place a session ends and gains `on_stop` hooks (mirrors
`on_start`). It is reached from:

1. `POST /sessions/{id}/stop` (phone End session, dashboard) — exists.
2. Engine `stopped` event (crossed-arms gesture, future vision engine) — **new**: `engine_bridge` calls
   `registry.stop()` after routing the event.
3. Last `set_done` where `set >= sets_target` — **new**, same place. The mock engine emits `stopped` itself
   after its third set (instead of looping) so this path is exercised daily.
4. Server shutdown — exists.

Mock mode keeps the LCD demo alive: `MOCK_AUTO_RESTART_S=30` (default) starts a fresh mock session 30 s after
the previous one ends. `MOCK_AUTO_RESTART_S=0` disables it. `MOCK_LOOP=1` restores today's never-ending
loop for anyone who relied on it.

### 5.2 `session_analyses` table (migration `002_session_analysis.sql`)

```sql
CREATE TABLE IF NOT EXISTS session_analyses (
  session_id       uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  created_at       timestamptz NOT NULL DEFAULT now(),
  form_score       smallint,            -- 0..100, computed, not from the LLM
  reps_total       int, good_reps int, sets_done int,
  spoken           text NOT NULL,       -- 2-3 sentences for the patient (voiced)
  note             text NOT NULL,       -- therapist draft, stored WITHOUT the label; label added on read
  highlights       jsonb NOT NULL,      -- [{"kind":"rom|balance|pain|streak|consistency","text":..., "delta":...}]
  next_focus       text,
  flagged_moments  jsonb NOT NULL,      -- [{"set","rep","rule_id","label","severity","t"}]
  comparison       jsonb NOT NULL,      -- {"prev_sessions":n,"rom_delta_deg":..,"asym_delta_pct":..,"good_rep_rate_delta":..}
  audio_url        text,
  source           text NOT NULL,       -- ai | template
  model            text
);
```

Per-set `sessions.ai_summary/ai_note` are **untouched** — they keep feeding the LCD after each set.

### 5.3 `SessionAnalyst` agent (`server/agents/session_analyst.py`)

On the `on_stop` hook (fire-and-forget task; never blocks `stop`):

1. Gather with existing `queries` + two new ones: session row, reps, form_events, `per_set`, `tw_load`,
   pain/effort, and `previous_sessions(patient_id, exercise_id, limit=5)` → avg ROM, avg asymmetry, good-rep
   rate. Skip entirely (log `analysis_skipped`) if `reps_total == 0` and duration < 20 s (false start).
2. Compute deterministically: `form_score = round(mean(reps.quality))` (fallback: `100 - 15·major_flags/rep`
   clamped), `good_reps = count(quality=='good')`, `flagged_moments` = form_events joined to reps (label from
   `cue_library` rule text), `comparison` deltas.
3. `gemini.ask("analyst", models.smart(), load_prompt("analysis.md"), [DATA json], json_mode=True,
   timeout_s=12)` → `AnalysisOut{spoken, note, highlights[1..3], next_focus}`; `validate_json` with one
   retry; `validate_text(kind="spoken"/"note")` and a new `LENGTH_LIMITS["highlight"]`; every number must
   appear in DATA. Fallback: `_template_analysis(data)` (fixed English built from the numbers).
   `register_fake("analyst", …)` for tests and `GEMINI_MODE=fake`.
4. `eleven.tts(spoken, cache_key)` → `audio_url` (None if disabled).
5. `writes.insert_session_analysis(...)` (upsert).
6. `hub.emit("session_summary", {...all fields..., "note": label_note(note)})` → phone and LCD.
7. `notifications.create(kind="session_summary", severity="info", therapist_id, patient_id, title, body)`.

Guardrails identical to SessionVoice: never diagnose, never change the plan, only numbers from DATA.

### 5.4 Routes

| route | auth | notes |
|---|---|---|
| `POST /sessions` | full or patient | patient scope forces `patient_id` = own; `exercise_id` must be in the patient's prescriptions (else 422). Response unchanged. |
| `POST /sessions/{id}/stop` | full or owner | unchanged response |
| `GET /sessions/{id}` | full or owner | adds `"analysis": {…} | null` and `"exercise_name"` |
| `POST /sessions/{id}/checkin` | full or owner | `{pain_post 0-10?, effort 1-10?, note?}` → updates `sessions`; if `pain_post >= 7` emits the same `command pain` path so the Guardian reacts |
| `POST /sessions/{id}/share` | full or owner | unchanged |
| `GET /patients/{id}/sessions?limit=30&before=` | full or owner | `[{id, exercise_id, exercise_name, started_at, ended_at, reps_done, pain_post, effort, shared, form_score, good_reps, reps_total, spoken_preview}]` |
`audio_url` values are static `/audio/<hash>.mp3` paths with no auth, as today for cues and per-set
summaries. `GET /sessions/{id}/frames` is unchanged except that an owner may read it.

### 5.5 Events

- New `/ws/events` type `session_summary` (payload = §5.2 fields + `session_id`, `t`). Added to
  `DEVICE_EVENT_TYPES` so the LCD gets it. Sample `server/samples/ws_events_session_summary.json`; iOS fixture
  mirrors it (ContractFixtureTests keep passing).
- Existing `summary_ready` (per set) unchanged.
- `device_status` is already emitted; the phone now decodes it (`DeviceStatusMessage`) for "Mira online".
- New `message` event (§6.4).

### 5.6 LCD kiosk

`device/kiosk/js/state.js` reducer: `session_summary` → screen `session_done` (celebrating mascot, "Session
complete", form score large, good reps / total, spoken text as caption, plays `audio_url` via `audio.js`).
Stays until the next `arrived` or 60 s, then `idle`. Reuses the existing Set-done layout tokens; one new
screen, no new assets. Node tests extended.

### 5.7 iOS

- **Home → Start**: `POST /sessions {exercise_id: first prescription}` then `fullScreenCover(.live(sessionID))`.
  The "or step on the mat and give Mira a thumbs up" hint stays (engine-started sessions appear via
  `arrived`/`/ws/live`, and the app picks up `session_id` from events).
- **LiveView**: pain slider sends `CommandMessage.pain(level:)` (bug fix). End session → `stopSession(id:)`
  → `fullScreenCover(.summary(sessionID))`. Header shows `exercise_name`, `Set N of M` from `/ws/live`.
  Movement view: a proper placeholder card ("3D movement view — coming soon. Mira will show your skeleton
  here once the camera engine is connected.") with a subtle pulse; still flips to the red border on flags.
- **SummaryView(sessionID)**: loads `GET /sessions/{id}`; subscribes to `session_summary`; while `analysis ==
  nil` shows "Mira is reviewing your session…" with a progress ring and re-fetches every 3 s (max 60 s, then
  shows the per-set fallback from `summary.ai_summary`). Shows form score, good reps, highlights, Mira's
  spoken text; **Hear Mira's summary** plays `audio_url` with AVPlayer (fallback: on-device TTS of `spoken`).
  Moments to review: list from `flagged_moments`; Replay/Slow stay disabled with a "3D replay coming soon"
  caption. Check-in → `POST /sessions/{id}/checkin`; Send to therapist → `share`. Default of the share
  toggle = `me.patient.share_by_default`.
- `SummaryView` is also reachable from Progress → session row.

## 6. Read-only screens: Home, Progress, Care, Settings (Plan 3)

### 6.1 `PatientStore` (iOS)

`@Observable final class PatientStore` owned by `AppState`: `me`, `sessions`, `progress[exerciseID]`,
`messages`, `care`, each with `lastLoaded` and `refresh()` methods; `refreshAll()` on foreground, pairing and
after a session ends. Errors surface as a non-blocking banner, cached data stays visible.

### 6.2 Home

- Greeting: `Good morning/afternoon/evening, {first_name}`; streak = consecutive calendar days with ≥1 ended
  session (computed client-side from `sessions`). Mascot bloom level = min(streak, 5) (existing art).
- This week strip: 7 dots from `sessions`. WeekView: knee-bend delta and balance from `progress` (first vs
  last day in the window); "Cameras" = calibration status; "Mira" = `me.lcd.online` or live `device_status`.
- Today's plan: `prescriptions` → `{exercise_name} {sets}×{reps}`, "About N min" = `sets·reps·4 s + rests`.
- Start button as §5.7. Session summary button opens the latest ended session.

### 6.3 Progress

- Exercise tabs = patient's prescriptions. Knee-bend chart = `progress.series[].max_knee` per day; forecast
  line = least-squares over the last 14 days clipped to the prescription target (client-side, dotted, with
  the band = ±1 RMSE). "On track for {target}° by {date}" only if the slope is positive.
- Balance chart = `50 - avg_asymmetry` → "% on injured leg" using `affected_side`.
- Goals: `GET /patients/{id}/milestones` → `memory_events WHERE kind='milestone'` (patient-facing text
  already exists in Insight); next milestone = prescription target not yet met.
- Calendar: done days from `sessions`; "N of M" = sessions this month vs. days elapsed.

### 6.4 Care and messaging

Migration `003_messages.sql`:
```sql
CREATE TABLE IF NOT EXISTS messages (
  id          bigserial PRIMARY KEY,
  patient_id  text NOT NULL REFERENCES patients(id),
  sender      text NOT NULL CHECK (sender IN ('patient','therapist','mira')),
  kind        text NOT NULL DEFAULT 'message' CHECK (kind IN ('message','note','visit_highlight')),
  body        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);
```

| route | auth | |
|---|---|---|
| `GET /me/care` | patient | `{therapist, next_visit_at, notes: [messages kind=note], highlights: [latest analysis highlights + memory_events kind in (insight, milestone) for the patient, labeled "AI draft"], emergency_contact, alert_therapist, hr_limit}` |
| `GET /me/messages?since_id=` | patient | messages kind=message, both directions |
| `POST /me/messages {body}` | patient | sender=patient; emits `message` event; creates a therapist notification |
| `POST /patients/{id}/messages {body, kind}` | full | therapist/dashboard side; emits `message` event to the patient's sockets |

iOS Care: therapist card from `care.therapist` (Call = `tel:` link, enabled when phone present), next visit,
notes list (Mira reads = on-device TTS, unchanged), "For your next visit" = `care.highlights`, Messages =
real thread with working composer, Videos keep the placeholder. Emergency contact edits → `PUT /me`.
"Send a test alert" → `POST /me/test-alert` (full-and-patient; creates a `test` notification and, if
`CALLS_DISABLED` is off and `ALLOW_TEST_CALLS=1`, a Retell call to the contact — default off; otherwise the
button shows "Test alert logged. Calls are disabled on this server.").

### 6.5 Settings

- Reminders: `UNUserNotificationCenter` daily repeating notification at the saved time/days; toggles enabled.
  "Apple Watch tap" enabled when the watch is paired (Plan 4), else disabled with the existing copy.
- Voice/captions device sync: `PUT /me {device_prefs: {voice, volume, slower_speech, captions}}`; server
  stores it, includes `device_prefs` in `/ws/live` payload; kiosk applies captions on/off and volume. Voice
  identity on the device stays single (ElevenLabs voice is server-wide) — the Voice picker says "Applies to
  your phone. Mira's device uses the clinic voice."
- Privacy: share toggle → `PUT /me {share_by_default}`. Download my data → `GET /me/export` → share sheet.
- Delete everything → confirmation → `DELETE /me` → wipe Keychain + UserDefaults → back to onboarding.

## 7. Apple Watch (Plan 4)

- New watchOS target `MiraWatch` (SwiftUI, watchOS 10): one screen — big **Ready** (tap) / **Stop** (hold
  1 s) buttons, live rep count, HR. `HKWorkoutSession` + `HKLiveWorkoutBuilder` for heart rate (needs a real
  watch; simulator shows "—"). `WCSession` sends `{t, bpm}` every ~3 s and `watch_button {name}`.
- iPhone `WatchBridge.swift`: `WCSessionDelegate`; relays to `LiveSessionStore.send(.hr / .watchButton)`;
  forwards incoming `haptic` events to the watch (`WKInterfaceDevice.play` patterns: good_rep=.success,
  fix_form=.retry×2, set_done=.notification). Also pushes `rep`/`reps_target` so the watch shows the count.
- Server: `hr` events are now persisted to `heart_rate` (today they are not) via `persist.on_event`; `/ws/live`
  already carries `hr`. `watch_button ready` maps to `command start`; `stop` to `command stop` (exists per
  README).
- HealthKit entitlement + `NSHealthShareUsageDescription`. Xcode project updated via a scripted pbxproj edit
  (verified by `xcodebuild -list`).

## 8. Error handling

- Every REST call in the app goes through `APIClient.request` (10 s timeout). 401 → `notPaired`: AppState
  clears `patientID` only if the server says `token_revoked` (so a transient 401 doesn't unpair). 503 →
  "Mira's server is offline right now"; cached data stays visible.
- Patient sockets for another patient's session: open, pings only; UI shows "Mira is running a different
  session" (`LiveSessionStore.connectionState = .otherSession`).
- Analyst failures never surface as errors to the patient: template fallback always produces an analysis;
  if even the DB write fails, the `session_summary` event still goes out (in-memory) and `GET /sessions/{id}`
  returns `analysis: null` so the app falls back to per-set text.
- All new writes stay in `db/writes.py`; all reads in `db/queries.py` (repo convention).

## 9. Testing

Server (`make test`, local Docker Timescale is up on :55432):
- `test_security.py`: Principal resolution, patient token hashing, revoked, cache.
- `test_pairing.py` (DB): pair with demo code, bad code, expired, `/me` roundtrip, `PUT /me`, `DELETE /me`
  purge, `/me/export`.
- `test_session_end.py`: stop via REST / engine `stopped` / last set → exactly one `on_stop`; mock auto-restart.
- `test_session_analyst.py`: fake Gemini → stored analysis, event shape, template fallback, number guard,
  skip on empty session.
- `test_api_patient_scope.py`: owner vs. other patient 403, full passes; sessions list; checkin; messages.
- `test_ws_patient.py`: patient socket filtered to own session; allowed/forbidden client messages.
- Samples: new `ws_events_session_summary.json`, `rest_get_me.json`, `rest_post_pair.json`,
  `rest_get_patient_sessions.json`, `rest_get_me_care.json` → iOS `Fixtures/` copies (contract test).

iOS: `swift test` (MiraNetworking: new models, pairing, PatientStore decode), `xcodebuild build` for iOS +
watchOS schemes, `xcodebuild test` for the app target where feasible. Manual: simulator against the local
server with `MIRA_SOURCE=mock` → pair with `K7P4M2`, Start, End, see analysis; and against Vultr.

Kiosk: `node --test device/kiosk/tests/*.test.mjs` (+ `session_summary` reducer tests).

Per the project's convention, the full suites run once at the end of each plan, not between subagent tasks.

## 10. Deployment

- `tools/deploy_vultr.sh` additionally runs `python -m server.db.migrate` (idempotent) before restart.
- On the box: set `GEMINI_MODE=live` (keys already present) and `PAIRING_DEMO_CODE_REUSABLE=1`. Everything
  else unchanged. Verify `/health`, `/stats.ai.mode == "live"`, pair a phone with `K7P4M2`.

## 11. Build order

1. **Plan 1 — Identity**: migrations + runner, `exercises.py`, security Principal, `/pair`, `/me*`, patient
   WS filtering, iOS onboarding pairing, `MeResponse` models, debug preset. *Everything else depends on this.*
2. **Plan 2 — Session end + analysis**: registry `on_stop`, engine `stopped` → stop, mock finishes + auto-
   restart, `session_analyses`, `SessionAnalyst`, routes (checkin, sessions list, analysis in GET), events,
   LCD screen, iOS Live fixes + Summary.
3. **Plan 3 — Screens**: `PatientStore`, Home, Progress (+milestones route), Care (+messages), Settings
   (reminders, device_prefs, export, delete).
4. **Plan 4 — Watch**: watchOS target, WatchBridge, HR persistence.
5. Deploy, flip Gemini live, end-to-end check on the demo phone.

Each plan ends with the suites green and a PR into `main`.
