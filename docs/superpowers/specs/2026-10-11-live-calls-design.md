# Live emergency calls by default — design

Date: 2026-10-11. Branch `calls` from `main` (986bf24).

## Goal

A confirmed fall from Eric's remote vision agent, with no response from the patient within the check-in window,
places a real Retell call to the emergency contact on every server Mira runs on. Nobody starts a special server or
flips a flag per run. Today this already works end to end (verified live 2026-10-11: fall → 15 s check-in → Retell
call → live update → hang-up → incident closed), but only when `CALLS_DISABLED=0`, and every running server and
the committed `.env` guidance keep it at 1.

## What already exists (unchanged)

- `/ws/vision` (MIRA_VISION_TOKEN) → `EngineBridge.ingest_remote` → `_on_engine_msg` → `SafetyGuardian.on_event`.
  A remote agent's events take the same path as the mock source.
- `SafetyGuardian`: `fall`/`confirmed` opens an incident, plays the check-in cue, waits `CHECKIN_TIMEOUT_S`
  (15 s in `.env`, 30 s code default) for `gesture thumbs_up|raised_hand` or `command im_ok`, then
  `RetellNotifier.escalate`. One incident per session; duplicate fall events are ignored.
- `RetellNotifier`: `live` when `CALLS_DISABLED=0` and `RETELL_API_KEY`, `RETELL_FROM_NUMBER`, `RETELL_AGENT_ID`
  are all set, else `log_only` simulator. Retries, backup agent, call watch, live updates, voicemail → therapist.

Nothing in the check-in timing, the Guardian's rules, or the Retell agent changes.

## Changes

### 1. Calls on where Mira runs; off in code and tests

- `.env`: `CALLS_DISABLED=0`, `ALLOW_TEST_CALLS=1` (keep `CHECKIN_TIMEOUT_S=15`).
- `.env.example`: same values, comments rewritten: live calls are the normal state, `CALLS_DISABLED=1` is the
  opt-in simulator for development without Retell credit.
- `server/config.py` defaults stay `CALLS_DISABLED=True`, `ALLOW_TEST_CALLS=False`; `server/tests/conftest.py`
  keeps `CALLS_DISABLED=1`. A fresh checkout or the test suite can never dial.
- `README.md`: the section "Retell live calls: BLOCKED (no spend approved)" becomes "Retell live calls: ON".
  It states the flags, the cost, how to turn the simulator back on, and keeps the test-call checklist as the
  periodic verification. The flags table rows for `CALLS_DISABLED` / `ALLOW_TEST_CALLS` say "on in `.env`".
- Vultr: the box's own `.env` is not in the repo and is not touched by this branch. The README gets the exact
  one-liner the operator runs (set the two flags, restart, check `/health`).

### 2. Fall event contract for the remote vision agent

New section "Falls and safety events" in `docs/vision-media-contract.md`, the file Eric already follows. It
documents, with one JSON example each, exactly what the server reads today (no new fields invented):

| message | required | optional | server action |
|---|---|---|---|
| `{"type":"fall","stage":"confirmed","t":<epoch s>,"session_id":...}` | `type`, `stage`, `t` | `drop_m`, `peak_down_speed_mps`, `posture` (`standing`/`sitting`/`lying`/`unknown`), `on_mat` | opens the incident, check-in cue, starts the timer. Stages other than `confirmed` are ignored. |
| `{"type":"fall_status","posture":...,"moving":bool,"still_s":float,"on_mat":bool,"t":...}` every ~2 s while down | `type` | the rest | status for the call narration ("still not moving", posture changes). |
| `{"type":"gesture","name":"thumbs_up"\|"raised_hand","t":...}` | `type`, `name` | | patient responded: closes the check-in (no call) or, mid-call, pushes an update. |
| `{"type":"hr","bpm":int,"t":...}` | `type`, `bpm` | | heart rate before/after for the narration (optional; the watch also sends it). |

