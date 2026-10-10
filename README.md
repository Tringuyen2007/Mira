# Mira server

FastAPI server for the Mira physical-therapy device: WebSocket streams for the device and dashboard,
the engine bridge (mock / replay / live vision), and the REST API.

## Setup

```bash
python3.12 -m venv .venv            # Python 3.12 (MediaPipe wheels)
.venv/bin/pip install -r server/requirements.txt
cp .env.example .env                 # then set MIRA_API_TOKEN to a long random string
```

Generate a token with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
The server starts without any external keys and without a database (`MIRA_SOURCE=mock` is the default).

## Database (Tiger Data / TimescaleDB)

The server runs without a database; set `TIGER_DSN` to persist frames, sessions, reps and alerts.
Every setup step is a script (TRI.md §5.11), so a database is rebuilt, never restored from a dump.

```bash
# Local fallback: start (or reuse) the mira-db Docker container on :55432 and rebuild the `mira` database
COPILOT_RO_PASSWORD=... make db-local          # schema → rollup → roles → seed → 900k simulated frames
                                               # → refresh → Data Guard check 3 → compress → policy → jobs
# Tiger Cloud (or any DSN): same steps, no drop
make db-setup DSN="postgres://tsdbadmin:...@<host>:<port>/tsdb?sslmode=require"
```

Then point the server at it, e.g. `TIGER_DSN=postgresql://postgres:mira@localhost:55432/mira` and
`COPILOT_DSN=postgresql://copilot_ro:<COPILOT_RO_PASSWORD>@localhost:55432/mira`.
`make db-local` takes ~30 s and prints a step log (rows, wall time, compression ratio, jobs).
`make db-setup` is idempotent: steps already done are skipped. Use `make ... VENV=/path/to/venv` as needed.

Individual pieces: `python -m server.db.apply_sql <file.sql> <dsn>` (one statement per round trip, autocommit),
`python -m server.db.seed <dsn>`, `python -m server.sim.simulate_history --dsn <dsn> --seed 42`,
`python -m server.db.compress_history <dsn>`. The simulator writes the Copilot eval answers to
`server/sim/expected.json`.

DB tests use the local container (`MIRA_TEST_ADMIN_DSN`, default `postgresql://postgres:mira@localhost:55432/postgres`),
create a throwaway database each and skip when it is unreachable. They never touch Tiger Cloud.

## Run

```bash
make run      # uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1
make test
```

Always exactly **one** server process (`--workers 1`): live state is in memory.
Use `make run VENV=/path/to/venv` if your virtual environment lives elsewhere.

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
```

Rehearse an emergency: `MIRA_SOURCE=mock:fall` (fall after 20 s, `fall_status` every 2 s,
optional thumbs up `MOCK_FALL_RESPONDS_AT` seconds after the fall), or with `DEMO_TOOLS_ENABLED=1`:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"responds_at_s": 45}' http://localhost:8000/demo/trigger_fall
```

Example payloads for every WebSocket message and REST response are in `server/samples/`.

## Frame contract: joint angles

`angles.knee_l`, `knee_r`, `hip_l` and `hip_r` in every engine frame (TRI.md §4.5) are **flexion in degrees**:
0 = straight joint, larger = deeper bend (flexion = 180 minus the interior angle between the two bones).
A standing person reads about 5, the bottom of a squat about 70-90, and a "90 degree squat target" means 90 degrees of
knee flexion. The database rollup (`max_knee`, `p95_knee` = deepest bend), the simulator, `/ws/live`
`main_angle` (the deeper knee) and the mock source all use this convention, so the vision engine must emit it too.
See `server/samples/engine_frame.json`.
Logs are JSON lines in `logs/server.log` (phone numbers masked, secrets redacted).

## AI layer (Gemini + ElevenLabs)

All AI lives in `server/ai/`; nothing else imports the Google SDK.

- `gemini.py`: `await ask(agent, model, system, messages, tools=None, timeout_s=4.0, background=False,
  json_mode=False)` returns a `Reply` or raises `AIUnavailable` (the caller then uses its fallback).
  `GEMINI_MODE=fake` (default) uses fakes registered per agent with `register_fake(agent, fn)`; `replay` serves
  saved answers from `data/ai_cache/` (falls back to the fake); `live` calls Gemini and caches every answer
  (a caller that rejects an answer calls `await gemini.discard(reply)` so it is not replayed).
  Retries 429/5xx after 1 s and 3 s, honours retry hints, and on a daily-quota 429 for `GEMINI_MODEL_SMART`
  moves through `GEMINI_SMART_FALLBACKS` (the exhausted model is skipped until midnight Pacific). Requests are
  counted per model per Pacific day: warning at 70 %, background callers refused at 85 %. `GEMINI_DISABLED=1`
  is the kill switch. Call counts and budget are in `/stats` under `ai`.
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

## Care Copilot (therapist Q&A, plan proposals)

