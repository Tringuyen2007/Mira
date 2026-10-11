# Sample payloads

One JSON file per WebSocket message (`ws_*`), engine frame (`engine_frame.json`) and REST response (`rest_*`).
They are produced by the mock source and the running server, and `server/tests/test_samples.py` checks their shape.

Joint angles (`angles.knee_l`, `knee_r`, `hip_l`, `hip_r`, `/ws/live` `main_angle`, `reps.peak_angle`, the knee curve
in `rest_get_session_frames_*`) are **flexion in degrees**: 0 = straight, larger = deeper bend. Standing is about 5,
the bottom of a squat about 70-90. The vision engine must send the same convention (see the README "Frame contract").

Copilot and proposals: `rest_post_copilot_ask` (chart points trimmed), `rest_post_copilot_review`, `rest_get_proposals` and
`rest_post_proposal_approve` (evidence SQL trimmed), `rest_error_409_proposal`, `ws_events_proposal_created`,
`ws_events_proposal_decided`, all from the running server in `GEMINI_MODE=fake` on the simulated history.
