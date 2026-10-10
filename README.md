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

## AI layer (Gemini + ElevenLabs)

All AI lives in `server/ai/`; nothing else imports the Google SDK.

- `gemini.py`: `await ask(agent, model, system, messages, tools=None, timeout_s=4.0, background=False,
  json_mode=False)` returns a `Reply` or raises `AIUnavailable` (the caller then uses its fallback).
  `GEMINI_MODE=fake` (default) uses fakes registered per agent with `register_fake(agent, fn)`; `replay` serves
  saved answers from `data/ai_cache/` (falls back to the fake); `live` calls Gemini and caches every answer.
  Retries 429/5xx after 1 s and 3 s, honours retry hints, and on a daily-quota 429 for `GEMINI_MODEL_SMART`
  moves through `GEMINI_SMART_FALLBACKS` (the exhausted model is skipped until midnight Pacific). Requests are
  counted per model per Pacific day: warning at 70 %, background callers refused at 85 %. `GEMINI_DISABLED=1`
  is the kill switch. Call counts and budget are in `/stats` under `ai`.
- `validators.py`: number / length / banned-content / JSON-shape checks applied to every AI text, and
  `AI_DRAFT_LABEL` for therapist notes.
- `eleven.py`: `await tts(text, cache_key)` → `/audio/<hash>.mp3`, generated once; `ELEVEN_DISABLED=1` or no key
  → `None` (already-recorded files are still served).
- `cue_library.py`: pre-written cues (`data/cues/cues.json`, hand-written default committed), 3 variants each,
  `pick(cue_id)` never repeats the last 2. Regenerate (costs quota, run by hand only):

  ```bash
  python -m server.ai.cue_library --generate-text    # 1 Gemini request (FAST model) → DATA_DIR/cues/cues.json
  python -m server.ai.cue_library --generate-audio   # ElevenLabs, missing MP3s only → DATA_DIR/cues/mp3/
  ```
- `session_voice.py`: `greeting{text, audio_url}` on `arrived` (prepared at session start) and
  `summary_ready{set, spoken, therapist_note, audio_url}` after each `set_done`. Both fall back to templates.
- `prompts/*.md`: system prompts; each ends with the shared guardrail rules.

Voice by [ElevenLabs](https://elevenlabs.io) (free plan, attribution required).
