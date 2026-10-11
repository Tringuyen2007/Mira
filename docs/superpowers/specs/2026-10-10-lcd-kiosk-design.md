# Mira LCD kiosk + device connection — design

Status: approved in chat 2026-10-10 (Tri). Branch `tri-lcd-connect`.
Design source: Figma `MIRA` file `UwyMjFR4Vbi0Y1Ej38qTlR`, page **LCD Hi-Fi** (node `17:196`); layout cross-checked
against page **LCD Wireframe** (`0:1`). Product brief: `VICTORIA.md` ("P0 LCD kiosk page").

## Goal

The Mira LCD (a 1024×600 landscape screen on a Raspberry Pi 4, Chromium in kiosk mode, seen from ~3 m) shows the
live session: Mira the mascot, the rep counter, set indicator, heart rate, L/R balance bar, captions with audio,
the countdown and the fall check-in. It is driven by the same server streams as the iOS app, so the phone, the LCD
and the agents always agree. Rep counts come from Eric's vision engine (in-process `vision.Engine`) or the mock
until it lands; the LCD never talks to vision directly.

There is **no second server**: the page is static files served by the existing FastAPI app at `/device/`
(`STATIC_PAGES["device"] = device/kiosk`, already in `server/main.py`).

## Non-goals

- Swift changes in the iOS app (Josie wires the LCD-online indicator from the contract below).
- Mascot frame animation beyond CSS motion on the 5 mood images (see "Mascot" for the extension point).
- QR pairing, Bonjour discovery, therapist dashboard.

## Part 1 — LCD page (`device/kiosk/`)

Plain HTML + CSS + vanilla ES modules, no build step, no runtime network access beyond the page's own origin.

```
device/kiosk/
  index.html            stage markup for all screens; <meta> CSP; loads js/main.js as a module
  css/tokens.css        Figma "MIRA Hi-Fi" variables as CSS custom properties
  css/kiosk.css         layout (absolute positions from Figma), components, CSS animations
  js/main.js            wiring: config → sockets → reducer → view, audio, health timer, stage scaling
  js/config.js          token from #token= fragment → localStorage, strip fragment; ws URL builder
  js/socket.js          one reconnecting WebSocket (backoff 1,2,4…30 s + jitter; ignores ping;
                        close 1008 → "rejected", stop retrying)
  js/state.js           pure reducer: reduce(state, {channel, msg}, now) → state; view(state, now) → view model
  js/view.js            applies a view model to the DOM (textContent only, class/attribute toggles)
  js/audio.js           plays audio_url only if non-null and a same-origin /audio/ path; one at a time
  js/mascot.js          mood → asset from assets/mira/mascot.json (image now, frames later)
  assets/mira/          idle, worried, cheering, sleeping, celebrating (+ mascot.json)
  assets/icons/         thumbs-up, raise-hand, sparkle, heart, pause, phone, voice (SVG from Figma)
  assets/fonts/         Inter (woff2, variable) + OFL.txt
  tests/state.test.mjs  node --test, no dependencies
  README.md             Pi setup, URL with token, dev preview, how to add mascot frames
```

### Stage and sizing

- One `#stage` element exactly **1024×600 CSS px**, every element positioned with the Figma coordinates
  (frames `24:232`, `35:163`, `35:232`, `36:163`, `36:227`, `36:296`, `37:242`, `37:321`).
- On the Pi (viewport 1024×600) the scale is 1. On any other viewport the stage is scaled uniformly with
  `transform: scale(min(vw/1024, vh/600))`, centred, letterboxed with the canvas colour — so a laptop preview looks
  exactly like the Figma frame.
- Aurora background (blurred fields) and halo rings as in Figma; avoid heavy `filter: blur` at runtime (Pi 4):
  bake the aurora into a CSS radial-gradient stack and the halos into one SVG.

### Screens and the state → screen mapping

| # | Screen (Figma) | Shown when | Mira mood |
|---|---|---|---|
| 1 | Greeting | after `arrived` / `greeting` until `awaiting_gesture` or `countdown` | idle |
| 2 | Awaiting gesture | `awaiting_gesture` until `gesture` / `countdown` | idle |
| 3 | Countdown | `live.phase == "countdown"` (big `live.countdown`, ring progress n/3) | — (no mascot) |
| 4 | Cue | `phase == "active"` and a correction cue arrived < 4 s ago, or balance off > 15 % | worried |
| 5 | Rep | `phase == "active"` otherwise; cheering 1.5 s after a `rep` with `quality == "good"` | cheering → idle |
| 6 | Paused | `live.paused` / `phase == "paused"` | sleeping |
| 7 | Set done | `phase == "rest"` or `"done"`; count = reps of that set; pill "Even weight L / R" | celebrating |
| 8 | Fall alert | `live.incident` true or an open `alert` / `fall` / `cue check_in` | — (full screen, red border) |
| — | Idle | no session / `phase == "idle"` with no greeting yet: greeting layout, "Mira" hero, no plan | idle |
| — | Offline | events socket down > 3 s: small "Reconnecting…" pill top-centre over the last screen | unchanged |
| — | Not paired | socket rejected (1008): full-screen plain message "This screen isn't paired" | — |

