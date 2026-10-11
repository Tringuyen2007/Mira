-- Plan 2: whole-session analysis. Every statement is idempotent (applied by server/db/migrate.py on every start).
CREATE TABLE IF NOT EXISTS session_analyses (
  session_id       uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  created_at       timestamptz NOT NULL DEFAULT now(),
  form_score       smallint,            -- 0..100, computed, not from the LLM
  reps_total       int, good_reps int, sets_done int,
  spoken           text NOT NULL,       -- 2-3 sentences for the patient (voiced)
  note             text NOT NULL,       -- therapist draft, stored WITHOUT the label; label added on read
  highlights       jsonb NOT NULL,      -- [{"kind":"rom|balance|pain|streak|consistency","text":..., "delta":...}]
  next_focus       text,
  flagged_moments  jsonb NOT NULL,      -- [{"set","rep","rule_id","label","severity","t"}]
  comparison       jsonb NOT NULL,      -- {"prev_sessions":n,"rom_delta_deg":..,"asym_delta_pct":..,
                                       --  "good_rep_rate_delta":..}
  audio_url        text,
  source           text NOT NULL,       -- ai | template
  model            text
);

INSERT INTO schema_migrations (name) VALUES ('002_session_analysis.sql') ON CONFLICT (name) DO NOTHING;
