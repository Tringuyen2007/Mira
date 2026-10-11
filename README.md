# Mira server

FastAPI server for the Mira physical-therapy device: WebSocket streams for the device and dashboard,
the engine bridge (mock / replay / live vision), the safety path (fall check-in, emergency call), the AI agents
and the REST API. This README is everything a new developer needs; the team plan (TRI.md, kept by Tri) has the
design background, but no setup step depends on it.

Open `device/Mira.xcworkspace` in Xcode to see the iPhone and Watch projects
together. Choose **Mira** for the patient app, **MiraWatchPreview** for the
interactive Watch mockup on iPhone, or **MiraWatchMock** for watchOS.

iOS networking setup and tests: [Networking guide](device/ios/NETWORKING.md).

Onboarding flow, remaining integrations, and checks: [Onboarding guide](device/ios/ONBOARDING.md).

Interactive Watch concept and preview instructions: [Watch mockup](device/watch/README.md).

## Repo layout

| Path | Owner | What |
|---|---|---|
| `server/` | Tri | this FastAPI server (everything below) |
| `vision/` | Eric | vision engine (camera, pose, reps, falls) — plugs in via the frame contract below |
| `device/ios/` | Josie | iOS app (Xcode project) — networking contract in `device/ios/NETWORKING.md` |
| `device/dashboard/` | Victoria | therapist dashboard (served at `/dashboard` when present) |
| `device/kiosk/` | Victoria / Tri | Mira LCD kiosk page (served at `/device`) |
| `data/` | shared | cues, audio, recordings (recordings and AI cache are git-ignored) |
| `tools/` | shared | dev utilities (seeders, mocks, scripts) |

## Prerequisites

