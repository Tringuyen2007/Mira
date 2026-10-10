# Mira

Initial Setup

mira/
  vision/        
  server/        
    main.py            FastAPI app, startup/shutdown, CORS
    hub.py             WebSocket hub (channels: /ws/pose, /ws/live, /ws/events)
    engine_bridge.py   runs Engine (or replay) in a thread, fans out frames/events
    db/                schema.sql, writer.py (batch inserter), queries.py
    api/               sessions.py, patients.py, calibration.py, proposals.py, copilot.py, alerts.py
    ai/                gemini.py, eleven.py, prompts/, cue_library.py
    agents/            copilot.py, session_coach.py, safety_guardian.py, memory_job.py
    notify/            twilio_client.py, templates.py, policy.py
    sim/               simulate_history.py
  data/cues/           cues.json + mp3/
  device/ dashboard/ ios/ 
