# Mira

Open `device/Mira.xcworkspace` in Xcode to see the iPhone and Watch projects
together. Choose **Mira** for the patient app, **MiraWatchPreview** for the
interactive Watch mockup on iPhone, or **MiraWatchMock** for watchOS.

iOS networking setup and tests: [Networking guide](device/ios/NETWORKING.md).

Onboarding flow, remaining integrations, and checks: [Onboarding guide](device/ios/ONBOARDING.md).

Interactive Watch concept and preview instructions: [Watch mockup](device/watch/README.md).

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
