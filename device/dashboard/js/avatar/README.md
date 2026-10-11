# Plugging in the real 3D renderer

Not served (the dashboard's static filter hides `tests/`, `dev/` and READMEs) — this is build-time
documentation for whoever writes the real renderer.

## Interface

`avatarView.js` exports two functions. Everything else in `js/screens/patient/` only ever calls these — it
never knows whether it's talking to the placeholder or the real renderer.

```js
import { createAvatarView, registerAvatarRenderer } from "../avatar/avatarView.js";

createAvatarView(container, { mode: "replay" | "live", affectedSide: "left" | "right", ghost: false })
  // -> { setFrame(frame, ghostFrame?), setView(view), setLabelsVisible(bool), dispose() }

registerAvatarRenderer(factory);
  // factory(container, opts) -> same shape as createAvatarView's return value.
  // Call this once, at module load, before any screen calls createAvatarView. Once registered,
  // createAvatarView delegates every call to your factory instead of building the placeholder.
```

### `setFrame(frame)`

```ts
frame = {
  joints: [[x, y, z], ...] | null,  // 33 MediaPipe landmarks, or null when the pose is missing/low-confidence
  load_l_pct: number,               // percent of load-bearing weight on the LEFT leg, 0-100
  flags: string[],                  // this rep's flags, e.g. "knee_cave_r", "weight_shift", "trunk_lean"
  t: number,                        // frame time (session-relative seconds, or epoch seconds for live frames)
}
```

Called up to ~30 Hz during replay/live playback. The placeholder throttles its own DOM writes to ~10 Hz
(`THROTTLE_MS` in `avatarView.js`); a canvas/WebGL renderer has no such limit and can render every frame.

### `setFrame(frame, ghostFrame)` — Overlay mode only

Only meaningful when `{ ghost: true }` was passed to `createAvatarView`. `ghostFrame` is the *earlier* date's
frame in the Recovery time-lapse screen's Overlay toggle (same shape as `frame`); the placeholder renders it as
a second, muted pill row underneath the live one. Ignored (and safe to omit) when `ghost` wasn't set — the
screen can always pass a second argument without checking which mode it's in.

### `setView(view)`

`"front" | "side" | "top"`. The screen's own Front/Side/Top segmented control calls this; your renderer should
switch camera presets but keep orbiting controls available afterwards (Original Task 12's presets: front
`(0, 1.0, 3.2)`, side `(3.2, 1.0, 0)`, top `(0, 4.0, 0.01)`, looking at roughly `(0, 1.0, 0)`).

### `setLabelsVisible(bool)`

Toggles the angle-label overlay (the placeholder's pills; your renderer's HTML-overlay labels). The screen
turns labels off in dense layouts (e.g. a small thumbnail); default is visible.

### `dispose()`

Frees everything the view created: DOM nodes, WebGL context/geometries/materials, `requestAnimationFrame`
loops, resize/orbit listeners. Called on every route change and on sign-out, so a leak here compounds across
the therapist's whole session (the shared clinic workstation runs for hours without a reload).

## Coordinate contract (mat frame)

Joints arrive already in the **mat frame**: x across the mat, y up, z toward Mira (the sensor), metres. This
is already Three.js's y-up convention, so a real renderer can map it directly — no axis flips, no scale
factor. `server/engine_bridge.py` and the vision engine both emit this frame; see README "Frame contract:
joint angles" at the repo root for the flexion-angle convention (`js/three/joints.js` matches it exactly, unit
tests included).

## MediaPipe 33-landmark indices

Exported as named constants from `js/three/joints.js` (`NOSE`, `L_SHOULDER`, `R_SHOULDER`, `L_ELBOW`,
`R_ELBOW`, `L_WRIST`, `R_WRIST`, `L_HIP`, `R_HIP`, `L_KNEE`, `R_KNEE`, `L_ANKLE`, `R_ANKLE`, `L_HEEL`,
`R_HEEL`, `L_FOOT`, `R_FOOT`), plus `BONES` (a `[a, b, segment]` list for drawing cylinders between joints,
and for `flagSegments` to know which segment a flag paints red) and the angle/validation helpers
(`kneeFlexion`, `hipFlexion`, `trunkLean`, `flagSegments`, `lerpJoints`, `isValidJoints`). Reuse these rather
than recomputing angles — they're the ones every other screen (and the therapist-facing pill text) relies on.

## What the placeholder does today

`avatarView.js`'s built-in implementation (used whenever nothing has called `registerAvatarRenderer`) is a
calm card: centred "3D view coming soon" / "Eric's 3D renderer plugs in here", four angle pills (R knee, L
knee, Hips, Trunk lean) computed live from `joints.js`, a red pill when the matching `BONES` segment is
flagged, and the shared `ui/balanceBar.js` underneath. No canvas, no WebGL, no Three.js import — it only
touches existing DOM nodes' `textContent`/classes per frame, never rebuilds. It's a reasonable reference for
what the real renderer's HTML overlay (angle pills, flag callout) should look like even after the 3D scene
replaces the "coming soon" card.
