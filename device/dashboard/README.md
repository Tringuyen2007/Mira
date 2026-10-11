# Mira therapist web dashboard

Plain HTML + CSS + vanilla ES modules, the same approach as `device/kiosk`. No build step, no npm, no bundler,
no CDN — nothing runs at runtime except files served from the page's own origin. The server mounts this folder
at `/dashboard/` (and never serves `dev/`, `tests/` or any `README.md` from it — see "Security").

Design source: Figma `MIRA` file, page **Web Dashboard Wireframe**; visual language from the kiosk hi-fi
tokens. Full design doc: `docs/superpowers/specs/2026-10-11-therapist-dashboard-design.md`.

## Run it

```
make run
```

Then open `http://<server-host>:8000/dashboard/` (e.g. `http://localhost:8000/dashboard/` on the server
machine itself, or `http://<laptop>.local:8000/dashboard/` from another device on the same network). The
dashboard can also run from its own dev server (e.g. `npx serve device/dashboard` or Vite) pointed at the same
API host — CORS allows any origin and the token requirement is unchanged.

## Sign in

Sign-in needs the server's `MIRA_API_TOKEN` and a therapist id (any `[A-Za-z0-9_-]{1,64}` string your caseload
data uses, e.g. `dr_lee`). The server refuses to start without `MIRA_API_TOKEN` set (`server/config.py`); ask
whoever deployed it for the value — never commit it, never put it in a URL except the WebSocket `?token=`
query, and never log it. On success the token and therapist id are kept in `sessionStorage` only (gone when
the tab closes); "Sign out" (and a 401 from any request) clears them and returns to the sign-in screen.

## Test

```
make test-dashboard        # this folder's Node tests only (CSP, banned APIs, every screen's model/view/index)
make test                  # the whole repo: test-dashboard, then the Python test suite
```

`node --test "device/dashboard/tests/*.test.mjs"` runs the same thing directly. There's no browser test runner;
`tests/fakedom.mjs` is a small fake DOM (enough `createElement`/`querySelectorAll`/event dispatch for the
`h()`-built views) so screen and component tests run under plain Node.

## Screens → endpoints

