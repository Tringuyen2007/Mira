# Testing Mira networking

The iOS app now has authenticated REST requests, `/ws/live`, `/ws/events`, and
`/ws/pose`, automatic reconnect, persisted server settings (token in the Keychain),
and a shared `LiveSessionStore`.
The current Live screen is a small integration surface; the full planned Live
experience (avatar, safety sheet, haptics, pain entry) is separate work.

Server entry, connection checks, stream status, message counts, and decoding
diagnostics are compiled into **Debug builds only** under Settings. They are
absent from Release builds. Home and Live show only a plain-language availability
message when needed, without server addresses or protocol details. A final
customer-facing pairing/provisioning flow is still needed to configure a server
on a fresh installation; hiding developer controls does not implement pairing.

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
   with the **Debug** build configuration.
2. Complete the welcome screen. Open **Settings**.
3. Enter `http://localhost:8000` and the **pairing token** (the server's
   `MIRA_API_TOKEN`), then tap **Save & connect**. The token is stored in the
   Keychain and shown only as `••••` plus its last four characters.
4. Expect **Mira server online**, all three streams online, and an increasing
   **Messages received** count.
5. Tap **Test REST connection**. Expect **REST OK · server online, token accepted**
   (it calls the public `GET /health`, then the authenticated `GET /calibration/status`).
6. Open **Home → Live session**. Expect changing angle/rep values and spoken-cue text.
7. Tap **Pause**: the server accepts it (it is echoed on `/ws/events`). The mock
   engine does not act on commands yet (see "Open items").
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
"token rejected" (no retry loop). Server logs redact `token=` values.

**Envelope.** Every message is one flat JSON object with snake_case keys.
`/ws/live` and `/ws/pose` are untagged; `/ws/events` messages have a `type`. The server
adds `t`, `seq` and `session_id` to most events. After 10 s without traffic every
channel (also live and pose) receives `{"type":"ping"}`, which the app ignores.

| Stream | Shape (see the sample) |
|---|---|
| `/ws/pose` ~25 Hz | `{t, joints[33][3], confidence[33], load_l_pct, flags}` (`ws_pose.json`) |
| `/ws/live` ~10 Hz | `{t, phase, exercise_id, rep, reps_target, set, sets_target, countdown, main_angle, load_l_pct, load_conf, hr, flags, cue_id, paused}`; most are null while idle (`ws_live.json`) |
| `/ws/events` server → app | `arrived`, `greeting{text,audio_url}`, `awaiting_gesture`, `gesture`, `countdown{n,…}`, `cue{id,text,audio_url}`, `rep{index,quality,flags,…}`, `set_done`, `paused{reason}`, `resumed`, `fatigue`, `fall{stage,…}`, `fall_status{posture,moving,…}`, `alert{alert_id,kind,step,patient_id,message,show_call_911}`, `haptic{pattern}`, `summary_ready{spoken,therapist_note,audio_url}`, `coach_adapt{action,detail}`, `proposal_created`, `proposal_decided`, `device_health`, `hr` (`ws_events_*.json`) |
| `/ws/events` app → server | only `command{name: start\|pause\|skip\|stop\|pain\|im_ok, payload}`, `hr{t,bpm}`, `wrist{t,quat,accel}`, `watch_button{name}`, `device_health{t,battery_pct,app}` (`ws_events_client_*.json`); anything else is dropped. At most 20 messages/s per socket (excess silently dropped). Accepted messages are echoed to every `/ws/events` client. `pain` takes `payload.level` (0–10); ≥ 7 opens a safety incident. |

`audio_url` values are server paths (`/audio/<file>.mp3`), resolved against the
server address. Joint angles (`main_angle`) are knee flexion in degrees (0 = straight).

**REST used by the app** (typed in `Models.swift`; `JSONValue` remains only for
free-form data: prescription targets/limits, proposal evidence/changes, Copilot
charts, `coach_adapt.detail`, command payloads, unknown future events):
`GET /health`, `GET /calibration/status`, `POST /sessions {patient_id?, exercise_id?}`,
`POST /sessions/{id}/stop`, `POST /sessions/{id}/share`, `GET /sessions/{id}`,
`GET /sessions/{id}/frames?downsample=`, `GET /patients/{id}/progress`,
`GET /prescriptions/{id}`, `GET /proposals`, `POST /proposals/{id}/approve|edit|dismiss
{therapist_id, change?, reason?}`, `GET /alerts`, `POST /alerts/{id}/resolve`,
`GET /notifications/count`, `POST /copilot/ask {question, patient_id?}`.
History routes need the database and return 503 with `DB_DISABLED=1`.

## Open items

- Session commands (`start/pause/skip/stop`) reach the server and are echoed, but no
  engine acts on them yet (the mock engine ignores them; Eric's vision engine and
  the server's engine reverse channel need to define it).
- There is no `POST /sessions/{id}/cue` on the server; the provisional `sendCue`
  call was removed.
- A customer-facing pairing flow (QR code or similar) is still needed; the token is
  entered in Debug Settings only.

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
