# Mira therapist Web Dashboard (hi-fi) — design

Status: draft for review, 2026-10-11 (Tri). Branch `therapist-page` (worktree `wt/therapist-page`, from `main` `fd58753`).
Design source: Figma `MIRA` file `UwyMjFR4Vbi0Y1Ej38qTlR`, page **Web Dashboard Wireframe** (`198:5`) for layout and
content; visual language from **iPhone Hi-Fi** (`1:3`) and **LCD Hi-Fi** (`17:196`), already captured as tokens in
`device/kiosk/css/tokens.css`. Product brief: `VICTORIA.md` (H4–H21 dashboard items); API contract: `README.md`
"For the therapist dashboard" and `server/samples/`.

## Goal

A therapist opens `http://<server>:8000/dashboard/`, signs in with the API token, and sees their caseload sorted by
who needs them, then reviews a patient's session in a 3D skeleton room, adjusts the prescription, works through
Care Copilot proposals, and watches a live session. Data comes only from the existing FastAPI server (REST + WS),
so the dashboard, the iPhone app, the LCD and the agents always agree.

The wireframe is the layout source of truth. "Hi-fi" means applying the Mira design system to it, not inventing
new layouts: Inter, the aurora canvas, white cards, accent green `#0f6e56` for primary actions and good reps, berry
`#9f3462` for AI-draft chips, alert red `#d92d20` for flags. The wireframe itself notes "Good rep (green in hi-fi)".

## Screens (Figma frame → route)

| # | Figma frame | Route | Milestone |
|---|---|---|---|
| 1, 1a–1c | Caseload (`205:205`, `227:794`, `227:1023`, `227:1200`) | `#/caseload` (`?filter=needs|watch|on_track`) | M2 |
| 2 | Patient review (`206:202`) | `#/patients/:pid` and `#/replay/:sid` | M3 |
| 3 | Prescribe (`207:282`) | `#/patients/:pid/prescribe` | M4 |
| 4 | Recovery time-lapse (`208:339`) | `#/patients/:pid/timelapse` | M6 |
| 5 | Care Copilot (`209:490`) | `#/copilot` and `#/proposals/:id` | M5 |
| 7 | Remote live session (`210:820`, stretch in Figma) | `#/live/:pid` | M6 |
| 6 | Ghost rep (`210:570`, stretch) | not built: no reference-rep backend | — |

`#/replay/<sid>` and `#/proposals/<id>` are kept exactly because server notifications already link to them
(`server/samples/ws_notifications_message.json`, `server/notify/`).

## Non-goals

- Ghost rep (screen 6) and "Record reference rep": there is no reference-rep data on the server.
- Therapist accounts or login: the server has one full-scope `MIRA_API_TOKEN`. The dashboard keeps this behind one
  `auth.js` module so a real login can replace it later without touching screens.
- A build step, npm dependencies, a CDN. (Three.js is vendored, pinned, as one file plus its addon.)
- iOS/LCD changes and changes to the patient-pairing work on `wire-tri`.

## Architecture

Plain HTML + CSS + vanilla ES modules, the same approach as `device/kiosk`. There is no build step and nothing runs
at runtime except files from the page's own origin. The server already mounts `device/dashboard` at `/dashboard/`.

```
device/dashboard/
  index.html               shell markup, <meta> CSP (with the importmap's sha256), importmap for three
  css/tokens.css           copy of kiosk tokens + dashboard additions (watch amber, card, shadows, projector theme)
  css/base.css             reset, typography, buttons, chips, cards, forms, focus rings
  css/shell.css            sidebar, page header, layout grid
  css/screens/*.css        one stylesheet per screen
  js/main.js               boot: auth → capabilities → sockets → router → shell
  js/core/dom.js           h(tag, attrs, ...children), clear(el); never innerHTML
  js/core/router.js        hash router: parse(hash) → {name, params, query}; link builders
  js/core/store.js         tiny observable store (get/set/subscribe) for shell-wide state
  js/core/format.js        dates, "wk 3", degrees, percents, relative times (pure)
  js/core/links.js         safeInternalLink(link) — only "/dashboard#/…" routes survive
  js/net/auth.js           token + therapist id in sessionStorage; signOut(); onUnauthorized
  js/net/api.js            fetch wrapper: base, bearer, timeout, JSON, typed ApiError(status, detail)
  js/net/endpoints.js      one function per server route (the only file that knows paths)
  js/net/socket.js         copy of device/kiosk/js/socket.js (drift-guarded by a test)
  js/net/sockets.js        SocketHub: opens /ws/{events,notifications,live,pose} on demand, fan-out
  js/net/capabilities.js   reads /openapi.json once; has("/patients/{patient_id}/messages") etc.
  js/ui/*.js               shared components: sidebar, toasts, proposalCard, aiDraftChip, statusIcon,
                           balanceBar, stepper, slider, segmented, emptyState, errorState
  js/screens/<name>/       one folder per screen: model.js (pure view model) + view.js (DOM) + index.js
  js/three/                room.js (scene), skeleton.js (33-joint avatar), joints.js (MediaPipe indices)
  vendor/three/            three.module.min.js, OrbitControls.js, LICENSE, VERSION (pinned, sha256 listed)
  assets/fonts/            Inter variable (copied from kiosk) + OFL.txt
  assets/icons/            SVG icons used by the shell and screens
  tests/*.test.mjs         node --test, no dependencies; fixtures are server/samples/*.json
  README.md                how to run, sign in, test, and where every screen's data comes from
```