| Screen | Route | Endpoints |
|---|---|---|
| Caseload | `#/caseload` (`?filter=needs\|watch\|on_track`) | `GET /caseload?therapist_id=` |
| Patient review | `#/patients/:pid`, `#/replay/:sid` | `GET /patients/{id}`, `GET /patients/{id}/memory?days=21`, `GET /sessions/{id}`, `GET /sessions/{id}/frames?...`, `POST /sessions/{id}/progress-note`, `GET /sessions/{id}/progress-note`; `POST /patients/{id}/messages` when capability-gated (see below) |
| Prescribe | `#/patients/:pid/prescribe` | `GET /prescriptions/{patient_id}`, `PUT /prescriptions/{patient_id}`, `PATCH /patients/{id}/limits`; `GET /exercises` when capability-gated |
| Recovery time-lapse | `#/patients/:pid/timelapse` | `GET /patients/{id}/session-history` (date pickers: earliest vs latest by default), `GET /sessions/{id}`, `GET /sessions/{id}/frames?...` (two dates, Overlay toggle). "Add to progress note" hands the headline to Patient review in memory (`store.noteDraft`, consumed once, labelled "From time-lapse — review before approving"), never in the URL |
| Mira Assistant | `#/assistant` (`#/copilot` kept as an alias, redirects) and `#/proposals/:id` | `POST /copilot/ask`, `POST /copilot/review/{patient_id}`, `GET /proposals?status=&patient_id=`, `POST /proposals/{id}/approve\|edit\|dismiss` |
| Live session | `#/live`, `#/live/:pid` | `GET /caseload` (who's live), `/ws/live`, `/ws/pose`; `POST /live/say` when capability-gated |
| Sidebar (every screen) | — | `GET /alerts?open=true`, `POST /alerts/{id}/resolve`, `GET /notifications?...`, `GET /notifications/count`, `POST /notifications/{id}/read\|read-all\|ack`, `/ws/events`, `/ws/notifications?since_id=` |

Full request/response shapes and samples: repo root `README.md` → "For the therapist dashboard", and
`server/samples/`.

## Mira Assistant

The renamed Care Copilot screen (`js/screens/assistant/`, route `#/assistant`; server module/route names —
`/copilot/ask`, `server/agents/copilot.py` — are unchanged). It answers questions about the caseload with
Gemini plus a guarded, read-only SQL tool against the therapy database (no write access, no arbitrary query
text from the model reaches the database unchecked).

What it can do:

- **Ask anything about the caseload** — suggested starting questions include "Summarise today", "Who missed
  sessions?", "Which of my patients are compensating?" and "Draft progress notes". A patient picker ("All
  patients" or one from the caseload) scopes the question.
- **Follow-ups with context** — the last 3 question/answer turns are sent back as `context` on the next
  `POST /copilot/ask`, so "and last week?" works. The conversation lives in memory only; nothing is persisted
  by the dashboard.
- **"Review this patient"** — `POST /copilot/review/{patient_id}`, a one-click comparison of a patient's last
  week against the week before; may leave one proposal for the therapist to decide on.
- **"Add to progress note"** — turns an answer into a draft for a specific session; the therapist can edit the
  text inline, then must explicitly click "Approve as progress note" (`POST /sessions/{id}/progress-note`)
  before anything is written. Nothing is ever sent automatically.
- **Proposals rail** — Waiting / Approved / Dismissed tabs of `proposalCard`s (`js/ui/proposalCard.js`):
  approve, edit (inline form, posts a `PlanChange`), or dismiss. A 409 (already decided elsewhere) refreshes
  the card instead of erroring.

**Every AI output is labelled.** Answer bubbles, drafts and proposal cards all carry a `chip--ai` "✦ AI draft"
badge (`js/ui/aiDraftChip.js`) until a therapist explicitly approves them — this is never removed by the
dashboard on its own. A draft's only actions are "Copy text" or, if the messages capability exists for that
patient, "Approve and send"; there is no path that sends a draft without that click.

## Capability-gated features

`js/net/capabilities.js` probes `GET /openapi.json` once at boot (no token needed) and exposes
`caps.has(pathTemplate)`. Screens hide or disable a feature instead of guessing or erroring when the matching
route isn't present on the running server:

| Feature | Waits on | Screen |
|---|---|---|
| "Add patient" (pairing code) | `/patients/{patient_id}/pairing-code` | Caseload |
| Message a patient | `/patients/{patient_id}/messages` | Patient review, Mira Assistant ("Approve and send" vs. "Copy text") |
| "Send a cue" during a live session | `/live/say` | Live session |
| Add an exercise from the library | `/exercises` | Prescribe |

## `rule_overrides`

Per-exercise overrides of the thresholds that decide when a rep is flagged, stored in
`prescriptions.rule_overrides` (set via `PUT /prescriptions/{patient_id}`) and edited on the Prescribe screen's
"When should Mira flag a rep?" card (`js/screens/prescribe/model.js`):

| Key | Meaning | Default |
|---|---|---|
| `weight_shift_pct` | Percentage points below 50% load on the affected leg before `weight_shift` flags | 15 |
| `knee_cave_deg` | Degrees of inward knee drift (valgus) before `knee_cave_l`/`knee_cave_r` flags | 10° |
| `trunk_lean_deg` | Degrees of trunk lean before `trunk_lean` flags | 20° |

An absent key means "use the default" everywhere it's read — never `null` meaning "disabled". Full detail and
the server-side contract: repo root `README.md` → "Form-rule thresholds (`rule_overrides`)".

## Avatar renderer plug-in point

Patient review's "3D room" is a placeholder today (`js/avatar/avatarView.js`): a card with four live angle
pills and the shared balance bar, no canvas or WebGL. It implements the exact interface a real Three.js
renderer will plug into — see **`js/avatar/README.md`** for `createAvatarView`/`registerAvatarRenderer`, the
frame shape, the mat-frame coordinate contract and the MediaPipe joint-index constants it shares with
`js/three/joints.js`. Projector-theme switches dispatch a `window` `CustomEvent("mira:theme-change", { detail:
{ theme } })` (`js/ui/theme.js`) for a renderer that reads raw colour values instead of CSS custom properties.

## Security rules

- **No `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`/`eval`/`new Function`** anywhere in `js/`.
  All server text goes through `textContent` via the `h()` helper (`js/core/dom.js`), which also whitelists URL
  attributes (`href`/`src`/...) to same-origin/relative/hash paths only. Enforced by `tests/banned.test.mjs`.
- **The API token lives only in `sessionStorage`**, key `mira.dashboard.auth` (`js/net/auth.js`) — never
  `localStorage`, never a URL except the WebSocket `?token=` query, never logged. `tests/banned.test.mjs` also
  rejects `localStorage.setItem(...)` calls with "auth" in them and any `console.log`/`info`/`debug` call that
  mentions a token.
- **Projector-theme persistence is the one localStorage exception**, and it's a convenience only: key
  `mira.dashboard.theme` (`js/ui/theme.js`), holding just `"light"` or `"projector"`, wrapped in `try/catch` so
  a blocked or full store never breaks the toggle — it just forgets the choice on the next load.
- **CSP** (`<meta>` in `index.html`): `default-src 'self'; connect-src 'self'; img-src 'self' data:;
  style-src 'self'; script-src 'self' 'sha256-<importmap hash>'; font-src 'self'; object-src 'none';
  base-uri 'none'; form-action 'none'`. No inline scripts except the one `<script type="importmap">` tag (its
  exact text is hashed into `script-src`), no inline `style="..."` attributes anywhere. If the importmap text
  ever changes, recompute the hash and update the `<meta>` tag:

  ```
  node -e "const c=require('crypto');const s=require('fs').readFileSync('device/dashboard/index.html','utf8').match(/<script type=\"importmap\">([\s\S]*?)<\/script>/)[1];console.log(c.createHash('sha256').update(s).digest('base64'))"
  ```

  `tests/csp.test.mjs` checks the `<meta>` tag's hash matches the live importmap text and that nothing else
  inline (script or style) slipped in.
- **Clickjacking:** `<meta http-equiv>` CSP can't set `frame-ancestors`, so `main.js` refuses to boot (clears
  `document.body`) if it detects it's running inside a frame.

