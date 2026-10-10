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
Logs are JSON lines in `logs/server.log` (phone numbers masked, secrets redacted).

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
MIRA_SOURCE=mock:fall CHECKIN_TIMEOUT_S=15 SIM_CALL_DURATION_S=30 MOCK_FALL_RESPONDS_AT=45 make run
# logs: detected → checked_in → escalated → call_registered → call_ongoing → patient_responded
#       → call_update → call_ended → closed   (exactly one escalation)
```

The Retell agent "Mira Safety Caller" is configured from code, exactly per TRI.md §8.3:

```bash
python -m server.notify.retell_setup            # dry run: prints the LLM / agent / number payloads
python -m server.notify.retell_setup --apply    # create or update (by name), bind the number, verify
```

`--apply` is free (it never places a call) and prints the agent id for `RETELL_AGENT_ID`.

## Retell live calls: BLOCKED (no spend approved) — exact manual steps to enable

Live calls cost Retell credits (about $0.07–0.15 per minute), so they are off. The agent is already
configured (`--apply` above); nothing else in the code changes for live calls. To enable them:

1. Get approval to spend Retell credits, then check the balance in the Retell dashboard (Billing).
2. In `.env` set `RETELL_API_KEY`, `RETELL_FROM_NUMBER` (E.164, the number bought in Retell) and run
   `python -m server.notify.retell_setup --apply`; it must end with `21/21 fields verified`.
3. Put the printed id in `.env` as `RETELL_AGENT_ID=agent_...`.
4. Set `DEMO_EMERGENCY_PHONE` (and `DEMO_THERAPIST_PHONE` for the voicemail/no-answer fallback call) to
   phones you own, in E.164. Save the Retell number as a contact "Mira Alert" on those phones.
5. Set `CALLS_DISABLED=0` and start the server. The startup log line shows `CALLS_DISABLED: false`;
   `/stats` → `notifier.mode` must be `live` (it stays `log_only` if any of the three Retell values is missing).
6. Trigger one staged fall (`MIRA_SOURCE=mock:fall`, or `DEMO_TOOLS_ENABLED=1` + `POST /demo/trigger_fall`)
   and run the TRI.md §8.3.1 test-call checklist. Print one real `client.call.retrieve(call_id)` to confirm
   the SDK attribute names (`call_status`, `call_analysis.custom_analysis_data`, `disconnection_reason`).
7. Set `CALLS_DISABLED=1` again after the rehearsal.

Optional webhook: Retell dashboard → Settings → webhook URL `https://<tunnel>/retell/webhook` and
`PUBLIC_WEBHOOK_HOST=<tunnel host>`. Polling stays the source of truth; webhook events only add
`webhook_<event>` steps.
