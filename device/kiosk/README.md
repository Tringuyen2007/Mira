# Mira LCD kiosk

The Mira LCD is a 1024×600 landscape screen on a Raspberry Pi 4 (Chromium in kiosk mode, seen from ~3 m) that
shows the live session: Mira the mascot, rep counter, set indicator, heart rate, L/R balance bar, captions with
audio, the countdown and the fall check-in. It's plain HTML + CSS + vanilla ES modules, no build step, no
network access beyond the page's own origin. It is driven by the same `/ws/live` and `/ws/events` streams as
the iOS app, so the phone, the LCD and the agents always agree; rep counts come from the vision engine (or the
mock) — the LCD never talks to vision directly.

There is **no second server**. The server at the repo root serves this directory's static files at `/device/`
(`STATIC_PAGES["device"]` in `server/main.py`); `dev/`, `tests/` and any `README.md` are 404 on that origin
(`KioskStaticFiles`), so this file and the dev preview below only exist on disk, never on the kiosk's network
path.

## URL and the device token

```
http://<server>:8000/device/#token=<MIRA_DEVICE_TOKEN>
```

The token arrives once as a URL **fragment** (`#token=…`, never sent to the server, logs or `Referer`), is
saved to `localStorage["mira.deviceToken"]`, and the fragment is stripped from the address bar
(`history.replaceState`). After that it's only sent as the WebSocket `?token=` query (browsers can't set
WebSocket headers). So: open with the `#token=` fragment on **first boot only**; every later boot can use the
bare URL (`http://<server>:8000/device/`) because the token is already in `localStorage`.

`MIRA_DEVICE_TOKEN` is a separate, least-privilege token (set it in the server's `.env`; see the repo root
`README.md` "Quick start"). It only unlocks `/ws/live` and `/ws/events` (and only an allow-listed, read-only
slice of `/ws/events` — see "Security" below); it gets 401 on every REST route and 1008 on `/ws/pose` and
`/ws/notifications`. It's optional: with it unset, the kiosk simply can't connect (no device scope exists).

## Dev preview (no server, no token)

```sh
cd device/kiosk && python3 -m http.server 8765
```

then open `http://127.0.0.1:8765/dev/preview.html?screen=<name>` in Chrome, any window size (the stage scales
uniformly to fit, letterboxed, so a laptop window looks like the 1024×600 Figma frame). `screen` is one of the
fixtures in `dev/preview.js`: `greeting`, `gesture`, `countdown`, `cue`, `rep`, `speaking`, `paused`, `setDone`,
`fall`, `calling`, `idle`, `unpaired`, `offline`. Add `&actual` to draw the stage at exactly 1024×600 CSS px for a side-by-side
check against the Figma export. This preview draws the real stage markup from `index.html` through the pure
reducer (`js/state.js`) with canned fixture input — no sockets, no device token, nothing server-side.

## Dev preview against the real server

```sh
make run   # from the repo root
```

then open `http://localhost:8000/device/#token=<MIRA_DEVICE_TOKEN>` in Chrome at any window size. With
`MIRA_SOURCE=mock` (the default) the mock session plays through every screen: `arrived` → greeting (held ~3 s)
→ `awaiting_gesture` → (~4 s later) `gesture {name: "thumbs_up"}` → countdown → reps → set done → summary. To see
the fall screen, start the server with `DEMO_TOOLS_ENABLED=1` and trigger it:

```sh
curl -X POST -H "Authorization: Bearer $MIRA_API_TOKEN" http://localhost:8000/demo/trigger_fall
```

