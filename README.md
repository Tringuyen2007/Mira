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
