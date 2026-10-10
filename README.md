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