(this uses `MIRA_API_TOKEN`, the full-scope token — the demo route isn't reachable with the device token).

## Tests

```sh
node --test device/kiosk/tests/*.test.mjs
```

(Node 24 rejects a bare directory argument to `--test`, so the glob is required.) Covers the reducer and view
model for every screen and the priority order between them, the balance-bar thresholds, the caption text and
the audio-URL filter — all pure, no server, no DOM beyond `view()`'s output.

## Pi 4 setup

```bash
chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito=false \
  --autoplay-policy=no-user-gesture-required --check-for-update-interval=31536000 \
  "http://<server>:8000/device/#token=<MIRA_DEVICE_TOKEN>"
```

Use the `#token=` fragment on first boot only; once it's in `localStorage` later boots can drop it and use
`http://<server>:8000/device/`. `--autoplay-policy=no-user-gesture-required` lets greeting/cue/summary audio
play with nobody touching the screen; `--check-for-update-interval=31536000` stops Chromium from stealing focus
to update itself mid-session.

## Screen states

| # | Screen | Shown when | Mira mood |
|---|---|---|---|
| 1 | Greeting | after `arrived` / `greeting` until `awaiting_gesture` or `countdown` | idle |
| 2 | Awaiting gesture | `awaiting_gesture` until `gesture` / `countdown`; "Raise your hand to start", hint "A thumbs up works too." (both gesture icons stay); Mira waves | idle |
| 3 | Countdown | `live.phase == "countdown"` (big `live.countdown`, ring progress n/3) | — (no mascot) |
| 4 | Cue | `phase == "active"` and a correction cue arrived < 4 s ago, or balance off > 15 % | worried |
| 5 | Rep | `phase == "active"` otherwise; cheering 1.5 s after a `rep` with `quality == "good"` | cheering → idle |
| 6 | Paused | `live.paused` / `phase == "paused"` | sleeping |
| 7 | Set done | `phase == "rest"` or `"done"`; count = reps of that set; pill "EVEN WEIGHT L / R" (balanced) or "WEIGHT L / R" | celebrating |
| — | Session complete | a `session_summary` event arrived ≤ 60 s ago and `phase` is `idle`, `rest`, or `done` (a new `countdown` wins — next session started); form score /100, "N of M good reps", the AI `spoken` line | celebrating |
| 8 | Fall alert | `live.incident` true, an `alert` opened by step `detected` (until a closing step: `resolved` / `closed` / `resolved_by_therapist`), a `fall` with `stage == "confirmed"` or `cue check_in`. Once the alert escalates, a live call status line under the gestures shows the latest `alert.message` (`#call-status`, `#stage[data-call]` = `calling` / `on_call` / `ended` / `failed`, see below); the phone icon pulses while calling and CALL 911 is emphasised when `show_call_911` is true or the call failed | — (full screen, red border) |
| — | Idle | no session / `phase == "idle"` with no greeting yet: greeting layout, "Mira" hero, no plan | idle |
| — | Offline | events socket down > 3 s: small "Reconnecting…" pill top-centre over the last screen | unchanged |
| — | Not paired | socket rejected (1008): full-screen plain message "This screen isn't paired" | — |

Priority when several apply: Not paired > Fall alert > Paused > Countdown > Session complete > Set done >
Awaiting gesture > Greeting > Cue > Rep > Idle.

The caption bar shows `live.caption.text`; the same text is what the audio plays (greeting/cue/summary_ready/
session_summary/say events carry `audio_url`, played only when it's a same-origin `/audio/*.mp3` path).

Call status phases (from `alert.step`; reset on `arrived` and on a closing step; steps for an alert that
already closed are ignored): `escalated` / `call_registered` / `call_retry` → calling; `call_ongoing` /
`call_update` / `patient_responded` → on_call; `call_ended` / `acknowledged` → ended; `call_not_connected` /
`call_error` / `call_failed` / `call_watch_timeout` → failed; `detected` / `checked_in` / closing steps → no line.
Other steps (`therapist_call`, `webhook_*`) keep the current line.

## Events the LCD uses

| Stream | Event | What the LCD does |
|---|---|---|
| `/ws/live` | snapshot (10 Hz) | phase, reps, set, countdown, balance, HR, `caption`, `incident`, `device_prefs` |
| `/ws/events` | `arrived`, `greeting`, `awaiting_gesture`, `gesture`, `stopped` | greeting / gesture screens (greeting plays its `audio_url`) |
| `/ws/events` | `cue` | correction window (Cue screen); `check_in` opens the fall screen; plays `audio_url` |
| `/ws/events` | `rep` | `quality == "good"` → Mira cheers 1.5 s |
| `/ws/events` | `fall`, `alert` | fall screen (see the screens table) |
| `/ws/events` | `summary_ready`, `session_summary` | plays `audio_url`; `session_summary` shows Session complete |
| `/ws/events` | `say` | Mira's trainer line `{id, text, audio_url}` (server `TrainerVoice`): plays `audio_url`; the text reaches the caption through `live.caption` (no screen change) |

## Voice and animation

Every screen is spoken by the server's trainer voice (`say` events: one pre-recorded ElevenLabs voice, counting
good reps aloud, narrating the emergency call). `AudioPlayer` (`js/audio.js`) reports `onSpeaking(true)` when
a clip starts playing and `onSpeaking(false)` when it ends, is paused (a newer clip replaced it), errors or is
blocked by autoplay; events from a superseded clip are ignored. `main.js` dispatches that as
`{channel: "speaking", msg: {on}}`, the reducer keeps `speaking`, and `view.js` sets `#stage[data-speaking]`.

Motion (CSS only, transform/opacity, all switched off by `prefers-reduced-motion`):

- `talk` (0.35 s bob −6 px + 1.5° tilt) on the `.mascot` wrapper while `data-speaking="true"`; the mood
  animation stays on `#mira`, so the two compose.
- `wave` (±4°, 1.2 s) on `.mascot` on the Awaiting gesture screen (talking wins while she speaks).
- `pop` (scale 1 → 1.18 → 1, 0.3 s) on `#rep-now` and `#countdown-n` each time the shown number changes:
  `view.js` toggles the `pop` class off and on with a reflow in between, only on a change. `#stage` also
  carries `data-rep` (current rep) and `data-tick` (countdown value or `""`).
- The caption bar's voice icon is three `.voice-bar` spans: static, and animated (`bars`, staggered) only while
  speaking.
- The rep sparkles (`.s-rep`) twinkle while Mira is cheering.

`live.device_prefs` (set from the phone's Settings, Plan 3 §6.5) is honoured on every `/ws/live` message: the
caption bar is hidden when `device_prefs.captions === false` (`state.js` `view().captionsOn`), and
`AudioPlayer.volume` is set from `device_prefs.volume` (0..1, default 1, clamped in `main.js`) before each clip
plays. Missing/empty `device_prefs` means captions on and full volume.

## How to add mascot frames

Mira's five moods (idle, worried, cheering, sleeping, celebrating) are listed in `assets/mira/mascot.json`, one
still PNG each. A mood can carry a frame list instead of a single `src` (`mascot.js` already supports both):

```json
"idle": {"src": "idle.png", "frames": ["idle/00.png", "idle/01.png"], "fps": 12, "loop": true}
```

See `assets/README.md` for where each still came from (Figma node, how it was composed) and what a fuller
animation would need (the 750×750 frame sequences named in the Figma component description).

## Security notes

- The device token only unlocks `/ws/live` and `/ws/events`; it is refused on every REST route (401) and on
  `/ws/pose` / `/ws/notifications` (1008). On `/ws/events` a device-scoped client may only *send*
  `device_health` (anything else is dropped) and only *receives* an allow-listed slice of events (no
  proposals, `coach_adapt`, or notifications) — see `server/devices.py` `DEVICE_EVENT_TYPES` /
  `DEVICE_HEALTH_KEYS`. `device_health` values are bounded (`server/main.py` `sanitize_device_health`):
  strings cut to 64 chars, `battery_pct` must be a number and is clamped to 0–100, objects/lists/nulls dropped.
- CSP (`index.html` `<meta>`): `default-src 'self'; connect-src 'self'; img-src 'self' data:;
  media-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'`.
  No inline script or style attributes.
- All server-supplied text (captions are AI-generated) is written with `textContent`, never `innerHTML`.
- Audio only plays for `audio_url` values matching a same-origin `/audio/*.mp3` path.

## Troubleshooting

- **"This screen isn't paired"**: no token at all (nothing in `localStorage`, no `#token=` on the URL). Check
  `MIRA_DEVICE_TOKEN` is set on the server and re-open with `#token=<MIRA_DEVICE_TOKEN>`.
- **Stuck on "Reconnecting…"**: a wrong token closes the WebSocket the same way a network failure does (browsers
  can't tell 1008 from a dropped connection), so the page can't distinguish "bad token" from "server down" —
  it just keeps retrying. Check `MIRA_DEVICE_TOKEN` matches the server's `.env`, then re-open the page with
  `#token=<MIRA_DEVICE_TOKEN>` to refresh the saved value.