| Tool | Why | Get it |
|---|---|---|
| Python **3.12** | the server (MediaPipe wheels need 3.12) | `uv python install 3.12` ([uv](https://docs.astral.sh/uv/)) or the python.org installer |
| Docker Desktop | the local TimescaleDB (`make db-local`) and the DB tests (they **skip silently** without it) | docker.com → Docker Desktop, start it once |
| `make` | the commands below | macOS: `xcode-select --install`; Linux: your package manager |
| git | clone | |

No cloud account or API key is needed to run the server, the mock streams or the tests.

## Quick start

```bash
git clone <repo-url> Mira && cd Mira
python3.12 -m venv .venv                       # or: uv venv --python 3.12 .venv
.venv/bin/pip install -r server/requirements.txt
cp .env.example .env
python3 -c "import secrets;print(secrets.token_urlsafe(32))"   # paste as MIRA_API_TOKEN in .env
make run                                       # http://localhost:8000/health → {"status": "ok"}
make test                                      # ~70 s; Docker must be running for the DB tests
make db-local                                  # optional: local TimescaleDB with 75 days of simulated history
```

`MIRA_API_TOKEN` is required: the server **refuses to start** without one, with the `.env.example`
placeholder, or with a token shorter than 24 characters (anyone on the hall Wi-Fi could otherwise drive the API).
The server starts without any external keys and without a database (`MIRA_SOURCE=mock` is the default).
`MIRA_DEVICE_TOKEN` is optional: set it (same rules as `MIRA_API_TOKEN`, but it must differ from it) to let the
Mira LCD kiosk connect with a least-privilege token instead of the full API one — see
[device/kiosk/README.md](device/kiosk/README.md).
Use `make ... VENV=/path/to/venv` if your virtual environment lives elsewhere.

The tests never read your `.env` (`server/tests/conftest.py` sets `MIRA_SKIP_DOTENV=1` before importing the
config), never call Gemini, ElevenLabs or Retell, and never touch Tiger Cloud.

## Database (Tiger Data / TimescaleDB)

The server runs without a database; set `TIGER_DSN` to persist frames, sessions, reps and alerts.
Every setup step is a script, so a database is rebuilt, never restored from a dump.

### Local (Docker)

```bash
make db-local     # start (or reuse) the mira-db container on :55432, then rebuild the `mira` database:
                  # schema → rollup → roles → seed → 900k simulated frames → refresh → Data Guard check 3
                  # → compress → columnstore policy → jobs   (~30-40 s, prints a step log)
```

Then in `.env`:

```bash
TIGER_DSN=postgresql://postgres:mira@localhost:55432/mira
COPILOT_DSN=postgresql://copilot_ro:mira-local-ro@localhost:55432/mira
```

**The local `copilot_ro` password is always `mira-local-ro`.** `copilot_ro` is a cluster-wide role, so
`make db-local`, `make demo-data` and the DB tests all set it to that fixed value (`LOCAL_RO_PASSWORD` in the
Makefile, `RO_PASSWORD` in `server/tests/dbutil.py`); a local app using it keeps working after `make test`, and the
Tiger password is never copied into the local container. `make db-local` drops and rebuilds `mira` each time.

### Tiger Cloud (free trial)

1. Sign up at [console.cloud.timescale.com](https://console.cloud.timescale.com) (Tiger Data, 30-day trial, no card).
2. **Create service** → PostgreSQL with the TimescaleDB extension (time-series), the region nearest you,
   the smallest size. When it is ready, copy the connection string from the service's **Connection info**
   (user `tsdbadmin`; the password is shown once, so save it now).
3. Pick a strong password for the read-only role and put both in `.env`:

   ```bash
   TIGER_DSN=postgres://tsdbadmin:<password>@<host>:<port>/tsdb?sslmode=require
   COPILOT_RO_PASSWORD=<new strong password>
   COPILOT_DSN=postgres://copilot_ro:<that password>@<host>:<port>/tsdb?sslmode=require
   ```
4. `make db-setup DSN="postgres://tsdbadmin:<password>@<host>:<port>/tsdb?sslmode=require"` (the owner DSN, in
   quotes). It is idempotent: steps already done are skipped, and `roles.sql` (step 3) is re-applied every time.

**Re-run `make db-setup` (and `make db-local`) after any change to `server/db/roles.sql`.** Existing databases
keep their old grants otherwise. The server checks this at startup (one WARNING `copilot_ro grants are stale`
naming each missing grant) and `/health/data` → `copilot_ro_grants` shows the same list and turns `failing`.
Manual check: `SELECT has_table_privilege('copilot_ro','pose_frames','SELECT'),
has_column_privilege('copilot_ro','patients','emergency_contact_phone','SELECT')` must return `true, false`.

Individual pieces: `python -m server.db.apply_sql <file.sql> <dsn>` (one statement per round trip, autocommit),
`python -m server.db.seed <dsn>`, `python -m server.sim.simulate_history --dsn <dsn> --seed 42`,
`python -m server.db.compress_history <dsn>`. The simulator writes the Copilot eval answers to
`server/sim/expected.json`. `server.db.setup` loads `.env` (through `server.config`) like the server does.

DB tests use the local container (`MIRA_TEST_ADMIN_DSN`, default `postgresql://postgres:mira@localhost:55432/postgres`),
create a throwaway database each and skip when it is unreachable. They never touch Tiger Cloud.

## API keys (all optional)

Without keys every service uses its fallback (fake Gemini, captions + recorded MP3s, log-only calls).

| Key | Where to click | `.env` |
|---|---|---|
| Gemini | [aistudio.google.com](https://aistudio.google.com) → **Get API key** → **Create API key** (free tier) | `GEMINI_API_KEY=...`, `GEMINI_MODE=live` |
| ElevenLabs | [elevenlabs.io](https://elevenlabs.io) → profile menu → **API Keys** → **Create**; **Voices** → pick a voice (e.g. "Sarah") → **Copy voice ID** | `ELEVEN_API_KEY=...`, `ELEVEN_VOICE_ID=...` |
| Retell | [dashboard.retellai.com](https://dashboard.retellai.com) → **API Keys** → copy the key; **Phone Numbers** → **Buy number** (US) | `RETELL_API_KEY=...`, `RETELL_FROM_NUMBER=+1...` |

Then configure the Retell agent from code (free, never places a call):

```bash
.venv/bin/python -m server.notify.retell_setup            # dry run: prints the LLM / agent / number payloads
.venv/bin/python -m server.notify.retell_setup --apply    # create/update, publish, bind the number, verify
```

It must end with `N/N fields verified` and prints `RETELL_AGENT_ID` and `RETELL_AGENT_VERSION` for `.env`.
Real calls stay off until `CALLS_DISABLED=0` (see "Retell live calls: BLOCKED" below).

## Kill switches

| Variable | Default | Effect |
|---|---|---|
| `DB_DISABLED=1` | 0 | run without the database even if `TIGER_DSN` is set (reads return 503, streams keep working) |
| `GEMINI_MODE` | `fake` | `fake` = no Gemini requests; `replay` = saved answers from `data/ai_cache/`; `live` = real requests |
| `GEMINI_DISABLED=1` | 0 | every AI caller uses its fallback (templates, SQL-only notes, cached demo answer) |
| `ELEVEN_DISABLED=1` | 0 | no ElevenLabs requests; captions and already-recorded MP3s only |
| `CALLS_DISABLED=1` | 1 | log-only call simulator, no Retell calls, no credits spent |
| `ALLOW_TEST_CALLS=1` | 0 | `POST /me/test-alert` may place a real call (still needs `CALLS_DISABLED=0` too; else it only logs a notification) |
| `INSIGHT_ENABLED=0` | 1 | no scheduled Insight agent / milestones |
| `DATA_GUARD_ENABLED=0` | 1 | no scheduled Data Guard checks |
| `COPILOT_CACHED_DEMO=1` | 0 | the demo question is answered from `data/copilot_demo.json` (stage backup) |
| `DEMO_TOOLS_ENABLED=1` | 0 | enables `POST /demo/trigger_fall` (rehearsals only) |
| `MOCK_LOOP=1` | 0 | the mock squat program repeats forever instead of finishing after 3 sets (pre-Plan-2 behaviour); implies no auto-stop/auto-restart (below) |
| `AUTO_STOP_AFTER_LAST_SET=0` | 1 | don't auto-end the session once `set == sets_target` (needed for engines that already send their own `stopped`) |

## Run

```bash
make run      # uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1
make test
```

Always exactly **one** server process (`--workers 1`): live state is in memory.

### Deploy (Vultr)

The demo server runs on a Vultr box as the systemd unit `mira` (`/opt/mira/app`, port 8000, plain HTTP).
`tools/deploy_vultr.sh [ref]` (default `origin/main`) ships the tracked files of that ref with rsync, keeps the
box's own `.env`, `.venv`, logs and generated data, installs requirements, creates a `MIRA_DEVICE_TOKEN` on first
deploy, restarts and checks `/health`. Override with `MIRA_HOST`, `MIRA_SSH_USER`, `MIRA_SSH_KEY`.
Plain HTTP sends both tokens in clear text; put Caddy or a tunnel with TLS in front before real patient use.

## Troubleshooting

- **`RuntimeError: MIRA_API_TOKEN ...; refusing to start`**: set a generated token (Quick start).
- **DB tests show as skipped**: Docker Desktop isn't running or `mira-db` isn't up; run `make db-local` once.
- **`password authentication failed for user "copilot_ro"` locally**: your local `COPILOT_DSN` must use
  `mira-local-ro`; `make db-local` resets the role to it.
- **`/health/data` is `failing` with `copilot_ro_grants` problems, or Data Guard errors say `permission denied`**:
  the database has old grants; re-run `make db-setup DSN=...` (Tiger) or `make db-local` (local).
- **`/stats` → `notifier.mode` is `log_only`** although `CALLS_DISABLED=0`: one of `RETELL_API_KEY`,
  `RETELL_FROM_NUMBER`, `RETELL_AGENT_ID` is missing.
- **Gemini answers are templates / `fallback` is set**: `GEMINI_MODE` is `fake`, the key is missing, or the
  daily budget is used up (`/stats` → `ai`); background agents are refused from 85 % of the budget.
- **Port 55432 already in use**: another Postgres container; `make db-local DB_LOCAL_PORT=55433` and update the DSNs
  (the tests then need `MIRA_TEST_ADMIN_DSN=postgresql://postgres:mira@localhost:55433/postgres`).
- **A tunnel (cloudflared/ngrok) for the Retell webhook**: do **not** rewrite the Host header
  (no cloudflared `httpHostHeader`, no ngrok `--host-header=rewrite`). The tunnel filter recognises tunnel
  traffic by `PUBLIC_WEBHOOK_HOST`; with a rewritten Host it looks like LAN traffic and only the token protects
  the API.

## Try it

```bash
TOKEN=...   # the MIRA_API_TOKEN from .env

curl http://localhost:8000/health                                   # no auth
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/stats  # 401 without the header
```

WebSockets take the token in the query string (browsers can't set headers on WebSockets):

```
ws://localhost:8000/ws/pose?token=$TOKEN     # 25 Hz pose
ws://localhost:8000/ws/live?token=$TOKEN     # ~10 Hz session state
ws://localhost:8000/ws/events?token=$TOKEN   # events; send client messages here too
ws://localhost:8000/ws/notifications?token=$TOKEN&since_id=0   # therapist notifications (replay + live)
```

A wrong or missing token closes any WebSocket with code 1008 before it is accepted.

Session replay (needs a database): `GET /sessions/{id}/frames?downsample=300` gives the lttb knee curve;
`downsample=0` gives full-rate frames, at most 9,000 per page. A full page carries `next_t0`; request again with
`t0=<next_t0>` (and the same `t1`) until `next_t0` is `null`.

Rehearse an emergency: `MIRA_SOURCE=mock:fall` (fall after 20 s, `fall_status` every 2 s,
optional thumbs up `MOCK_FALL_RESPONDS_AT` seconds after the fall), or with `DEMO_TOOLS_ENABLED=1`:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"responds_at_s": 45}' http://localhost:8000/demo/trigger_fall
```

Example payloads for every WebSocket message and REST response are in `server/samples/`.

## For the therapist dashboard

Victoria's dashboard lives in `device/dashboard/` and is served by this server at `/dashboard/` (static files,
`index.html` as the entry point; nothing is mounted while the folder is empty). It can also run on its own dev
server (e.g. Vite on `http://localhost:5173`): CORS allows any origin, and the token is still required.

**Auth:** every REST call sends `Authorization: Bearer <MIRA_API_TOKEN>` (401 without it, except `GET /health`);
every WebSocket URL carries `?token=<url-encoded token>` (closed with 1008 otherwise). How the dashboard gets the
token (prompt, local config) is the dashboard's choice; never commit it.

| Need | Endpoints (samples in `server/samples/`) |
|---|---|
| Patients | `GET /patients`, `GET /patients/{id}` (`rest_get_patients`, `rest_get_patient`) |
| Progress | `GET /patients/{id}/progress?exercise_id=&days=90` (`rest_get_patient_progress`) |
| Sessions, replay | `GET /sessions/{id}`; `GET /sessions/{id}/frames?downsample=300` (knee curve) or `downsample=0` (full frames, page with `t0=<next_t0>`) (`rest_get_session`, `rest_get_session_frames_*`) |
| Alerts | `GET /alerts?open=true`, `POST /alerts/{id}/resolve {resolved_by, note?}` (`rest_get_alerts`, `rest_post_alert_resolve`) |
| Plan proposals | `GET /proposals?status=pending&patient_id=`, `POST /proposals/{id}/approve {therapist_id}`, `/edit {therapist_id, change}`, `/dismiss {therapist_id, reason?}` (409 if already decided) (`rest_get_proposals`, `rest_post_proposal_approve`, `rest_error_409_proposal`) |
| Prescriptions | `GET /prescriptions/{patient_id}`, `PUT /prescriptions/{patient_id}` (`rest_get_prescriptions`, `rest_put_prescriptions`) |
| Copilot | `POST /copilot/ask {question, patient_id?}` (`rest_post_copilot_ask`) |
| Notifications | `GET /notifications?unread=&limit=&since_id=&therapist_id=`, `GET /notifications/count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `POST /notifications/{id}/ack {therapist_id}` (see "Dashboard notifications") |
| Health | `GET /health` (public), `GET /health/data`, `GET /stats` (`rest_get_health*`, `rest_get_stats`) |

WebSockets: `/ws/events` (alerts, proposals, session events; `ws_events_*`), `/ws/notifications?since_id=<last id>`
(replay + live; `ws_notifications_message`), `/ws/live` (`ws_live`), `/ws/pose` (`ws_pose`, 25 Hz). Every channel
sends `{"type":"ping"}` after 10 s of silence; ignore it. History routes need the database (503 with
`DB_DISABLED=1`; `rest_error_503`).

Desktop pop-ups (browser Notification API) only work in a secure context: open the dashboard at
`http://localhost:8000/dashboard/` on the server laptop (or over HTTPS). On another machine via
`http://<laptop>.local:8000` use in-page toasts. Request permission from a button click.

## Patient identity (phone)

Three token scopes (`server/security.py`):

| scope | who | how | can |
|---|---|---|---|
| `full` | dashboard, ops, tests | `MIRA_API_TOKEN` | everything |
| `device` | LCD kiosk | `MIRA_DEVICE_TOKEN` | `/ws/live`, `/ws/events` (allow-list), send `device_health` |
| `patient` | iPhone | per-phone token from `POST /pair` | `/me/*`, its own sessions/progress/prescriptions, `/ws/*` for its own session, send `command`/`hr`/`wrist`/`watch_button` |

Patient tokens (`pt_…`) are minted by `POST /pair` and stored hashed in `patient_devices`; `Principal.owns()`
lets a patient token through only for its own `patient_id` (403 `forbidden` for anyone else's). `full` always
passes.

| route | auth | body → response |
|---|---|---|
| `POST /pair` | none, 5 attempts/min/IP (any outcome) | `{code, device_name?, profile{name, height_cm, mass_kg, affected_side?, condition}, emergency_contact{name, phone, relationship?}, alert_therapist}` → `{patient_id, token, me}`. `404` unknown code, `410` expired or already used, `429` rate-limited. |
| `GET /me` | patient (or full with `?patient_id=`) | profile, care team, plan, LCD/calibration status |
| `PUT /me` | patient (or full with `?patient_id=`) | partial `{profile?, emergency_contact?, alert_therapist?, share_by_default?, device_prefs?}` → the updated `GET /me` body |
| `DELETE /me` | patient (or full with `?patient_id=`) | purges the patient's own non-simulated history (sessions, reps, pose frames, alerts, …), keeps the patient row and prescriptions, revokes the token; labeled simulated demo history is kept |
| `GET /me/export` | patient (or full with `?patient_id=`) | JSON of everything `DELETE /me` would delete, for "download my data" |
| `POST /patients/{id}/pairing-code` | full | mints a fresh single-use code, valid 24 h → `{code, expires_at}` |
| `DELETE /patients/{id}/devices` | full | revokes every paired phone for that patient |

A demo code `K7P4M2` is seeded for Maya and never expires; while `PAIRING_DEMO_CODE_REUSABLE=1` (default on)
it can be reused so the demo phone can re-pair. Every phone remembers whether it paired with a reusable code
(`patient_devices.via_reusable`, migration `004`). Setting `PAIRING_REUSABLE_UPDATES_PROFILE=0` on a public
server, once the demo phone has paired, locks those demo phones down: `/pair` with the reusable code no longer
rewrites Maya's profile or emergency contact, `PUT /me` from a demo phone ignores `profile` and
`emergency_contact` (other fields still apply), `DELETE /me` from a demo phone is a `403`, and
`POST /me/test-alert` from a demo phone only logs (never calls). Phones paired with a therapist-minted
single-use code are unaffected. The 10-active-phones cap only ever evicts the oldest demo phones, so strangers
pairing with the public code can never sign the real patient's phone out. The flag alone is not a secret,
though: on a public box also rotate `PAIRING_DEMO_CODE` (re-run the seed) so the published `K7P4M2` stops
working.

Patient-scope `POST /me/messages`, `POST /me/test-alert` and `POST /sessions` are each limited to 10 per
minute per patient (`429`; `server/api/ratelimit.py`); the admin token is never limited. A phone can't start
a session while a Safety Guardian incident is open for its patient (`409`).

Existing routes also accept a patient token, scoped to its own patient: `GET /sessions/{id}` (own session
only), `GET /patients/{id}`, `GET /patients/{id}/progress`, `GET /prescriptions/{id}` (writes stay `full`-only).
Any other patient's id is a `403`.

Patient WebSockets (`/ws/live`, `/ws/events`, `/ws/pose`) are filtered per message: an event tagged with a
`patient_id` reaches the phone only if it is the token's own patient; else one tagged with a `session_id`
reaches it only if that session (live or recently ended) is its patient's; untagged messages (`/ws/live` and
`/ws/pose` frames) reach it only while the current live session is its patient's. Otherwise the socket stays
open (pings only). Patient
clients may send `command`/`hr`/`wrist`/`watch_button`, not `device_health`.

Migrations (`server/db/migrations/NNN_name.sql`, idempotent) run at `server/db/setup.py` step 1b, via
`make db-migrate DSN=…`, and at server start when `DB_AUTO_MIGRATE=1` (default on).

## Care API

Plan 3: the therapist card, notes, AI-draft highlights, milestones and the patient/therapist message thread
(`server/api/care.py`), plus a "send a test alert" button. All routes take the same principal as `/me`
(patient token, or `full` with `?patient_id=`), via the shared `patient_for()` helper (`server/api/scope.py`).

| route | body → response |
|---|---|
| `GET /me/care` | `{therapist, next_visit_at, hr_limit, emergency_contact, alert_therapist, notes, highlights, unread_messages}` |
| `GET /me/messages?since_id=` | `{messages: [{id, sender, kind, body, created_at, read_at}, …]}` (ascending by id, `kind='message'` only); marks every unread message not sent by the patient as read |
| `POST /me/messages {body}` | `{message}` (sender forced to `"patient"`); emits the `message` event and notifies the therapist |
| `POST /patients/{id}/messages {body, kind?}` | full-token only; `kind` is `message`\|`note`\|`visit_highlight` (default `message`); `{message}`, sender `"therapist"` |
| `GET /patients/{id}/milestones` | `{patient_id, milestones: [{id, time, message, evidence}, …]}` from `memory_events` kind `milestone`, newest first |
| `POST /me/test-alert` | `{logged, called, note}` — always logs a therapist notification; only places a real call when `CALLS_DISABLED=0` **and** `ALLOW_TEST_CALLS=1` **and** the notifier supports it **and** the patient has an emergency contact phone |

The `message` event (`/ws/events`, filtered like every other patient-tagged event):
`{"type": "message", "patient_id": "...", "message": {id, sender, kind, body, created_at, read_at}}`.

The `messages` table (`server/db/migrations/003_messages.sql`): `id bigserial`, `patient_id` (FK
`patients.id`), `sender` (`patient`\|`therapist`\|`mira`), `kind` (`message`\|`note`\|`visit_highlight`,
default `message`), `body`, `created_at`, `read_at`; indexed on `(patient_id, id)`. `DELETE /me` purges a
patient's own rows along with the rest of their history.

## Device prefs

`PUT /me {device_prefs: {...}}` (at most 20 keys; flat values only: true/false, numbers or strings of at most
64 characters, else `422`) saves the patient's device preferences (captions, volume, slower speech) to
the `patients` row and, if that patient has a live session right now, pushes them straight onto the running
`/ws/live` stream (`server/api/me.py::put_me` sets `state.bridge.live.device_prefs`, no session restart
needed) — the next `/ws/live` tick already carries `device_prefs` (`server/engine_bridge.py`). The Mira LCD
kiosk reads it on every tick: `device/kiosk/js/state.js`'s `view()` sets `captionsOn = live.device_prefs?.captions
!== false`, hiding `#caption-bar` when false, and `main.js` applies `device_prefs.volume` (clamped 0..1,
default 1) to the audio player before each clip.

## Patient app data flow

The iOS app's `PatientStore` (`device/ios/Mira/App/PatientStore.swift`) loads `GET /me`, `GET /me/care`, the
patient's sessions, per-exercise progress, milestones and messages in parallel, and keeps each piece's
failure independent so a down endpoint doesn't blank the rest of the screen. `AppState.refreshMe()` drives
it and is called on app foreground, right after pairing, and whenever a `session_summary` event arrives on
`/ws/events` (session end) — so Home, Progress and Care stay in sync with the server without the patient
doing anything.

## Frame contract: joint angles

`angles.knee_l`, `knee_r`, `hip_l` and `hip_r` in every engine frame (the team plan's frame contract) are **flexion in degrees**:
0 = straight joint, larger = deeper bend (flexion = 180 minus the interior angle between the two bones).
A standing person reads about 5, the bottom of a squat about 70-90, and a "90 degree squat target" means 90 degrees of
knee flexion. The database rollup (`max_knee`, `p95_knee` = deepest bend), the simulator, `/ws/live`
`main_angle` (the deeper knee) and the mock source all use this convention, so the vision engine must emit it too.
See `server/samples/engine_frame.json`.
Logs are JSON lines in `logs/server.log` (phone numbers masked, secrets redacted).

### What the LCD needs from the engine

The Mira LCD kiosk (`device/kiosk/`, see its README) renders from the same event/frame stream as the iOS app —
no new engine API — but it depends on these specifically:

- `arrived`, then `awaiting_gesture` a few seconds later, before each session's first set: the gap is what
  keeps the greeting on screen, and `awaiting_gesture` makes the kiosk show "give me a thumbs up" instead of
  jumping straight to a countdown. (The mock holds the greeting `MOCK_GREETING_HOLD_S` = 3 s.) Put
  `reps_target`, `sets_target` and `exercise_id` on `arrived` so the greeting can show the plan ("3 sets · 10 reps").
- `gesture {name}` on a thumbs-up or raised hand.
- `countdown {n, set, reps_target, sets_target, exercise_id}`.
- `rep {index, set, quality}`.
- frames with `load_l_pct` (drives the L/R balance bar) and `hr` when available (drives the heart-rate chip;
  hidden when null).

## Engine reverse channel

The server sends these messages to the engine with `bridge.send_to_engine(msg)` (the mock source handles
`trigger_fall`, `incident_closed`, `command` and ignores the rest; the vision engine must apply the Coach
messages):

| `type` | Other fields | Sent by | Meaning |
|---|---|---|---|
| `trigger_fall` | `responds_at_s` | `/demo/trigger_fall` | start the scripted mock fall |
| `incident_closed` | — | Guardian | the fall incident is over; resume at the next set |
| `command` | `name: start\|pause\|skip\|stop`, `payload`, `session_id` | `/ws/events` client (`command`) | session control from the patient/companion app, see below |
| `end_set_early` | `session_id`, `set`, `reason: "fatigue"` | Coach | end the current set now (already ≥ `limits.min_reps`) |
| `extend_rest` | `session_id`, `rest_s` | Coach | make the coming rest `rest_s` seconds (≤ `limits.max_rest_s`) |
| `adjust_next_set` | `session_id`, `reps?`, `rest_s?` | Coach | use these (already clamped) values for the next set |
| `skip_next_exercise` | `session_id`, `body_part`, `reason: "pain"` | Coach | skip the next exercise that loads `body_part` |

Coach values are always inside the active prescription's `limits`; the Coach sends nothing while the Safety
Guardian holds the session, and it never sends a resume.

A client `command` on `/ws/events` named `start`, `pause`, `skip` or `stop` (§4.3; `pain`/`im_ok` are read
straight off the broadcast event by the Guardian/Coach and never reach the engine) is forwarded to the
engine as `{"type": "command", "name": ..., "payload": ..., "session_id": ...}` by
`main._forward_session_command`, the same reverse channel used for `incident_closed`. It works the same in
`ENGINE_MODE=thread` and `=process`. The mock source applies it: `pause` freezes the program (frames keep
flowing, the patient standing still, `/ws/live` reports `paused: true`) and emits `paused {reason: "user"}`;
`start` while paused emits `resumed` and lets the program continue where it left off; `skip` ends the
current set (`set_done` with the reps completed so far) and moves straight to the next set's countdown;
`stop` ends the program for good (`stopped`, `/ws/live` `phase: "done"`, no more reps). The server never
forwards a `start` for a session the Safety Guardian is holding open (a pain/HR incident): only the
Guardian's own `incident_closed`/resolution ends that pause, never the patient's `start`. The vision engine
should implement the same four commands.

## AI layer (Gemini + ElevenLabs)

All AI lives in `server/ai/`; nothing else imports the Google SDK.

- `gemini.py`: `await ask(agent, model, system, messages, tools=None, timeout_s=4.0, background=False,
  json_mode=False)` returns a `Reply` or raises `AIUnavailable` (the caller then uses its fallback).
  `GEMINI_MODE=fake` (default) uses fakes registered per agent with `register_fake(agent, fn)`; `replay` serves
  saved answers from `data/ai_cache/` (falls back to the fake); `live` calls Gemini and caches every answer
  (a caller that rejects an answer calls `await gemini.discard(reply)` so it is not replayed).
  Retries 429/5xx after 1 s and 3 s, honours retry hints, and on a daily-quota 429 for `GEMINI_MODEL_SMART`
  moves through `GEMINI_SMART_FALLBACKS` (the exhausted model is skipped until midnight Pacific). A `503`
  (model overloaded) gets at most one retry before moving to the next model in the chain, and a per-call
  timeout moves on immediately; both put the model on a short (60 s) cooldown instead of the daily block,
  all still bounded by `timeout_s * 3` for the whole `ask()`. Requests are
  counted per model per Pacific day: warning at 70 %, background callers refused at 85 %. `GEMINI_DISABLED=1`
  is the kill switch. Call counts and budget are in `/stats` under `ai`.
  `GEMINI_THINKING_LEVEL` (default `low`; `minimal|low|medium|high|off`) is sent as `thinking_config` on every
  live request to a model that accepts it; `off` sends no thinking config at all. If a model rejects the
  setting (e.g. a flash-lite model), that one call is retried once without it and the model is remembered
  as not supporting it for the rest of the process.
- `validators.py`: number / length / banned-content / JSON-shape checks applied to every AI text, and
  `AI_DRAFT_LABEL` for therapist notes.
- `eleven.py`: `await tts(text, cache_key)` → `/audio/<hash>.mp3`, generated once; `ELEVEN_DISABLED=1` or no key
  → `None` (already-recorded files are still served).
- `cue_library.py`: pre-written cues (`data/cues/cues.json`, hand-written default committed), 3 variants each,
  `pick(cue_id)` never repeats the last 2; the bridge fills `text`/`audio_url` on every `cue {id}` event
  (`enrich_cue_event`). Regenerate (costs quota, run by hand only):

  ```bash
  python -m server.ai.cue_library --generate-text    # 1 Gemini request (FAST model) → DATA_DIR/cues/cues.json
  python -m server.ai.cue_library --generate-audio   # ElevenLabs, missing MP3s only → DATA_DIR/cues/mp3/
  ```
- `session_voice.py`: `greeting{text, audio_url}` on `arrived` (prepared at session start; the mock patient
  arrives 3 s after start) and
  `summary_ready{set, spoken, therapist_note, audio_url}` after each `set_done`. Both fall back to templates.
- `prompts/*.md`: system prompts; each ends with the shared guardrail rules.

Voice by [ElevenLabs](https://elevenlabs.io) (free plan, attribution required).

## Between-Set Coach

`server/agents/session_coach.py` (`SessionCoach`) adjusts the workout, always within the active
prescription's `limits` (`min_reps`, `max_reps`, `min_rest_s`, `max_rest_s`; sane defaults `8/15/30/120`
when no database or prescription is found; limits are loaded once per session, so an approved proposal
takes effect the next time a session starts).

- **Live rules (no AI, instant):** `fatigue` ends the set early with a `fatigue` cue, but never before
  `limits.min_reps` reps are done; a heart rate over `hr_limit - 10` extends the next rest, never past
  `limits.max_rest_s`; a reported pain level of 5-6 (`command {name: pain, payload: {level, body_part?}}`)
  skips the next exercise for that body part (`payload.body_part`, else squat → knee, glute_bridge → hip)
  and raises a `kind="coach"` therapist notification. Pain ≥ 7 is left to the Safety Guardian.
- **Guardian first:** while the Guardian holds the session (open incident, `paused`, confirmed `fall`,
  pain ≥ 7) the Coach makes no adaptation, plays no cue and drops any in-flight AI result.
- **Between-set agent (Gemini FAST, during rest, ≤ 5 tool calls, 4 s timeout):** on `set_done`, a
  background task gives the model this set's stats (`get_set_stats`) and the last 3 sessions
  (`get_history`), then lets it call `adjust_next_set(reps?, rest_s?)` (clamped server-side to `limits`,
  requested vs. applied both logged) and `choose_focus_cue(cue_id)` (must already exist in
  `cues.json` and be a form/encouragement cue, played as a real `cue` event) before `finish(reason)`.
  Tool arguments are validated with Pydantic (types, ranges, no extra fields); unknown tools are refused.
  It can never change the prescription or speak unrecorded words. If Gemini is slow or disabled, or its
  answer arrives after the rest window (the next set's countdown or first rep, or `max(min_rest_s, 4 s)`
  after `set_done`), the result is dropped and the plain rules stand; the live session never waits on it.
- Every adaptation is written through `writes.insert_agent_action(agent="coach", ...)`, announced as
  `coach_adapt {action, detail}` on `/ws/events` (actions `end_set_early`, `extend_rest`,
  `adjust_next_set`, `skip_next_exercise`, `choose_focus_cue`) and, except the cue, sent to the engine
  (see "Engine reverse channel"); a shortened set or a skipped exercise also raises a
  best-effort notification through `server.notify.notifications.create` (a no-op until F7 lands).
- `GEMINI_MODE=fake` ships a default coach fake (`register_fake("coach", ...)`) that reads the set's
  stats and history and nudges the next set / picks a focus cue when a form flag was seen, so the agent
  is exercised in a plain `make run` with no live Gemini calls.

## Session end and analysis

Every session ends through **one** code path, whichever gets there first: `POST /sessions/{id}/stop` (REST;
it first sends the engine a `stop` command for a live session, so the LCD/mock program stops counting too),
the engine emitting `stopped` (its own program finished), or the last set's `set_done`
(`set == sets_target`, for engines that don't send `stopped`; disable with `AUTO_STOP_AFTER_LAST_SET=0`).
All three land on `SessionRegistry.stop()`, which is idempotent (a second stop for the same session is a
no-op, tracked in `self.ended`) and fires `on_stop` hooks once per session — sync hooks run inline, a hook
that returns a coroutine is scheduled as a task so `stop()` never waits on it, and a failing hook is logged,
never raised. `server/session_end.py`'s `SessionEnder` is the `EngineBridge` event subscriber that implements
the `stopped` / last-set rules above.

The **mock** engine (`MIRA_SOURCE=mock`) now finishes its 3-set program and emits `stopped {reason:
"program_complete"}` instead of looping forever, so the demo goes through a real session end every time.
Three env flags control it:

| Variable | Default | Effect |
|---|---|---|
| `MOCK_LOOP=1` | 0 | the mock program repeats forever (pre-Plan-2 behaviour); see Kill switches above. Implies no auto-stop/auto-restart: `SessionEnder`'s last-set check and `MockAutoRestart` are both skipped, since the mock program's own `stopped` never fires either |
| `MOCK_AUTO_RESTART_S` | `60.0` | mock mode only (and not `MOCK_LOOP`): `MockAutoRestart` starts a fresh demo session this many seconds after one ends, unless something else (REST, boot) already started one, and keeps waiting while a Safety Guardian incident is open; auto-restarted and boot sessions are analysed with the fast model, phone/dashboard sessions with the smart one; `0` disables the auto-restart so the LCD stays on "Session complete" |
| `AUTO_STOP_AFTER_LAST_SET=0` | 1 | see Kill switches above |
| `ANALYST_DELAY_S` | `2.0` | how long `SessionAnalyst` waits after a session ends before reading it, so the last reps/form_events writes have landed |

**`SessionAnalyst`** (`server/agents/session_analyst.py`) is registered as a `SessionRegistry.on_stop` hook
and runs once per ended session, after `ANALYST_DELAY_S`:

- Reads the finished session's `reps`, `form_events`, pain/effort and the last 5 same-patient-and-exercise
  sessions (`queries.previous_sessions_stats`) from the database.
- Computes the **form score deterministically** — `round(100 * mean(reps.quality))` (never from the LLM;
  `quality` is 0..1 per `persist.QUALITY_SCORES`) — along with `good_reps` (`quality >= 1.0`) and `sets_done`
  (distinct set numbers). An empty session (0 reps) under 20 s is skipped entirely (no analysis, no event:
  a false start); 0 reps at 20 s or more still gets a template analysis ("No reps were counted…").
- Asks Gemini (`analyst` agent, `models.smart()`, `json_mode=True`, 12 s timeout) for
  `{spoken, note, highlights, next_focus}` with the same `validate_text`/`validate_json` number guard and
  one retry as `SessionVoice`'s per-set summary; any validation failure (banned content, a number not in
  DATA, bad JSON) falls back to a **template** analysis and `source` becomes `"template"` instead of `"ai"`.
  Flagged moments come from `form_events` joined to the reps, labelled via `cue_library.pick(rule_id)` (falling
  back to `session_voice.FLAG_REMINDERS`, then the humanised rule id), capped at 12, most severe/earliest
  first.
- Stores one row in **`session_analyses`** (`server/db/migrations/002_session_analysis.sql`; `session_id`
  PK → `sessions(id) ON DELETE CASCADE`, `form_score`, `reps_total`, `good_reps`, `sets_done`, `spoken`,
  `note` (stored **without** the `AI_DRAFT_LABEL`, added back on read/emit), `highlights`, `next_focus`,
  `flagged_moments`, `comparison`, `audio_url`, `source`, `model`). A second analysis for the same session
  upserts the row (latest values win).
- Emits a `session_summary` event on `/ws/events` and `/device/`:
  `{session_id, t, exercise_id, exercise_name, form_score, reps_total, good_reps, sets_done, spoken, note
  (labeled "AI draft — review before use: …"), highlights: [{kind, text}], next_focus, flagged_moments:
  [{set, rep, rule_id, label, severity, t}], comparison: {prev_sessions, good_rep_rate_delta, rom_delta_deg,
  asym_delta_pct, form_score_delta}, audio_url, source: ai|template, model}`. Sample:
  `server/samples/ws_events_session_summary.json`.
- Raises a therapist notification (`kind="session_summary"`, `severity="info"`, title `"<first name> finished
  <exercise>"`, body `"Form <score>/100 · <good>/<total> good reps"`, `link="/sessions/{id}"`, deduped per
  session) through `server.notify.notifications.create`, same as the other agents.
- Never raises into its caller: every DB read is individually guarded and an unexpected error is logged and
  swallowed, so a broken analysis can never break the live stream or `stop()`.
- On shutdown, `SessionAnalyst.close()` cancels every pending analysis task: a session that ends right at
  shutdown gets no analysis (there's no live request left to serve one to, and the process is exiting).

**New/changed REST:**

- `GET /sessions/{id}` gains `"exercise_name"` (display name for `exercise_id`) and `"analysis"`
  — the `session_analyses` row (with `note` labeled) or `null` if the analyst hasn't finished yet. Poll this
  until `analysis` is non-null, or just listen for `session_summary` on `/ws/events`.
- `POST /sessions/{id}/checkin` (owner or full scope) — `{pain_post?: 0..10, effort?: 1..10, note?: ≤1000
  chars}` → `{session_id, pain_post, effort, note_saved}`. A `pain_post >= 7` raises a therapist notification
  (`kind="pain"`, `severity="high"`, title `"<first name> reported pain <n>/10 after a session"`, deduped
  per session so re-posting the same check-in is harmless) instead of opening a live Safety Guardian
  incident — the check-in arrives after the session (and usually its analysis) already ended, so there is
  no live call left for the Guardian to hold open.
- `GET /patients/{id}/sessions?limit=30&before=` (owner or full scope) → `{patient_id, sessions: [...]}`,
  newest first, ended and non-simulated sessions only, each row carrying `form_score`/`good_reps`/
  `reps_total`/`spoken_preview` (first 140 chars of `spoken`) alongside the usual session fields.

Samples for all of the above: `server/samples/ws_events_session_summary.json`,
`server/samples/rest_get_patient_sessions.json`, `server/samples/rest_post_session_checkin.json`, and the
regenerated `server/samples/rest_get_session.json` (now carries `exercise_name`/`analysis`).

**LCD:** the kiosk's "Session complete" screen (`device/kiosk/README.md`) shows while a `session_summary`
event is ≤ 60 s old and the phase is `idle`, `rest` or `done` — the form score out of 100, "N of M good
reps", and the AI `spoken` line (captioned and voiced the same way as every other kiosk line).

## Care Copilot (therapist Q&A, plan proposals)

`POST /copilot/ask {question, patient_id?}` → `{answer, charts[], sql[], evidence[], cached, proposals[], fallback}`.
The Copilot (`server/agents/copilot.py`) runs up to 5 Gemini steps (`GEMINI_MODEL_SMART` + fallbacks, 8 s each)
with five tools: `run_sql` (checked by `server/agents/sql_guard.py`: one SELECT on progress_v, sessions, reps,
form_events, memory_events, patients or prescriptions, blocked functions, LIMIT ≤ 200, then run as the read-only
`copilot_ro` user with a 3 s timeout), `get_patient_summary`, `create_plan_proposal` (a pending card only),
`draft_message` (saved to `agent_actions`, never sent) and `final_answer`. Tool results go back labelled as data,
with phone numbers removed (copilot_ro can't read the emergency contact columns at all; the guard refuses them
and `SELECT *` on patients). Trends use completed local days only, so a live session today can't reorder them. Every number in the answer must appear in a tool result; the model gets one retry,
then the answer falls back (`fallback` says why). It needs `TIGER_DSN` and `COPILOT_DSN`.

Proposals (the therapist decides): `GET /proposals?status=pending|approved|edited|dismissed|all&patient_id=`,
`POST /proposals/{id}/approve {therapist_id}`, `POST /proposals/{id}/edit {therapist_id, change}`,
`POST /proposals/{id}/dismiss {therapist_id, reason?}`. Approve/edit insert a new prescription version (the old
one stays, inactive; `created_by = proposal:<id>`; rest days and drills are kept in `targets`), set
`decided_by`/`decided_at` and log `agent_actions` in one transaction (all or nothing), then emit
`proposal_decided` on `/ws/events`. A decided proposal returns 409; only the patient's therapist may decide (403).

```bash
python -m server.agents.copilot --weekly-review [--patient maya]   # pending cards; run once before the demo (quota)
python -m server.agents.copilot --write-demo-cache                 # refresh data/copilot_demo.json (see below)
```

Stage backup: `COPILOT_CACHED_DEMO=1` answers "Which of my patients are compensating?" instantly from
`data/copilot_demo.json` (`DATA_DIR` copy wins), and the same file is the fallback if Gemini is unavailable. The
served answer's `proposals` lists the pending proposal cards for the patients it names; with none (or no database)
its "I drafted a plan proposal" sentence becomes "A plan proposal can be drafted on request." The
committed file was produced by `--write-demo-cache` with `GEMINI_MODE=fake` against a freshly simulated history
(seed 42): the real guarded SQL on copilot_ro, no hand-written numbers. Dates and numbers are relative to the day
the history was simulated, so on the day of the demo run ONE command and commit both files:

```bash
make demo-data   # fresh local history for today → server/sim/expected.json, then
                 # data/copilot_demo.json (fake model, real SQL on copilot_ro / mira-local-ro); refuses a wrong ranking
```

It rebuilds the local `mira` database (like `make db-local`). Load Tiger the same day (`make db-setup`) so the
live answer matches the cache.

Evals (not part of `make test`): 10 questions with answers from `server/sim/expected.json`.

```bash
MIRA_EVAL_RO_DSN=postgresql://copilot_ro:mira-local-ro@localhost:55432/mira \
  GEMINI_MODE=fake make evals                # after make db-local; free, must pass 10/10
MIRA_EVAL_DSN=postgresql://postgres:mira@localhost:55432/mira \
MIRA_EVAL_RO_DSN=postgresql://copilot_ro:mira-local-ro@localhost:55432/mira \
GEMINI_MODE=live make evals EVAL_ARGS="--only compensating -v"   # live: ~2-5 smart requests per question
```

## Background agents: Insight, Data Guard, milestones

With a database (`TIGER_DSN`), the lifespan starts `server/agents/scheduler.py`:

| Job | When | Reads | Writes |
|---|---|---|---|
| Data Guard `run_all()` (archive + checks 1-4) | `DATA_GUARD_DELAY_S` (300) after startup, then every `DATA_GUARD_INTERVAL_S` (900) | `copilot_ro` only (`COPILOT_DSN`; skipped without it) | `data_checks`, `daily_progress_archive` (writes.py) |
| Insight agent + milestones | `INSIGHT_DELAY_S` (120) after startup, then every `INSIGHT_INTERVAL_S` (900) | `copilot_ro` only (`COPILOT_DSN`) | `memory_events` (writes.py) |

Background agents read only through `copilot_ro` and write only through the fixed functions in
`server/db/writes.py` (a test traps the app pool during a full pass of both jobs). **Grant deviation from the team plan's
role design:** `roles.sql` also grants `copilot_ro` SELECT on `pose_frames`, `daily_progress_archive`, `data_checks` and
daily_progress's materialization hypertable (looked up in a re-runnable `DO` block), because the Data Guard's
checks 1-4 read them and the project rule is that background agents read only through the read-only role. It
still has no access to `alerts`, `agent_actions`, `ai_calls`, `notifications` or `therapists`, and the Copilot
stays confined to its seven relations by `sql_guard`'s table allow-list. `timescaledb_information.job_errors`
only shows rows to the job owner, so a failing check 1 reports `job_stats` fields and points at `job_errors`.
Owner-only maintenance (check 6 compression via `compress_history`, `safe_drop`'s `drop_chunks`, never
scheduled) still runs on the app pool.

Kill switches: `DATA_GUARD_ENABLED=0`, `INSIGHT_ENABLED=0`. A failing run is logged and counted (`/stats` →
`scheduler`) and the schedule carries on. All DB work is on the async pools (measured event-loop lag p95 ≈ 1 ms
during back-to-back runs on the 900k-frame history).

- **Insight** (`server/agents/insight.py`): five SQL rules find candidates (depth gain ≥ 10° vs 3 weeks
  ago; asymmetry rising and compensation = depth up ≥ 5° while affected-leg load falls ≥ 2 points, both as
  least-squares trends over ≥ 3 session days in the last 14 days; pain_post ≥ 5 in the last 7 days; ≥ 3
  prescribed session days missed in the last 14). Each is deduped by `<patient>:<kind>:<ISO week>`; a
  compensation candidate (or note this week) for a patient + exercise suppresses its asymmetry note. New ones are
  investigated by Gemini (smart model, background priority, ≤ 5 tool steps, read-only patient-scoped tools); the
  note is number-checked against the evidence stored with it, else the SQL-only note is written.
  Compensation and pain notes raise a `high` dashboard notification (kind `insight`).
- **Milestones:** the first day a patient's p95 knee depth reaches `prescriptions.targets.knee_deg` (squat default
  90°) in the last 7 days → notification kind `milestone`, severity `low`, deduped per patient + exercise + target.
- **Data Guard** failures (`fail`/`error`) → notification kind `data_guard`, severity `normal`, once per check per day,
  addressed to `SELECT DISTINCT therapist_id FROM patients` on `copilot_ro` (its column-level `patients` grant
  keeps `therapist_id` and leaves out the emergency contact columns).
- Notifications go through `server.notify.notifications.create` when that module is present (no-op otherwise).

```bash
curl -s -H "Authorization: Bearer $MIRA_API_TOKEN" localhost:8000/health/data   # latest result per check
curl -s -X POST -H "Authorization: Bearer $MIRA_API_TOKEN" localhost:8000/insight/run   # on-demand Insight pass
```
`/health/data` → `{status: ok | failing | unavailable, checks: [{check_name, status, details, time}], last_run,
scheduler}`.

## Safety path (Guardian, emergency calls)

The Safety Guardian (`server/agents/safety_guardian.py`) watches the event stream: a confirmed `fall`,
`command {name: "pain", payload: {level >= 7}}` or heart rate above the patient's limit for 5 s or more
opens an incident. It plays the check-in cue and haptic, then waits `CHECKIN_TIMEOUT_S` for a thumbs up,
raised hand or **I'm OK**. With no answer it builds the incident snapshot (ring buffer + cached profile,
no AI, no database) and calls the emergency contact through Retell. While the call is live, meaningful
changes (she responded, sat up, lay down again, still not moving after another 30 s, heart rate changed by more
than 15 bpm) are pushed into the call: at most one update every 10 s, never the same fact twice in a row (each
kind of fact is compared with the last value pushed, so lying → sitting → lying is said again). Every step
is an `alert` event on `/ws/events`, a JSON log line with `alert_id`/`call_id`, an `alerts` row (when a
database is configured) and a call to each `state.on_alert_step` hook.

`CALLS_DISABLED=1` (the default) runs a **log-only call simulator**: the server logs the exact
`retell_llm_dynamic_variables` and the rendered opening/voicemail it would send (phone numbers masked)
and walks `call_registered → call_ongoing → call_ended` over `SIM_CALL_DURATION_S`, so the whole ladder
can be rehearsed for free:

```bash
MIRA_SOURCE=mock:fall CHECKIN_TIMEOUT_S=15 SIM_CALL_DURATION_S=30 MOCK_FALL_RESPONDS_AT=25 make run
# logs: detected → checked_in → escalated → call_registered → call_ongoing → patient_responded
#       → call_update → call_ended → closed   (exactly one escalation)
```

The Retell agent "Mira Safety Caller" is configured from code (the team plan's call design):

```bash
python -m server.notify.retell_setup            # dry run: prints the LLM / agent / number payloads
python -m server.notify.retell_setup --apply    # create/update (by name), publish, pin the number, verify
```

`--apply` is free (it never places a call). Retell only edits an agent's latest *draft*, so `--apply`
writes the draft (opening a new one from the published version when needed), **publishes** it and binds
the number's outbound agent to that exact version number (not `"latest"`, which would be whatever draft
someone last edited in the dashboard). It prints `RETELL_AGENT_ID` and `RETELL_AGENT_VERSION`; calls pass
both as `override_agent_id` / `override_agent_version`. The LLM also has an `end_call` tool so the agent
can hang up once the contact has said what they will do.

## Apple Watch companion

Plan 4 adds a real watchOS target, `MiraWatch` (bundle `com.mira.app.watchkitapp`, embedded in the `Mira`
iPhone app as "Embed Watch Content"), alongside the design-review mock in `device/watch/` (unchanged — see
[Watch mockup](device/watch/README.md)). The watch talks to the iPhone over `WatchConnectivity`; the phone
relays everything onto the normal `/ws/events` socket, so the server never knows the difference between a
phone tap and a watch tap.

**Relay path:** watch → iPhone (`WCSession`) → `/ws/events` → server, and back: server → `/ws/events` →
iPhone → watch (`updateApplicationContext`/`sendMessage`). The shared wire codec is
`device/ios/Mira/Net/WatchWire.swift` (`WatchWire.encode`/`decodeToWatch`/`decodeToPhone`, dictionary ⇄ typed
messages, JSON-safe scalars only); the iPhone-side relay is `device/ios/Mira/Watch/WatchBridge.swift`
(`WCSessionDelegate`), driving and driven by `LiveSessionStore`. `AppState.watchPaired` (`watch.isPaired &&
watch.isWatchAppInstalled`) gates the "Apple Watch tap" toggle in Settings.

**`watch_button` mapping** (server, `server/main.py::_on_client_event`, `WATCH_BUTTON_COMMANDS`): the watch's
**Ready** tap and **Stop** (tap, then confirm — or hold for 1 s) arrive as `{"type": "watch_button", "name": "ready" | "stop"}` on
`/ws/events` and are forwarded as session commands exactly like a phone tap — `ready → command start`,
`stop → command stop`, the forwarded command carrying `payload.source = "watch"` — and go through the same `_forward_session_command`
path, so a Safety Guardian hold blocks a watch `ready` exactly like it blocks a phone `start`. The raw
`watch_button` event is also broadcast to every connected `/ws/events` client either way.

**On the watch** (`device/ios/MiraWatch/`): one screen driven by the phone's snapshot. `countdown`, `active`
and `rest` show the live rep ring (rest stays on the live screen with "Rest a moment."); `paused`/`incident`
show the paused screen (Resume is hidden during an `incident`, which reads "Mira is checking on you"
instead); the end of a session shows the done screen (reps, good-form reps and form score once the summary
arrives) until **Back to Mira**. Ready plays `.click`, Stop plays `.stop`. The `HKWorkoutSession` runs from
`countdown`/`active` until the session ends — a `done`/`idle` snapshot, the summary, or a local Stop. Because
the server clears the session right after `done` and the phone's `/ws/live` then goes quiet, the phone
pushes an idle snapshot itself when `/ws/live` reports `done` or disappears, and again with the summary;
snapshot pushes are throttled to one per 0.5 s with a trailing push, so the final state is never dropped.
The watch's `.status(workoutActive:)` is stored on the phone as `WatchBridge.workoutActive` (nothing reads
it yet). Heart rate is sent to the phone only while it is reachable.

**Haptic coaching** (`server/agents/haptics.py`, `HapticCoach`): subscribes to engine events and emits a
`haptic` event only while a session is live. `HAPTIC_PATTERNS = ("good_rep", "fix_form", "set_done",
"check_in")`:
- `good_rep` — a `rep` event with `quality == "good"`.
- `fix_form` — a `rep` event with any other quality.
- `set_done` — a `set_done` event.
- `check_in` — emitted directly by the Safety Guardian (`templates.CHECKIN_HAPTIC`), not by `HapticCoach`,
  to avoid a duplicate emit.

On the watch: `good_rep → .success`, `fix_form → .retry` played twice 150 ms apart, `set_done →
.notification`, `check_in` (and any unknown pattern) `→ .directionUp`.

`haptic` stays off the LCD kiosk's event allow-list (patients and the dashboard only).

**Heart rate** (`server/db/writes.py::insert_heart_rate`, `server/db/persist.py::SessionPersistence._on_hr`):
an `hr` event with a numeric `bpm` is now persisted to the `heart_rate` table, throttled to at most one row
per second per session (`_last_hr_at`, forgotten when the session stops).

**What works on the simulator vs. a physical watch:** pairing, Ready/Stop, haptics, live rep/set counts and
the exercise name all work on a paired Apple Watch *simulator* — nothing there depends on real hardware.
Heart rate does not: `HKWorkoutSession`/`HKLiveWorkoutBuilder` only deliver live samples from a physical
Apple Watch being worn. On the simulator the heart-rate card shows "—" with "Heart rate needs a real Apple
Watch"; on a real watch with the workout permission denied it shows "Heart rate needs permission in the
Watch app"; otherwise it streams into
`WatchSessionModel` and is relayed to the phone as `heartRate` and on to `/ws/events` as `hr`.

**Build and run:**
1. Open `device/ios/Mira.xcodeproj` (or the `device/Mira.xcworkspace` that also has the mocks) in Xcode.
2. Select the **MiraWatch** scheme for the watch app standalone, or the **Mira** scheme (which embeds
   `MiraWatch.app`) to run the iPhone app with the watch companion attached.
3. Pick an iPhone simulator and a paired Apple Watch simulator. To pair simulators: Xcode →
   **Window ▸ Devices and Simulators**, or `xcrun simctl pair <watch-udid> <phone-udid>`. The watch app's
   sources are in `device/ios/MiraWatch/`.
4. Run Tri's server (above, "Try it") and pair the phone as usual (README "Patient identity (phone)"). Once
   the phone is paired and a session is live, the watch mirrors reps/set/exercise and plays the matching
   haptic on `good_rep`/`fix_form`/`set_done`/`check_in`.

## Dashboard notifications

One backend turns alerts, call progress, Insight notes, Copilot proposals, milestones, Coach
adaptations and Data Guard failures into therapist notifications with unread state, acknowledgement,
history and real-time delivery. `server/notify/notifications.py: create(kind, severity, therapist_id,
patient_id, title, body, link, source_table, source_id, dedupe_key) -> int | None` is the **only**
producer API — every agent (and F7 itself, from the Guardian's `on_alert_step` hooks) calls it, never
the database directly. It applies the notification policy (quiet hours 9 PM–8 AM America/Chicago except
urgent, at most 1 non-urgent push per patient per local day — the row is still stored either way, only
the `push` flag on it changes), stores the row (`ON CONFLICT (dedupe_key) DO NOTHING`, so a step is
never notified twice) and publishes `{"type": "notification", ...row}` on the hub's `"notifications"`
channel. With a database, the `notifications` table is the source of truth: ids are its ids, and the
REST list/count and the WS `since_id` replay read it, so history and unread state survive a restart. If
the INSERT fails (database down, unknown therapist), the row is still delivered live with a negative,
process-local id (listed, counted and ackable, but not part of a `since_id` replay). Without a database,
an in-memory ring with its own id sequence is the store. An urgent `alert` notification that stays
unacked is re-sent on the hub every `NOTIFY_RESEND_MIN` minutes (default 5) until it, or another
notification about the same alert, is acked (call-progress rows are not re-sent).

Guardian steps become notifications: `escalated` → `kind=alert, severity=urgent`; `call_registered`,
`call_ongoing`, `call_update`, `call_not_connected`, `call_error`, `call_retry`, `call_failed`,
`call_ended`, `acknowledged` → `kind=call_update, severity=urgent`; `resolved` (false alarm) →
`kind=alert, severity=low`. Dedupe keys are `alert:<alert_id>:<step>` (one row per step), except live
call updates: `alert:<alert_id>:call_update:<sha1(text)[:10]>`, one row per distinct update, with the
(phone-masked) update text as `body`. The therapist comes from the incident's cached
`PatientProfile.therapist_id`.

REST (token required, like every other router, but **not** gated on a database):
```
GET  /notifications?unread=true&limit=50&since_id=&therapist_id=   # panel list (newest first), or with
                                                                    # since_id the oldest `limit` rows after
                                                                    # it, ascending (polling fallback); limit 1-500
GET  /notifications/count?therapist_id=                             # bell badge {unread, urgent_unacked}
POST /notifications/{id}/read
POST /notifications/read-all {therapist_id?}
POST /notifications/{id}/ack {therapist_id}   # acking an alert/call_update notification pushes
                                               # "Her therapist <name> has seen the alert." into the
                                               # open (escalated) live call via notifier.push_update,
                                               # once per incident
```
Samples: `server/samples/rest_get_notifications.json`, `rest_get_notifications_count.json`,
`rest_post_notification_read.json`, `rest_post_notifications_read_all.json`,
`rest_post_notification_ack.json`.

WebSocket `/ws/notifications?token=…&since_id=<last id>&therapist_id=<optional>`: on connect the server
replays every row after `since_id` (the newest 1000 at most), with no gap and no duplicate, then
streams new rows live as `{"type": "notification", ...row}`; `therapist_id` scopes both the replay and
the live stream (default: all). Without `since_id` there is no replay (load the panel with REST)
(`server/samples/ws_notifications_message.json`). A reconnecting dashboard never misses one. Fallback
when the socket is down: poll `GET /notifications?since_id=` every 10 s.

## Retell live calls: BLOCKED (no spend approved) — exact manual steps to enable

**What is blocked:** placing a real phone call. Every real call spends the existing Retell credit balance
(about $0.07–0.15 per minute), and the owner has not approved spending it. So `CALLS_DISABLED=1` stays on and the
log-only simulator stands in for the call; no code change is needed to switch.

**Already done (free, no call placed):** the "Mira Safety Caller" agent and its LLM are configured from code
(`retell_setup --apply`), published as **version 1**, and the Retell number's outbound agent is bound to that
version. The code paths for a live call (create, poll, live updates, post-call analysis) are covered by tests
with a fake client.

**To enable, in order:**

1. Get the owner's approval to spend Retell credits; check the balance in the Retell dashboard (Billing).
2. In `.env`: `RETELL_API_KEY`, `RETELL_FROM_NUMBER` (E.164, the bought number), `RETELL_AGENT_ID=agent_...`
   (printed by `retell_setup --apply`) and `RETELL_AGENT_VERSION=1`. If you ever re-run `--apply`, copy the newly
   printed version instead.
3. Set `DEMO_EMERGENCY_PHONE` to a real phone you own, in E.164 (e.g. `+17135550123`, not `713-555-0123`).
   Optionally `DEMO_THERAPIST_PHONE` (no-answer / voicemail fallback call) and `DEMO_PATIENT_PHONE`. Save the
   Retell number as a contact "Mira Alert" on those phones so it isn't shown as spam.
4. Set `CALLS_DISABLED=0` (and `CHECKIN_TIMEOUT_S=15` for a shorter wait).
5. Start a staged fall, either `MIRA_SOURCE=mock:fall make run` (falls 20 s after start), or `make run` with
   `DEMO_TOOLS_ENABLED=1` and then
   `curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' localhost:8000/demo/trigger_fall`.
   The startup log shows `CALLS_DISABLED: false`, and `/stats` → `notifier.mode` must be `live`
   (`log_only` means a Retell value is missing). Don't respond on the mat until the phone rings.
6. Work through the test-call checklist:
   - [ ] The phone rings within ~10 s of escalation, and caller ID shows "Mira Alert" (not "Spam Likely").
   - [ ] The opening starts ~1 s after you answer and isn't cut off.
   - [ ] **No raw `{{…}}` is spoken** (a variable name doesn't match or wasn't sent).
   - [ ] The situation takes about 15–20 s (3–4 sentences).
   - [ ] Numbers and times sound natural ("one hundred twelve", "three forty-two PM").
   - [ ] Ask "Is she moving now?": it answers from the current status, or says Mira can't tell.
   - [ ] Give a thumbs up on the mat mid-call (or `MOCK_FALL_RESPONDS_AT`): you hear "Update: …" within seconds.
   - [ ] Say "Yes, I'll go check on her": after hang-up the `alerts` rows show `acknowledged`.
   - [ ] Let it go to voicemail once: it leaves the fixed message; `disconnection_reason = voicemail_reached`.
   - [ ] Only **one** call per incident, even if the fall event fires twice.
   - [ ] Print one real call once, to confirm the SDK attribute names the poller reads:
     ```bash
     .venv/bin/python -c "from retell import Retell; from server.config import cfg; c=Retell(api_key=cfg.RETELL_API_KEY).call.retrieve('<call_id>'); print(c.call_status, c.disconnection_reason, getattr(c.call_analysis,'custom_analysis_data',None))"
     ```
7. Set `CALLS_DISABLED=1` again afterwards.

**Verify** (with a database): the incident's steps, in order, from the `alerts` table:

```sql
SELECT time, step, channel, payload
FROM alerts
WHERE alert_id = (SELECT alert_id FROM alerts ORDER BY time DESC LIMIT 1)
ORDER BY time;
-- expect: detected → checked_in → escalated (payload.retell.contact_call_id = the call id) → call_registered
--         → call_ongoing → call_update … → acknowledged → call_ended → closed
```

Without a database the same steps are JSON log lines in `logs/server.log` (search for the `alert_id`).

Optional webhook: Retell dashboard → Settings → webhook URL `https://<tunnel>/retell/webhook` and
`PUBLIC_WEBHOOK_HOST=<tunnel host>`. Polling stays the source of truth; webhook events only add
`webhook_<event>` steps.
