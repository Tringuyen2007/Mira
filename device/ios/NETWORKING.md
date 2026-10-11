# Testing Mira networking

The iOS app now has authenticated REST requests, `/ws/live`, `/ws/events`, and
`/ws/pose`, automatic reconnect, persisted server settings (token in the Keychain),
and a shared `LiveSessionStore`.
The current Live screen is a small integration surface; the full planned Live
experience (avatar, safety sheet, haptics, pain entry) is separate work.

Server entry, connection checks, stream status, message counts, and decoding
diagnostics are compiled into **Debug builds only** and require the explicit
launch argument `--network-debug`. With that argument, open Settings → Developer
connection tools. Without it, the app shows the user design. Diagnostics are
absent from Release builds. Home and Live show only a plain-language availability
message when needed, without server addresses or protocol details. Pairing in
onboarding (`POST /pair` with the therapist code, README "Patient identity
(phone)") is the normal path to a working token; Developer connection tools and
the `MIRA_API_TOKEN` entry remain for diagnostics only (simulating the admin/full
scope, not a paired patient).

## Run Tri's server (preferred)

The real server with its built-in mock engine needs no API keys, no database and no
camera. From the repository root (one-time setup: README "Quick start"):

```sh
python3 -c "import secrets;print(secrets.token_urlsafe(32))"   # your pairing token
MIRA_SOURCE=mock DB_DISABLED=1 CALLS_DISABLED=1 GEMINI_MODE=fake ELEVEN_DISABLED=1 \
DEMO_TOOLS_ENABLED=1 MIRA_API_TOKEN=<token> make run
```

`MIRA_SOURCE=mock` streams a scripted squat session (arrived → greeting → countdown →
reps → set_done → summary). `DEMO_TOOLS_ENABLED=1` enables `POST /demo/trigger_fall`
for the safety path. Without a database the REST routes that read history (patients,
progress, sessions/{id}, proposals, alerts) answer 503; streams, sessions start/stop,
calibration, notifications and Copilot (fake mode) work.

## Quick manual test (Simulator)

1. Open `device/ios/Mira.xcodeproj` in Xcode, select an iPhone simulator, and Run
   with the **Debug** build configuration. In Product → Scheme → Edit Scheme →
   Run → Arguments, add `--network-debug`. Remove design-preview arguments.
2. Complete onboarding. Open **Settings → Developer connection tools**.
3. Enter `http://localhost:8000` and the **pairing token** (the server's
   `MIRA_API_TOKEN`), then tap **Save & connect**. The token is stored in the
   Keychain and shown only as `••••` plus its last four characters.
4. Expect **Mira server online**, all three streams online, and an increasing
   **Messages received** count.
5. Tap **Test REST connection**. Expect **REST OK · server online, token accepted**
   (it calls the public `GET /health`, then the authenticated `GET /calibration/status`).
6. Open **Home → Live session**. Expect changing angle/rep values and spoken-cue text.
7. Tap **Pause**: the server echoes it on `/ws/events`, the mock engine freezes the
   program (`/ws/live` reports `paused: true`, event `paused {reason: "user"}`) and the
   app shows its Paused screen. **Resume** sends `start`; the server answers `resumed`.
8. `curl -X POST -H "Authorization: Bearer <token>" http://localhost:8000/demo/trigger_fall`
   → **Mira alert** appears with the server's message; **I'm OK** closes it.
9. Stop the server (Ctrl-C). Expect **Mira is unavailable right now.**; restart it
   and the app reconnects by itself (backoff ~1 s up to ~30 s).
10. Enter a wrong token: Settings shows **Token rejected by the Mira server** and the
    sockets stop retrying until the token changes. With no token at all the app shows
    **Not paired** and opens no sockets.

## Testing on a real iPhone

Use `--host 0.0.0.0` (already the default of `make run`) and connect the Mac and
iPhone to the same trusted Wi-Fi:

```sh
scutil --get LocalHostName
```

Use `http://<LocalHostName>.local:8000` in Settings (replace the placeholder with the
command's output). `localhost` on a physical iPhone refers to the phone, not the
Mac. Allow Mira's Local Network permission and any macOS firewall prompt for
Python. Guest Wi-Fi/client isolation can prevent devices from reaching each other.
The app's Info.plist includes the local-network usage description and the local
HTTP ATS exception; HTTPS addresses automatically use secure WebSockets (`wss`).

## Development fixture (outage tests only)

`tools/mira_network_mock.py` is a dependency-free fixture whose only advantage is
`POST /test/drop` (cut every connection) for outage/recovery tests. It enforces the
same auth as the server and answers REST with `server/samples/` files:

```sh
MIRA_API_TOKEN=<token> python3 tools/mira_network_mock.py --port 8001
```

## Automated tests

```sh
# unit + contract-fixture tests (no server needed)
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test --package-path device/ios

# against Tri's real server (started as above, same token)
MIRA_SERVER_URL=http://127.0.0.1:8000 MIRA_API_TOKEN=<token> \
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test --package-path device/ios

# outage/recovery against the fixture
MIRA_MOCK_URL=http://127.0.0.1:8001 MIRA_API_TOKEN=<token> \
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test --package-path device/ios
```

- `ContractFixtureTests` decodes every `server/samples/*.json` payload with the app's
  models and checks that what the app sends equals the server's client samples.
  `Tests/Fixtures/` is a copy of `server/samples/`; a test fails when it drifts. After
  a server change: `cp server/samples/*.json device/ios/Tests/Fixtures/`.
- `AuthTests`: Bearer header, 401 → "token rejected", no token → no request
  ("not paired"), Keychain-backed token (never UserDefaults), masking, pings ignored.
- `IntegrationTests.testAgainstRealServer`: 401 without/with a wrong token, refused
  sockets stop in `.rejected`, all three streams, session start/stop, every event type
  of a mock session decoded, `pause` accepted, demo fall → typed `alert`, `im_ok`
  closes the incident, explicit disconnect stops retries.
- `IntegrationTests.testOutageAndRecoveryAgainstMock`: disconnect, retained data,
  automatic reconnection.

The `DEVELOPER_DIR` prefix is needed on machines whose active command-line tools
aren't set to Xcode; it does not change the machine's global configuration.

## Server contract (confirmed against Tri's server)

The server is the source of truth: `README.md` ("Try it", "Safety path", "Dashboard
notifications"), `server/samples/*.json` (one file per message, generated by the
running server) and `server/main.py`.

**Auth.** One shared token, the server's `MIRA_API_TOKEN`.

| Channel | How the token is sent | Wrong/missing token |
|---|---|---|
| REST (all routes except `GET /health`) | `Authorization: Bearer <token>` | `401 {"detail":"unauthorized"}` |
| WebSockets `/ws/pose`, `/ws/live`, `/ws/events` (and `/ws/notifications`) | `?token=<url-encoded token>` | closed with 1008 before accept (client sees HTTP 403) |

The app keeps the token in the Keychain, next to the server address in Debug
Settings. A missing token is "not paired" (nothing is attempted); a refused token is
"token rejected" (no retry loop). Server logs redact `token=` values. A paired
patient token calling a route or another patient's resource it doesn't own gets
`403 {"detail":"forbidden"}`, which maps to `APIError.forbidden`.

**Envelope.** Every message is one flat JSON object with snake_case keys.
`/ws/live` and `/ws/pose` are untagged; `/ws/events` messages have a `type`. The server
adds `t`, `seq` and `session_id` to most events. After 10 s without traffic every
channel (also live and pose) receives `{"type":"ping"}`, which the app ignores.

| Stream | Shape (see the sample) |
|---|---|
| `/ws/pose` ~25 Hz | `{t, joints[33][3], confidence[33], load_l_pct, flags}` (`ws_pose.json`) |
| `/ws/live` ~10 Hz | `{t, phase, exercise_id, rep, reps_target, set, sets_target, countdown, main_angle, load_l_pct, load_conf, hr, flags, cue_id, paused}`; most are null while idle (`ws_live.json`) |
| `/ws/events` server → app | `arrived`, `greeting{text,audio_url}`, `awaiting_gesture`, `gesture`, `countdown{n,…}`, `cue{id,text,audio_url}`, `rep{index,quality,flags,…}`, `set_done`, `paused{reason}`, `resumed`, `fatigue`, `fall{stage,…}`, `fall_status{posture,moving,…}`, `alert{alert_id,kind,step,patient_id,message,show_call_911}`, `haptic{pattern}`, `summary_ready{spoken,therapist_note,audio_url}`, `session_summary{…}` (see "Session end and the Summary screen" below), `coach_adapt{action,detail}`, `proposal_created`, `proposal_decided`, `device_status`, `device_health`, `hr` (`ws_events_*.json`) |
| `/ws/events` app → server | only `command{name: start\|pause\|skip\|stop\|pain\|im_ok, payload}`, `hr{t,bpm}`, `wrist{t,quat,accel}`, `watch_button{name}`, `device_health{t,battery_pct,app}` (`ws_events_client_*.json`); anything else is dropped. At most 20 messages/s per socket (excess silently dropped). Accepted messages are echoed to every `/ws/events` client. `pain` takes `payload.level` (0–10); ≥ 7 opens a safety incident. |

`audio_url` values are server paths (`/audio/<file>.mp3`), resolved against the
server address. Joint angles (`main_angle`) are knee flexion in degrees (0 = straight).

**REST used by the app** (typed in `Models.swift`; `JSONValue` remains only for
free-form data: prescription targets/limits, proposal evidence/changes, Copilot
charts, `coach_adapt.detail`, command payloads, unknown future events):
`GET /health`, `GET /calibration/status`, `POST /sessions {patient_id?, exercise_id?}`,
`POST /sessions/{id}/stop`, `POST /sessions/{id}/checkin {pain_post?, effort?, note?}`,
`POST /sessions/{id}/share`, `GET /sessions/{id}` (now also carries `exercise_name` and
`analysis`), `GET /sessions/{id}/frames?downsample=`, `GET /patients/{id}/progress`,
`GET /patients/{id}/sessions?limit=&before=`, `GET /prescriptions/{id}`, `GET /proposals`,
`POST /proposals/{id}/approve|edit|dismiss {therapist_id, change?, reason?}`, `GET /alerts`,
`POST /alerts/{id}/resolve`, `GET /notifications/count`, `POST /copilot/ask {question, patient_id?}`.
History routes need the database and return 503 with `DB_DISABLED=1`.

## Session end and the Summary screen

Every session the app runs follows one flow, start to finish:

1. **Start** — `POST /sessions {patient_id?, exercise_id?}` → `{session_id, patient_id, exercise_id,
   started_at, first_name}`. The app opens `/ws/live` and `/ws/events` (or they're already open) and drives
   the Live screen from those streams.
2. **Stop** — the patient taps "End session": `POST /sessions/{id}/stop` → `{session_id, stopped: true}`.
   (A session can also end on its own — the engine's `stopped` event or the last set — in which case the
   app sees `phase: "done"` on `/ws/live` without having called `stop` itself.)
3. **Wait for the analysis** — open `SummaryView(sessionID:)` and poll `GET /sessions/{id}` every few
   seconds until its `analysis` field is non-null (up to ~60 s: `SessionAnalyst` runs `ANALYST_DELAY_S`
   after the stop, plus however long the Gemini call takes), **and** listen for a `session_summary` event on
   `/ws/events` with a matching `session_id` — whichever arrives first fills the screen immediately instead
   of waiting out the next poll. `GET /sessions/{id}`'s `analysis` and the `session_summary` event carry the
   same shape: `form_score`, `reps_total`, `good_reps`, `sets_done`, `spoken` (voiced via `audio_url` or
   on-device TTS), `note`, `highlights`, `next_focus`, `flagged_moments`, `comparison`, `source`, `model`.
4. **Check in** — the patient rates pain/effort and adds a note: `POST /sessions/{id}/checkin {pain_post?,
   effort?, note?}` → `{session_id, pain_post, effort, note_saved}`. A `pain_post >= 7` opens a Safety
   Guardian incident on the server side (same as a live pain report), so the app should treat it the same
   way it treats any other incident `alert`.
5. **Share** — if the patient opts in, `POST /sessions/{id}/share` marks the session shared with their
   therapist.

## Mira LCD

A separate device (`device/kiosk/`, a Raspberry Pi running Chromium) connects to `/ws/live` and `/ws/events`
with its own, separately-scoped `MIRA_DEVICE_TOKEN` — **not** the app's `MIRA_API_TOKEN`, and not something
the app sends or reads. The device token only unlocks those two streams (401 on every REST route, 1008 on
`/ws/pose` and `/ws/notifications`), and on `/ws/events` it may only send `device_health` (allow-listed keys,
scalar values only: strings cut to 64 chars, `battery_pct` clamped to 0–100) and only receives an
allow-listed slice of event types (no proposals, `coach_adapt`, or notifications).

When at least one LCD is connected and reporting health, the server emits on `/ws/events`:

```json
{
  "type": "device_status",
  "device": "lcd",
  "online": true,
  "t": 1791662260.102,
  "last_health": {
    "t": 1791662260.101,
    "app": "lcd",
    "screen": "1024x600",
    "version": "1"
  }
}
```

(`online` flips to `false`, `last_health` keeps the last value, on disconnect or once health goes stale.) The
app could use this to show "Mira's screen is offline" — e.g. a small indicator on Home/Live that appears only
after the first `device_status{online:false}` (so a household with no LCD never sees it) and clears on the
next `online:true`. Sample: `server/samples/ws_events_device_status.json`.

`/ws/live` gained four additive, nullable fields (existing `LiveMessage` decoding is unaffected by new keys):

| Field | Type | What |
|---|---|---|
| `caption` | `{text: string, source: string, t: number} \| null` | the latest thing Mira said (greeting/cue/summary text); late joiners get it repainted immediately |
| `first_name` | `string \| null` | the current session's patient first name |
| `exercise_name` | `string \| null` | display name for `exercise_id` (e.g. "Mini squats" for `squat`) |
| `incident` | `boolean` | true while a fall/check-in/open alert is active, until it's resolved or closed |

## PatientStore (care, progress, messaging)

`device/ios/Mira/App/PatientStore.swift` (`@MainActor @Observable`) is the app's read model for Plan 3's
Home/Progress/Care screens: `refreshAll(api:patientID:)` fetches `/me`, `/me/care`, the patient's sessions,
milestones and messages in parallel, then per-exercise progress, each independently fault-tolerant (one
endpoint failing doesn't blank the others — see `lastError`). `AppState.refreshMe()` calls it on foreground,
right after `pair()` completes, and on every `session_summary` event, and exposes the typed results
(`me`, `care`, `sessions`, `progress`, `milestones`, `messages`) plus pure helpers (`streakDays`, `weekDots`,
`kneeTrend`, `forecast`, `balanceSeries`) used by the screens. `send(_:api:)` posts a new message
(`POST /me/messages`) and `appendIncoming(_:)` folds an incoming `message` event from `/ws/events` into the
thread — see below.

### Messaging over `/ws/events`

The patient/therapist message thread is both a REST resource and a live event. `GET /me/messages` and
`POST /me/messages` carry the full history and send the patient's own messages; a `message` event on
`/ws/events` (`{"type": "message", "patient_id", "message": {id, sender, kind, body, created_at, read_at}}`)
delivers anything sent from the dashboard side (`POST /patients/{id}/messages`) or by the patient from
another device, in real time. `LiveSessionStore.onMessageEvent` is wired to `PatientStore.appendIncoming` in
`AppState.init`, so a therapist's message shows up in Care without a manual refresh.

### ATS exception (plain-HTTP demo server)

`Info.plist`'s `NSAppTransportSecurity` sets `NSAllowsArbitraryLoads = true` (alongside the existing
`NSAllowsLocalNetworking`) because the demo server (Vultr, README "Deploy (Vultr)") is plain HTTP on a public
IP — no TLS was set up for the hackathon, and `NSAllowsLocalNetworking` alone only covers LAN addresses, not
a public one. This is a deliberate, documented exception, not an oversight: **remove it once the server gets
HTTPS**, at which point `wss://`/`https://` addresses work without any ATS exception at all.

## Apple Watch companion (WatchConnectivity relay)

The `MiraWatch` watchOS target (Plan 4) never talks to the server directly — it only talks to the iPhone
app over `WatchConnectivity`, and the iPhone relays that traffic onto the sockets described above. There is
no new server endpoint and no new auth scope for the watch; from the server's point of view a watch tap or
heart-rate sample is indistinguishable from one the phone sent itself.

- **Codec:** `Mira/Net/WatchWire.swift` is pure Foundation, built into both the `Mira` and `MiraWatch`
  targets, and covered by `Tests/WatchWireTests.swift` under plain `swift test` (the watch target itself
  isn't exercised by `swift test`, only this shared codec). It converts between `[String: Any]` dictionaries
  (what `WCSession.updateApplicationContext`/`sendMessage` take directly — no `Data`/JSON step) and two typed
  enums: `ToWatch` (`snapshot`, `haptic(pattern:)`, `summary`) and `ToPhone` (`heartRate(bpm:t:)`,
  `button(name:)`, `status(workoutActive:)`). Every case round-trips through `encode`/`decodeToWatch` or
  `decodeToPhone`; an unrecognized `"type"`, a missing required field, or a non-JSON-safe value (e.g. a
  nested dict where a `Double` is expected) decodes to `nil` on either side.
- **iPhone-side relay:** `Mira/Watch/WatchBridge.swift` (`@MainActor @Observable final class WatchBridge:
  NSObject, WCSessionDelegate`), excluded from the `MiraNetworking` SwiftPM package so it's free to import
  `WatchConnectivity` (unavailable in the plain-macOS `swift test` build, which uses a same-shaped stub in
  `AppState`). It exposes `isPaired`, `isWatchAppInstalled`, `isReachable`, `lastWatchHR` and
  `workoutActive` (the watch's `.status(workoutActive:)`, stored for a future Settings row; nothing reads it
  yet). Delegate callbacks copy the `WCSession` values they need before hopping to the main actor.
  - **Phone → watch:** `push(_ live: LiveMessage?)` sends the live snapshot via `updateApplicationContext`
    (delivered even while the watch app isn't foregrounded), skipped when the encoded snapshot hasn't changed
    and throttled by `PushThrottle` (in `WatchWire.swift`, unit-tested): the first change in a quiet 0.5 s
    window goes out at once, later ones wait for the window to close and the latest is sent then (a trailing
    push), so the final state is never dropped. `push(nil)` sends `LiveSnapshot.idle`. `AppState` pushes idle
    when `/ws/live` reports `done` or disappears — the server clears the session right after `done`, after
    which this patient's `/ws/live` goes quiet — and again when `sessionSummary` arrives (unless a new session
    is already live), so the watch always ends its workout. `forward(haptic:)` and `forward(summary:)` use
    `sendMessage` instead — best-effort, silently dropped when the watch isn't reachable or the send errors,
    since a missed haptic or summary isn't worth retrying.
  - **Watch → phone:** incoming `didReceiveMessage` is decoded with `WatchWire.decodeToPhone`. `.heartRate`
    updates `lastWatchHR` and calls `liveSession.send(.hr(HeartRateMessage(t:bpm:)))`; `.button(name)` calls
    `liveSession.send(.watchButton(WatchButtonMessage(name:)))`. Both then flow into `/ws/events` exactly like
    any other client-originated event (see `watch_button`/`hr` in the table above). The server forwards
    `watch_button` `ready`/`stop` as `command start`/`stop` with `payload.source = "watch"` and also broadcasts
    the raw `watch_button`. The watch sends heart rate only while the phone is reachable.
  - `AppState.watch: WatchBridge` is created with the app's `LiveSessionStore` and activated at launch.
    `WCSession.isSupported()` is true on every iPhone; the bridge is simply inert when no watch is paired or
    the watch app isn't installed. `AppState.watchPaired`
    (`watch.isPaired && watch.isWatchAppInstalled`) gates the Settings "Apple Watch tap" toggle.
- **The `haptic` event:** `{"type": "haptic", "pattern": "good_rep" | "fix_form" | "set_done" |
  "check_in", "session_id", "t"}` on `/ws/events` (server → app, already listed in the events table above).
  `LiveSessionStore` decodes it into `haptic: HapticMessage?` plus a `hapticSeq: Int` that increments on every
  occurrence — including a repeated pattern — so `AppState` can forward each one to the watch even when two
  consecutive patterns are identical (the watch just replays the same haptic rather than deduping). The watch
  app maps each pattern to a `WKInterfaceDevice` haptic type (`good_rep → .success`, `fix_form → .retry`
  played twice 150 ms apart, `set_done → .notification`, `check_in` and any unknown pattern →
  `.directionUp`); its own Ready/Stop buttons play `.click`/`.stop`.
- **Not covered here:** `device/ios/MiraWatch/` (the watch app's own UI — live/paused/done screens, Stop as
  tap-to-confirm or 1 s hold — and `HKWorkoutSession` heart-rate capture) — see the README "Apple Watch
  companion" section and the sources in `device/ios/MiraWatch/`.

### Server events for the watch (tri-watch)

The authoritative shapes are in `docs/superpowers/plans/2026-10-11-watch-contract.md` and the samples in
`server/samples/ws_events_*.json` / `server/samples/rest_*.json`. Decode `seq` as optional on all of these
(they are emitted with `Hub.emit`, which never adds it). This is the server contract only — the watch app's
own screens for them are W8's concern, not documented here.

| Event | When | Decode as |
|---|---|---|
| `haptic {pattern}` | `good_rep`, `fix_form`, `set_done`, `check_in`, plus `countdown_tick` (every countdown number) and `breather` (breather start/end) — `HAPTIC_PATTERNS` on the server now has all six | an unrecognized pattern decodes to `nil`/falls through to `.directionUp`, same as today |
| `breather {state: "start"\|"end", bpm, limit, session_id, resolution?}` | an HR incident opens the breather; it ends on recovery, on the incident's own escalated-close, or on the session stopping | not yet decoded by `WatchWire`; ignore until a phone/watch UI exists for it |
| `hr_recovery {set_index, peak_bpm, bpm_60s, drop_bpm, session_id}` | ~60 s after `set_done`, or at the next set's countdown if sooner | not yet decoded |
| `rotation_rep {exercise_id, set_index, rep, reps_target, angle_deg, goal_deg, reached}` | each watch turn `RotationCoach` counts | not yet decoded; the watch already feels its own turn via `wrist`, this is for the phone/dashboard |
| `rotation_set {exercise_id, set_index, angles, best_deg, avg_deg, goal_deg}` | `set_done` of a watch (rotation) exercise | not yet decoded |
| `sync_progress {progress, locked, offset_ms?, method?}` | each `watch_sync` window while a session is current | not yet decoded |
| `limits_changed {patient_id, hr_limit, set_by_name}` | the therapist changed the heart limit | re-fetch `GET /me/watch`, refresh Settings |
| `watch_features_changed {patient_id, tremor_monitoring}` | the therapist toggled tremor monitoring | re-fetch `GET /me/watch` |
| `cue {id: "elbow_drift", text}` | the watch-arm elbow drifted off the side for > 0.5 s during a rotation set | same `cue` the phone already decodes for camera exercises; the id is just new |

Client → server additions `WatchWire`/`WatchBridge` must stay compatible with: `watch_button {name:
"resume"}` (a third button name alongside `ready`/`stop`; relays to `command start`, blocked by the same
Guardian hold as any `start`); `wrist {t?, quat?, accel?, rotation_deg?}` (≤ 10 Hz, sanitised and persisted
server-side, only meaningful during a rotation set or a clock-sync window); `watch_sync {t0, dt_ms,
accel_mag[]}` (1 Hz during a sync wave; the server never echoes or broadcasts it). Watch exercises also
produce ordinary `rep` events with `"source": "watch"` and quality `good`/`partial` (never `fix_form`) —
decoders must accept these like any other `rep`. As always, an unrecognized event type or field must decode
to `nil`/be ignored rather than crash, so the app never regresses when the server adds one of the above.

## Open items

- Session commands (`start/pause/skip/stop`) are forwarded to the engine over the
  server's reverse channel (README "Engine reverse channel"); the mock engine applies
  them. Eric's vision engine must implement the same four commands.
- There is no `POST /sessions/{id}/cue` on the server; the provisional `sendCue`
  call was removed.
- Onboarding's therapist step now pairs for real via `POST /pair` (see
  ONBOARDING.md); Debug Settings' token entry is diagnostics-only, not the
  customer-facing path.

## Behavior and limits

- A successful WebSocket ping marks a stream connected; 5-second heartbeats have
  a 10-second deadline. Reconnect uses bounded exponential backoff with jitter.
- Overall online status requires all three streams. Settings shows each stream
  individually; the REST test is a separate request, not an inferred health check.
- Automatic reconnect preserves data. Changing the saved server clears old data.
- Commands are rejected while offline and never queued or replayed. A successful
  socket send does not itself prove server execution; observe the returned state
  or event. REST mutations are also never automatically retried.
- This layer does not add Watch connectivity, audio playback, haptics, emergency
  notifications, Bonjour discovery, or a full safety workflow. Manual server
  entry is supported. No messages are sent to contacts by the fixture, and with
  `CALLS_DISABLED=1` (the default) the server only logs simulated calls.