Priority when several apply: Not paired > Fall alert > Paused > Countdown > Set done > Awaiting gesture >
Greeting > Cue > Rep > Idle.

- **Rep counter** = `live.rep` / `live.reps_target` (“7 / 12”), label “REPS · {exercise_name}”.
- **Set pill** = “SET {live.set} OF {live.sets_target}”; hidden when unknown.
- **Heart-rate chip** = `live.hr` bpm; hidden when null.
- **Balance bar**: left share = `live.load_l_pct`; balanced (green, “✓ Even weight”, L/R labels) when
  |load − 50| ≤ 15, else red with “⚠ Even out your weight” (colour plus icon plus words). Hidden when null.
- **Caption bar** = `live.caption.text` (≤ 2 lines, ellipsis), voice icon. Same text as the audio played.
- **Fall alert**: “Are you okay?”, gesture icons, “If {first_name} doesn't respond, anyone nearby:”,
  CALL 911 (always shown on this screen; it is a label for bystanders, not a button), flashing border
  (`prefers-reduced-motion` → steady border).
- Countdown sequence (3·2·1) is hidden in the hi-fi; not rendered.

### Data in

- `/ws/live` (10 Hz) is the source of truth for phase, rep, set, hr, balance, caption, first name, exercise name,
  incident. On (re)connect the first live message repaints everything.
- `/ws/events` drives moments that are not state: `rep` (cheer), `cue` (worried window), `awaiting_gesture`,
  `gesture`, `greeting`/`cue`/`summary_ready` (play `audio_url`), `alert`/`fall`.
- Every 5 s the page sends `{"type":"device_health","app":"lcd","t":…,"screen":"1024x600","version":…}`.

### Mascot

`assets/mira/mascot.json`:

```json
{"version": 1,
 "moods": {"idle": {"src": "idle.png"}, "worried": {"src": "worried.png"}, "cheering": {"src": "cheering.png"},
           "sleeping": {"src": "sleeping.png"}, "celebrating": {"src": "celebrating.png"}}}
```

A mood may later be `{"frames": ["idle_00.png", …], "fps": 12, "loop": true}` or
`{"sprite": "idle.webp", "frames": 24, "fps": 12}`; `mascot.js` already supports `src` and `frames` (sprite is
documented as the next step). CSS motion per mood now: idle sway + blink-free float, cheering bounce, worried
droop, sleeping slow breathe, celebrating bounce + sparkles. The Figma moods are flat 8-bit PNGs (420×474);
ask Victoria for full-colour 2× exports and, for richer animation, layered parts or frame sequences.

### Security (page)

- Token arrives once as `/device/#token=<MIRA_DEVICE_TOKEN>` (fragment: never sent to the server, logs or
  Referer), is saved to `localStorage["mira.deviceToken"]`, and the fragment is removed with
  `history.replaceState`. It is then only sent as the WebSocket `?token=` query (browsers cannot set WS headers).
- CSP: `default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; media-src 'self';
  style-src 'self'; script-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'`.
  No inline script or style attributes that need `unsafe-inline` (dynamic values via CSS custom properties set
  from JS with `style.setProperty`, which CSP allows).
- All server text (captions are AI-generated) is written with `textContent`; no `innerHTML` anywhere.
- Audio: only `audio_url` values matching `^/audio/[A-Za-z0-9._-]+\.mp3$` are played.

## Part 2 — Server and networking

### Scoped device token

- New `MIRA_DEVICE_TOKEN` in `Config` (str, default ""), in `SECRET_FIELDS`, documented in `.env.example`.
  Optional: empty → no device scope exists. If set it must be ≥ 24 chars, not the placeholder, and differ from
  `MIRA_API_TOKEN`; otherwise the server refuses to start (same style as `token_problem()`).
- `server/security.py`: `ws_scope(ws) -> "full" | "device" | None` (constant-time compares). `require_token`
  is unchanged, so the device token gets 401 on every REST route.
