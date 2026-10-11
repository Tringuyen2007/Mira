# Sample payloads

One JSON file per WebSocket message (`ws_*`), engine frame (`engine_frame.json`) and REST response (`rest_*`).
They are produced by the mock source and the running server, and `server/tests/test_samples.py` checks their shape.

Joint angles (`angles.knee_l`, `knee_r`, `hip_l`, `hip_r`, `/ws/live` `main_angle`, `reps.peak_angle`, the knee curve
in `rest_get_session_frames_*`) are **flexion in degrees**: 0 = straight, larger = deeper bend. Standing is about 5,
the bottom of a squat about 70-90. The vision engine must send the same convention (see the README "Frame contract").

Copilot and proposals: `rest_post_copilot_ask` (chart points trimmed), `rest_get_proposals` and
`rest_post_proposal_approve` (evidence SQL trimmed), `rest_error_409_proposal`, `ws_events_proposal_created`,
`ws_events_proposal_decided`, all from the running server in `GEMINI_MODE=fake` on the simulated history.

`rest_post_pair` is the `POST /pair` response for the demo code `K7P4M2`; the `token` field is redacted to a
fixed placeholder since a real device token is a secret.

`rest_get_me` is the `GET /me?patient_id=maya` response (admin token), matching the `me` object nested in
`rest_post_pair`: patient profile, therapist, prescriptions, LCD status and calibration.

Plan 2 (session end, §5.4/§5.5): `ws_events_session_summary` is the `SessionAnalyst`'s whole-session event,
captured from a mock session stopped via `POST /sessions/{id}/stop`. `rest_get_session` is regenerated from
that same finished session, so its `analysis` field (the stored `session_analyses` row, note labeled on read)
and `exercise_name` are populated instead of null. `rest_post_session_checkin` is the
`POST /sessions/{id}/checkin` response for a moderate pain/effort/note check-in. `rest_get_patient_sessions`
is `GET /patients/maya/sessions`, newest-first ended sessions with each one's `form_score` from the analysis.

Plan 3 (care, messaging, milestones): `rest_get_me_care` is `GET /me/care` after a therapist note and an
unread therapist message exist. `rest_get_me_messages` is `GET /me/messages` right after that message was
read (so `read_at` is set). `rest_post_me_messages` is the response to the patient's own
`POST /me/messages`. `rest_get_patient_milestones` is `GET /patients/maya/milestones` with one
`memory_events` row of `kind='milestone'` inserted directly. `ws_events_message` is the `message` event a
therapist's `POST /patients/{id}/messages` emits on the patient's `/ws/events` socket.
