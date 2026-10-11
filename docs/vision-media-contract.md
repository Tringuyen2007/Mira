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