## Projector theme and accessibility

- `data-theme="projector"` on `<html>` swaps every colour/type-size token in `css/tokens.css` for a dark,
  higher-contrast set. The switch lives in the sidebar footer ("Projector mode", `role="switch"`,
  `aria-checked`) and is applied as the very first statement `main.js` runs — before any other import's side
  effects, before anything is rendered — so the page never paints the wrong theme and then flips.
- Skip link ("Skip to main content") as the first focusable element in the page, jumping to `#main`
  (`tabindex="-1"`) without disturbing the hash router.
- Every icon-only button carries an `aria-label`; decorative icons are `aria-hidden`. One `<h1>` per screen.
  Visible focus rings (`:focus-visible`, token `--color-ring-focus`, checked ≥ 3:1 non-text contrast against
  both themes' canvas/card colours). `prefers-reduced-motion: reduce` disables animations and transitions.
- Layout is designed at 1440×1024, holds down to 1280px wide (list/detail screens collapse their side rail to
  one column below that), and caps at `max-width: 1600px` centred in `#main` so nothing stretches at
  1920×1080 or wider.

## Known limitations

- No reference-rep / "Ghost rep" screen — there's no reference-rep data on the server.
- No therapist accounts: the server has one full-scope `MIRA_API_TOKEN`; `js/net/auth.js` keeps this behind one
  module so a real login can replace it later without touching any screen.
- The 3D room is the angle-pill placeholder described above, not yet the real Three.js scene (`vendor/three`
  is vendored and ready for it).
- Desktop notification pop-ups (browser Notification API) only work in a secure context — `localhost` or
  HTTPS. From another machine over plain `http://<host>.local:8000`, use the in-page toasts/bell instead.