`POST /copilot/ask {question, patient_id?}` → `{answer, charts[], sql[], evidence[], cached, proposals[], fallback}`.
The Copilot (`server/agents/copilot.py`) runs up to 5 Gemini steps (`GEMINI_MODEL_SMART` + fallbacks, 8 s each)
with five tools: `run_sql` (checked by `server/agents/sql_guard.py`: one SELECT on progress_v, sessions, reps,
form_events, memory_events, patients or prescriptions, blocked functions, LIMIT ≤ 200, then run as the read-only
`copilot_ro` user with a 3 s timeout), `get_patient_summary`, `create_plan_proposal` (a pending card only),
`draft_message` (saved to `agent_actions`, never sent) and `final_answer`. Tool results go back labelled as data,
with phone numbers removed. Every number in the answer must appear in a tool result; the model gets one retry,
then the answer falls back (`fallback` says why). It needs `TIGER_DSN` and `COPILOT_DSN`.

Proposals (the therapist decides): `GET /proposals?status=pending|approved|edited|dismissed|all&patient_id=`,
`POST /proposals/{id}/approve {therapist_id}`, `POST /proposals/{id}/edit {therapist_id, change}`,
`POST /proposals/{id}/dismiss {therapist_id, reason?}`. Approve/edit insert a new prescription version (the old
one stays, inactive; `created_by = proposal:<id>`; rest days and drills are kept in `targets`), set
`decided_by`/`decided_at`, log `agent_actions` and emit `proposal_decided` on `/ws/events`. A decided proposal
returns 409.

```bash
python -m server.agents.copilot --weekly-review [--patient maya]   # pending cards; run once before the demo (quota)
python -m server.agents.copilot --write-demo-cache                 # refresh data/copilot_demo.json (see below)
```

Stage backup: `COPILOT_CACHED_DEMO=1` answers "Which of my patients are compensating?" instantly from
`data/copilot_demo.json` (`DATA_DIR` copy wins), and the same file is the fallback if Gemini is unavailable. The
committed file was produced by `--write-demo-cache` with `GEMINI_MODE=fake` against a freshly simulated history
(seed 42): the real guarded SQL on copilot_ro, no hand-written numbers. Regenerate it on the day the history is
loaded (dates and numbers are relative to that day); with `GEMINI_MODE=live` it spends ~5 smart-model requests.

Evals (not part of `make test`): 10 questions with answers from `server/sim/expected.json`.

```bash
make evals                                   # GEMINI_MODE from env/.env; fake mode is free and must pass 10/10
MIRA_EVAL_DSN=postgresql://postgres:mira@localhost:55432/mira \
MIRA_EVAL_RO_DSN=postgresql://copilot_ro:<password>@localhost:55432/mira \
GEMINI_MODE=live make evals EVAL_ARGS="--only compensating -v"   # live: ~2-5 smart requests per question
```

## Safety path (Guardian, emergency calls)

The Safety Guardian (`server/agents/safety_guardian.py`) watches the event stream: a confirmed `fall`,
`command {name: "pain", payload: {level >= 7}}` or heart rate above the patient's limit for 5 s or more
opens an incident. It plays the check-in cue and haptic, then waits `CHECKIN_TIMEOUT_S` for a thumbs up,
raised hand or **I'm OK**. With no answer it builds the incident snapshot (ring buffer + cached profile,
no AI, no database) and calls the emergency contact through Retell. While the call is live, meaningful
changes (she responded, sat up, still not moving after another 30 s, heart rate changed by more than
15 bpm) are pushed into the call: at most one update every 10 s, never the same fact twice. Every step
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

The Retell agent "Mira Safety Caller" is configured from code, exactly per TRI.md §8.3:

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

## Retell live calls: BLOCKED (no spend approved) — exact manual steps to enable

Live calls cost Retell credits (about $0.07–0.15 per minute), so they are off. The agent is already
configured (`--apply` above); nothing else in the code changes for live calls. To enable them:

1. Get approval to spend Retell credits, then check the balance in the Retell dashboard (Billing).
2. In `.env` set `RETELL_API_KEY`, `RETELL_FROM_NUMBER` (E.164, the number bought in Retell) and run
   `python -m server.notify.retell_setup --apply`; it must end with `24/24 fields verified`.
3. Put the printed values in `.env` as `RETELL_AGENT_ID=agent_...` and `RETELL_AGENT_VERSION=<n>`
   (re-copy the version after every `--apply`).
4. Set `DEMO_EMERGENCY_PHONE` (and `DEMO_THERAPIST_PHONE` for the voicemail/no-answer fallback call) to
   phones you own, in E.164. Save the Retell number as a contact "Mira Alert" on those phones. Set
   `DEMO_PATIENT_PHONE` (E.164) so the voicemail and prompt give Maya's number; without it they say
   "the number you have for her".
5. Set `CALLS_DISABLED=0` and start the server. The startup log line shows `CALLS_DISABLED: false`;
   `/stats` → `notifier.mode` must be `live` (it stays `log_only` if any of the three Retell values is missing).
6. Trigger one staged fall (`MIRA_SOURCE=mock:fall`, or `DEMO_TOOLS_ENABLED=1` + `POST /demo/trigger_fall`)
   and run the TRI.md §8.3.1 test-call checklist. Print one real `client.call.retrieve(call_id)` to confirm
   the SDK attribute names (`call_status`, `call_analysis.custom_analysis_data`, `disconnection_reason`).
7. Set `CALLS_DISABLED=1` again after the rehearsal.

Optional webhook: Retell dashboard → Settings → webhook URL `https://<tunnel>/retell/webhook` and
`PUBLIC_WEBHOOK_HOST=<tunnel host>`. Polling stays the source of truth; webhook events only add
`webhook_<event>` steps.
