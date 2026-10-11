# Live camera + 3D mesh contract

Mira has two deliberately separate, synchronized live transports:

```text
Eric's camera laptop
  ├─ H.264/Opus camera stream ── WHIP ──> MediaMTX on Vultr ── WHEP/WebRTC ──> therapist browser
  └─ mesh pose / joints / form events ── WSS /ws/vision ──────────────────────> therapist 3D renderer
```

The browser places the camera and 3D body mesh next to each other. Both messages carry the same `session_id`
and capture timestamp `t`; renderers should keep a small (100–200 ms) pose buffer and use the video clock as the
display reference. Do not send MP4/JPEG frames through `/ws/vision`.

## Eric's agent

On session start, publish a camera stream at `mira/<session_id>`. The deployed media server exposes its playback
as `https://<mira-host>/media/mira/<session_id>/whep`; configure `MIRA_WHEP_URL` as
`https://<mira-host>/media/mira/{session_id}/whep`. The agent must send its existing frames/events to:

```text
wss://<mira-host>/ws/vision?token=<MIRA_VISION_TOKEN>
```

Use the same `session_id` and epoch-second `t` on both the video capture metadata and every mesh/pose frame. The
vision WebSocket also delivers `command`, `incident_closed`, and `end_set_early` back to the agent.

**Privacy gate:** connecting is not permission to infer or publish. In `MIRA_SOURCE=remote`, the server drops every
remote frame until the patient presses **Start today's session** in the iOS app. That patient-scoped request creates
the session and sends `command {name:"start", payload:{source:"ios"}, session_id}` to the agent. The agent must keep
camera capture/inference and WHIP publishing stopped until it receives that command, and stop them on `command stop`.

## Mesh renderer

Eric's renderer should register once at dashboard startup through
`registerAvatarRenderer(factory)` from `device/dashboard/js/avatar/avatarView.js`. A factory receives every
`setFrame({joints, flags, load_l_pct, t})` update. For a skinned body mesh, load geometry/materials once and apply
the incoming bone transforms each frame; never send mesh geometry per frame. The placeholder can remain as a safe
fallback until that module is merged.

## Access and sharing

`GET /live/media` is dashboard-token protected and returns only the current live session's WHEP URL. The media
relay must independently require that same bearer token for WHEP playback; a URL alone is not authorization.
Raw video is live-only by default and must not be recorded. A patient-facing replay/share feature requires an
explicit retention period, patient consent, encrypted recording storage, and a separate patient-scoped media
grant before it is enabled.

## Falls and safety events

The server's Safety Guardian reads these messages on `/ws/vision` exactly as it reads them from the built-in
sources. Every message carries the active `session_id` and an epoch-second `t` (within an hour of server time;
otherwise the server clock is used). Messages sent before `command start`, or for another `session_id`, are dropped.

```json
{"type": "fall", "stage": "confirmed", "session_id": "<sid>", "t": 1791729381.4,
 "drop_m": 0.9, "peak_down_speed_mps": 2.1, "posture": "lying", "on_mat": true}
```

Required: `type`, `stage` (only `"confirmed"` opens an incident; send `"possible"` for your own debugging, the
server ignores it), `session_id`, `t`. Optional and spoken on the call when present: `drop_m` (metres),
`peak_down_speed_mps`, `posture` (`standing` | `sitting` | `lying` | `unknown`), `on_mat`.

```json
{"type": "fall_status", "session_id": "<sid>", "t": 1791729383.4,
 "posture": "lying", "moving": false, "still_s": 2.0, "on_mat": true}
```

Send one every ~2 s while the person is down. `moving` (bool) and `posture` drive the live updates the caller
hears ("Maya is still not moving", "she is sitting up now"); `still_s` is seconds without movement.

```json
{"type": "gesture", "name": "raised_hand", "session_id": "<sid>", "t": 1791729390.1}
```

`name` is `raised_hand` (`thumbs_up` is still accepted, but Mira no longer asks for it). This is the patient's answer to the check-in.

```json
{"type": "hr", "bpm": 111, "session_id": "<sid>", "t": 1791729383.4}
```

Optional; the Apple Watch sends heart rate too. It is quoted before/after the fall on the call.

**What happens after `fall`:** the server opens one incident for the session, plays the check-in cue on the LCD
and watch, and waits `CHECKIN_TIMEOUT_S` (15 s on the demo servers) for a `gesture` (or the phone's "I'm OK").
A response in time closes the incident with no call. No response: the server builds the situation from the
incident data and places a Retell call to the emergency contact, keeps polling the call, and pushes your
`fall_status` changes into it as live updates until the contact hangs up. One call per incident, however many
`fall` events arrive; voicemail or no answer falls back to one therapist call. When the incident closes, the agent
receives `{"type": "incident_closed"}` on this socket. Real calls require `CALLS_DISABLED=0` on the server
(`GET /health` → `"calls": "live"`); with `CALLS_DISABLED=1` the same path runs a log-only simulator.