Every screen exports `mount(root, ctx) → unmount()`. `ctx = { api, sockets, auth, caps, navigate, toast, store }`.
A screen's `model.js` is pure (server JSON in, view model out) and unit-tested against `server/samples`. `view.js`
builds DOM from the view model only. This keeps screens testable without a browser and keeps the server contract in
one place: if a server payload changes, `server/tests/test_samples.py` and the dashboard model tests both fail.

### Networking

- REST: `api.js` adds `Authorization: Bearer <token>`, a 10 s timeout (`AbortController`), `Accept:
  application/json`, and turns non-2xx into `ApiError {status, detail}`. 401 → `auth.signOut()` and the sign-in
  screen. 503 → "History needs the database" empty state. 409 on a proposal → refresh the card ("already decided").
- WebSockets: `SocketHub` owns one `ReconnectingSocket` per channel. `events` and `notifications` stay open while
  signed in; `live` and `pose` open only while the Live or Patient-review Live tab is mounted, and close on unmount.
  `/ws/notifications` resumes with `since_id` = the last id seen.
- Capabilities: endpoints that are being built right now (`/pair`, `/patients/{id}/pairing-code`,
  `/patients/{id}/messages`, `/live/say`) are detected from the server's `/openapi.json`. Buttons that need them
  (Add patient, Message Maya, Say it through Mira) are rendered only when the route exists. When the
  `wire-tri` bridge work merges, they light up without a dashboard change.

### Security

- CSP in `<meta>`: `default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self';
  script-src 'self' 'sha256-<importmap>'; font-src 'self'; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'` (the last is ignored in `<meta>` but documents intent), `referrer: no-referrer`. A test
  recomputes the importmap hash so the CSP can't drift.
- No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` or `new Function` anywhere in
  `js/` (enforced by a test). All server text, including Copilot answers and AI drafts, is set with
  `textContent`. Copilot SQL is shown in a `<pre>` via `textContent`.
- The token is never in a URL except the WebSocket `?token=` query (the only option browsers have; the server
  already requires it). It lives in `sessionStorage` (cleared when the tab closes), never `localStorage`, never
  logged. "Sign out" clears it and closes every socket.
- Notification `link`s go through `safeInternalLink`: only `/dashboard#/…` routes are followed; anything else is
  dropped.
- The server serves `device/dashboard` with the same filter as the kiosk: `tests/`, `dev/` and READMEs are not
  served.
- Every write is an explicit click, AI output is always labelled "AI draft" until a therapist approves it, and
  nothing is sent to a patient without "Approve and send".

### Server additions (small, in new files, to keep the `wire-tri` merge clean)

All behind `require_token`. New routers go in `server/api/dashboard.py`; new SQL in
`server/db/dashboard_queries.py`; new writes in `server/db/dashboard_writes.py`. `server/main.py` changes by two
lines (mount filter + router include). Pure logic (triage) lives in `server/dashboard_triage.py` and is unit-tested
without a database.