- `server/main.py`:
  - `/ws/live`: full or device.
  - `/ws/events`: full or device. Device clients:
    - may send only `device_health` (anything else dropped, logged at debug); the server sets `app="lcd"`
      and `t` on what it injects, and drops keys outside an allow-list (`t, app, screen, version, battery_pct`).
    - receive only an allow-list of event types (Hub client `accept` filter): `arrived, greeting,
      awaiting_gesture, gesture, countdown, cue, rep, set_done, paused, resumed, stopped, fall, alert,
      summary_ready, hr, device_status, ping`. No proposals, coach_adapt, notifications.
  - `/ws/pose`, `/ws/notifications`: full only (device token → close 1008).

### LCD presence

- New `server/devices.py` `DeviceRegistry`: counts connected device-scope `/ws/events` sockets, keeps the last
  `device_health` per device and its time. Online = ≥ 1 device socket and last health < 15 s old
  (`DEVICE_STALE_S`). A 5 s check loop catches staleness.
- On every online/offline transition it emits on `/ws/events`:
  `{"type":"device_status","device":"lcd","online":true,"t":…,"last_health":{…}|null}`.
- `/health/data` gains a `lcd` check: `ok` online, `warning` offline while a session is running,
  `ok` offline otherwise (it is optional hardware), with `last_seen`.
- Sample `server/samples/ws_events_device_status.json`; `test_samples.py` contract list updated;
  `ws_events_client_device_health.json` gains an LCD variant. iOS fixture copies updated.
  `device/ios/NETWORKING.md` documents `device_status` and the new live fields for Josie.

### `/ws/live` additions (late join)

`LiveState` gains (all additive, nullable):

| Field | From |
|---|---|
| `caption` `{text, source, t}` | latest `greeting.text`, `cue.text`, `summary_ready.spoken` |
| `first_name` | session start (`SessionRegistry` current session's first name) |
| `exercise_name` | display name for `exercise_id` (small map; "Mini squats" for `squat`) |
| `incident` | true from `fall` / `cue check_in` / open `alert` until `incident_closed` or alert resolved |

Agent events (`hub.emit` from SessionVoice, SessionCoach, SafetyGuardian) currently bypass `LiveState`. Hub gets
an emit observer list; the bridge registers `live.on_event` so every published event updates live state once.
Existing fields and their behaviour are unchanged.

### Mock engine

After `arrived` the mock emits `awaiting_gesture {gesture:"thumbs_up"}`, waits `MOCK_GESTURE_DELAY_S`
(4 s), emits `gesture {name:"thumbs_up"}`, then the countdown — the same sequence the vision engine is expected
to produce. Tests that wait for the first countdown get the extra delay accounted for.

### Vision contract (for Eric)

README "Frame contract" gains an "LCD needs" list: `awaiting_gesture` before each session's first set,
`gesture` on thumbs-up/raised hand, `countdown {n, set, reps_target, sets_target, exercise_id}`, `rep {index,
set, quality}`, frames with `load_l_pct`. No new engine API.

### Agents

No agent logic changes. Coach and Guardian cues and Voice greetings/summaries now reach the LCD caption via
`/ws/live` too. `device_status` is on the bus for any agent that wants it later.

## Testing and verification

- pytest (existing patterns, `make_client`, `read_until`):
  - device token: accepted on `/ws/live` and `/ws/events`, refused (1008) on `/ws/pose` and
    `/ws/notifications`, 401 on every REST route; startup refuses a short/equal/placeholder device token.
  - device client: non-`device_health` messages dropped (a `command stop` from a device never reaches the
    engine); `app` forced to `lcd`; only allow-listed events delivered.
  - `device_status` online on connect + health, offline on disconnect and on staleness; `/health/data` `lcd`.
  - live: `caption`, `first_name`, `exercise_name`, `incident` set from bridge and agent events.
  - mock: `awaiting_gesture` → `gesture` → `countdown` order.
  - static: `/device/` serves `index.html`; `/device/../ios` still 404.
- `node --test device/kiosk/tests/`: reducer and view-model for every screen and the priority order, balance
  thresholds, caption, audio URL filter.
- Visual: `make run`, open `http://localhost:8000/device/#token=…` in Chrome at 1024×600 for each state
  (mock flow + `POST /demo/trigger_fall` with `DEMO_TOOLS_ENABLED=1`), compare side by side with the Figma
  screenshots.
- `make test` stays green.

## Files touched

New: `device/kiosk/**`, `server/devices.py`, `server/samples/ws_events_device_status.json`, tests, this spec,
the plan. Changed (additive): `server/config.py`, `.env.example`, `server/security.py`, `server/main.py`,
`server/hub.py`, `server/engine_bridge.py`, `server/api/health.py`, `server/tests/test_samples.py`,
`server/samples/ws_live.json`, `README.md`, `device/ios/NETWORKING.md`, iOS test fixture copies.
Not touched: agent logic, database, REST routes used by iOS, `MIRA_API_TOKEN` behaviour, `vision/`.