Plus: `session_id` must match the session the server started with `command start`; events before that are
dropped by the privacy gate; `t` is epoch seconds within an hour of server time; what the agent receives back
(`incident_closed`, `command`); and a one-paragraph timeline of what happens after `fall` (15 s, call, one call
per incident, voicemail → therapist).

### 3. Proof: remote fall → Retell call (test)

`server/tests/test_remote_fall_calls.py`: `make_client(MIRA_SOURCE="remote", MIRA_VISION_TOKEN=…,
CALLS_DISABLED="0", RETELL_API_KEY=…, RETELL_FROM_NUMBER=…, RETELL_AGENT_ID=…, CHECKIN_TIMEOUT_S="0.2")`, swap
`get_state().notifier._client_obj` for a `FakeCalls` scripted `registered → ongoing → ended`, start a session,
send `command start`, send the contract's `fall` event over `/ws/vision`, then assert within a few seconds that
`FakeCalls.created` has exactly one call to `DEMO_EMERGENCY_PHONE` with `patient_first_name == "Maya"`, and the
alert steps include `escalated`. A second test sends `fall` then `gesture thumbs_up` inside the window and
asserts no call was created and the step is `resolved`. A third sends two `fall` events and asserts one call.

### 4. Visibility: `/health` reports the call mode

`GET /health` → `{"status":"ok","time":…,"calls":"live"|"log_only"}` (unauthenticated: the value is not
secret, it is one word). `tools/deploy_vultr.sh`: after the restart loop, read `/health` and exit non-zero with
a clear message if `calls` is not `live`, unless the invoker passes `ALLOW_LOG_ONLY=1`. The remote `.env` is
never printed.

### 5. The phone's test-alert button rings

`RetellNotifier.test_call(to_number: str) -> str | None`: places one call with the normal create path
(`_create_call` with `alert_id="test-<hour bucket>"`, `role="test"`, so the idempotency key dedupes repeats
within the hour), `retell_llm_dynamic_variables` containing the fixed test opening ("This is a test of Mira's
alert system for Maya. No action is needed."), no watch, no live updates. In `log_only` mode it logs the same
warning the simulator uses and returns a `sim_test_…` id. `POST /me/test-alert` keeps its existing gate
(`CALLS_DISABLED=0 and ALLOW_TEST_CALLS=1 and not locked_demo and contact phone`). A phone paired with the reusable demo
code may dial unless the server sets `PAIRING_REUSABLE_UPDATES_PROFILE=0` (`locked_demo`), which the public box
should once the demo phone has paired; the ledger also allows one test call per contact per hour.
Tests: `test_retell_client.py` covers both modes; `test_care_api.py` adds the `called: true` case with a fake
client, and keeps the disabled case.

`CallLedger` policy must accept role `test`; check `server/notify/policy.py` and add it if the role list is
closed.

## Error handling

- Calls on but Retell incomplete: notifier is `log_only`, startup logs the existing error, `/health` shows
  `log_only`, the deploy script fails. No silent regression.
- `test_call` failure: caught, logged, endpoint returns `called: false` with note "Call could not be placed".
- Remote agent sends `fall` with no session or wrong `session_id`: dropped by `ingest_remote` (existing).

## Out of scope

Check-in length, therapist auto-call rules, Retell agent prompt/version, an adapter for a different event shape,
editing the Vultr `.env` from this branch.

## Operator steps after merge

```bash
ssh -i ~/.ssh/mira_vultr root@45.76.255.253 '
  cd /opt/mira/app
  for kv in CALLS_DISABLED=0 ALLOW_TEST_CALLS=1; do
    k=${kv%%=*}; grep -q "^$k=" .env && sed -i "s/^$k=.*/$kv/" .env || echo "$kv" >> .env
  done
  systemctl restart mira'
tools/deploy_vultr.sh   # fails if /health says calls != live
```