| Route | Purpose | Data |
|---|---|---|
| `GET /caseload?therapist_id=` | Caseload buckets with a one-line reason and evidence | patients + open alerts + unacknowledged `memory_events` + last session pain/effort + flags + adherence |
| `GET /patients/{id}/memory?days=21` | "Therapist Memory" list | `memory_events` |
| `POST /sessions/{id}/progress-note` `{therapist_id, text}` | Approve (optionally edited) AI progress note | `agent_actions` row `agent='therapist', action='progress_note_approved'` (no schema change) |
| `PATCH /patients/{id}/limits` `{therapist_id, hr_limit}` | Heart-rate limit on Prescribe | `patients.hr_limit` (40–220) |

Sample JSON for each new route goes in `server/samples/` and is covered by `test_samples.py`, like every other
route.

### Triage rules (`GET /caseload`)

Evaluated per patient. The first matching rule gives the reason and evidence:

- **Needs you**: an open alert (fall/pain/HR) → "Fall detected, not resolved"; last session `pain_post ≥ 7` →
  "Reported pain 7 out of 10 after …", evidence "Pain 3 → 7"; an unacknowledged `compensation` or
  `asymmetry_worse` memory event → its message, evidence from its `evidence` json; 3+ missed days with a
  prescription active → "Missed N sessions in a row", evidence "0 of N sessions done".
- **Watch**: an unacknowledged `pain` memory event below 7; ≥ 3 flagged reps in the last session → "Knee drifting
  inward on 4 of 12 reps", evidence "Flagged reps 1 → 4"; effort ≥ 8 on 3 sessions running.
- **On track**: everything else, with the latest `depth_gain`/milestone message as the reason when there is one,
  otherwise "Last session …".

Inside a bucket, the order is severity first, then most recent signal. The response also carries `today:
{sessions_done, live_now}`. `live_now` is the patient in the current `/ws/live` session if phase ≠ idle.

## Visual system (hi-fi)

- Canvas: the aurora gradient from `tokens.css` (`--color-canvas`, `--color-mist`, `--color-sky`). Sidebar:
  translucent white with the mascot mark. Cards: white, 20 px radius, `0 1px 2px rgba(28,26,34,.04), 0 8px 24px
  rgba(143,186,204,.12)`.
- Type: Inter. Page title 32/700, card title 20/600, body 16/400, meta 14/400 `--color-ink-soft`.
- Primary button: `--color-accent` fill, white text, pill. Secondary: white with 1.5 px ink border, pill.
- Status: needs-you = red triangle + red evidence text; watch = amber diamond (`--color-watch #b54708`); on-track
  = green check (`--color-accent`). Status always pairs an icon with text, never colour alone.
- AI: "✦ AI draft" chip with a dashed berry border; it stays until the therapist approves.
- Reps: good = `--color-accent-bright`, flagged = `--color-alert` with a ⚠ marker above.
- Balance bar: affected side red when it carries less than 50 − threshold %.
- Projector theme (`data-theme="projector"`): dark canvas, +2 px type, higher contrast (`VICTORIA.md`
  "Projector check"). Toggled from the sidebar footer, remembered in `localStorage` (a convenience only).
- Accessibility: landmarks, a real `<nav>`, buttons not divs, visible focus rings, `aria-live="polite"` toasts,
  `prefers-reduced-motion` disables animations, 44 px targets, WCAG AA contrast.
- Layout: designed at 1440×1024. Minimum supported width is 1280; below that the right rail stacks under the
  main column.

## Testing

- `node --test device/dashboard/tests/*.test.mjs`: models against `server/samples`, router, links, api (fake
  fetch), auth (fake storage), socket drift guard, CSP hash, a banned-API scan.
- `make test` (pytest): triage unit tests, new routes with `TestClient` (with and without DB, as existing tests
  do), static filter for `/dashboard/tests/…` → 404, sample shapes.
- Manual: `make run` with `ENGINE=mock`, open `/dashboard/`, walk every screen at 1440×1024 and 1920×1080
  (projector theme), compare with the Figma frames.

## Milestones (each ends with a code review and debug pass)

- **M1 Foundation**: static hardening, scaffold, tokens, core/net modules, shell, sign-in, capabilities.
- **M2 Caseload**: `/caseload` + triage, Caseload screen with filters, Today, Copilot preview and Live-now cards.
- **M3 Patient review**: memory + progress-note routes, 3D room, replay + rep timeline, the four cards.
- **M4 Prescribe**: limits route, Prescribe screen, shared proposal cards.
- **M5 Care Copilot, alerts, notifications**: chat, charts, show-the-query, proposals tabs, alert banners, toasts,
  bell.
- **M6 Live session + Time-lapse + projector polish**.
