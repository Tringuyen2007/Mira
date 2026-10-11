-- Mira schema (TRI.md §5.5 + notifications §8.7.1). Apply with: python -m server.db.apply_sql server/db/schema.sql "$DSN"
-- Tiger Cloud services have TimescaleDB enabled. Toolkit hyperfunctions are included on Tiger Cloud;
-- if a function is missing, run the second line.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

CREATE TABLE therapists (
  id    text PRIMARY KEY,
  name  text NOT NULL,
  phone text,                                 -- E.164, e.g. +17135550100
  clinic text
);

CREATE TABLE patients (
  id                               text PRIMARY KEY,
  name                             text NOT NULL,
  age                              int,
  height_cm                        real,
  mass_kg                          real,
  affected_side                    text CHECK (affected_side IN ('left','right','none')),
  condition                        text,
  therapist_id                     text REFERENCES therapists(id),
  emergency_contact_name           text,
  emergency_contact_phone          text,
  emergency_contact_relationship   text,
  alert_therapist                  boolean NOT NULL DEFAULT true,
  next_visit_at                    timestamptz,
  share_by_default                 boolean NOT NULL DEFAULT false,
  device_prefs                     jsonb NOT NULL DEFAULT '{}',
  hr_limit                         int DEFAULT 130,
  simulated                        boolean NOT NULL DEFAULT false,
  created_at                       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prescriptions (               -- versioned: never UPDATE a row, insert a new version
  id             bigserial PRIMARY KEY,
  patient_id     text NOT NULL REFERENCES patients(id),
  exercise_id    text NOT NULL,
  version        int  NOT NULL,
  sets           int  NOT NULL,
  reps           int  NOT NULL,
  targets        jsonb NOT NULL DEFAULT '{}',   -- e.g. {"knee_deg": 90}
  limits         jsonb NOT NULL DEFAULT '{}',   -- e.g. {"min_reps": 8, "max_rest_s": 120}
  rule_overrides jsonb NOT NULL DEFAULT '{}',
  active         boolean NOT NULL DEFAULT true,
  created_by     text NOT NULL,                 -- therapist id or 'proposal:<id>'
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (patient_id, exercise_id, version)
);

CREATE TABLE calibrations (
  id          bigserial PRIMARY KEY,
  patient_id  text REFERENCES patients(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  extrinsics  jsonb,
  errors      jsonb
);

CREATE TABLE sessions (                     -- note: started_at/ended_at, because "end" is a reserved word
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id  text NOT NULL REFERENCES patients(id),
  exercise_id text NOT NULL,
  started_at  timestamptz NOT NULL,
  ended_at    timestamptz,
  reps_done   int,
  pain_pre    smallint,
  pain_post   smallint,
  effort      smallint,
  note        text,
  shared      boolean NOT NULL DEFAULT false,
  ai_summary  text,
  ai_note     text,                         -- always shown as "AI draft — review before use"
  simulated   boolean NOT NULL DEFAULT false
);

CREATE TABLE reps (
  session_id  uuid NOT NULL REFERENCES sessions(id),
  rep         int  NOT NULL,
  set_no      smallint,
  started_at  timestamptz,
  ended_at    timestamptz,
  peak_angle  real, rom real, hold_s real,
  asym_avg    real, asym_max real,
  smoothness  real, quality real,
  flags       text[],
  PRIMARY KEY (session_id, rep)
);

CREATE TABLE pose_frames (
  time         timestamptz NOT NULL,
  patient_id   text NOT NULL,
  session_id   uuid NOT NULL,
  exercise_id  text NOT NULL,
  set_no       smallint,
  knee_l real, knee_r real, hip_l real, hip_r real,
  trunk_lean real, trunk_side real, pelvis_rot real, knee_ratio real,
  com_x real, com_y real, com_z real,
  load_l_pct real,                          -- % of body weight on the LEFT leg (50 = even)
  heel_l_pct real, heel_r_pct real,
  confidence real,
  landmarks real[]                          -- 33 joints × 3 = 99 values (may be NULL for most simulated rows)
);

CREATE TABLE heart_rate   (time timestamptz NOT NULL, patient_id text, session_id uuid, bpm smallint);
CREATE TABLE wrist_motion (time timestamptz NOT NULL, session_id uuid,
                           quat_w real, quat_x real, quat_y real, quat_z real, rotation_deg real);
CREATE TABLE form_events  (time timestamptz NOT NULL, patient_id text, session_id uuid,
                           rep int, rule_id text, severity text, duration_s real);
CREATE TABLE alerts       (time timestamptz NOT NULL, alert_id uuid NOT NULL, patient_id text,
                           session_id uuid, kind text, step text, channel text, payload jsonb);

CREATE TABLE memory_events (
  id           bigserial PRIMARY KEY,
  patient_id   text NOT NULL REFERENCES patients(id),
  time         timestamptz NOT NULL DEFAULT now(),
  kind         text NOT NULL,               -- depth_gain | asymmetry_worse | pain | missed_days | compensation
  message      text NOT NULL,
  evidence     jsonb NOT NULL,
  dedupe_key   text UNIQUE,                 -- e.g. 'maya:depth_gain:2026-W41' so it isn't re-written every 15 min
  agent        text NOT NULL DEFAULT 'insight',
  acknowledged boolean NOT NULL DEFAULT false
);

CREATE TABLE plan_proposals (
  id          bigserial PRIMARY KEY,
  patient_id  text NOT NULL REFERENCES patients(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  agent       text NOT NULL,
  rationale   text NOT NULL,
  evidence    jsonb NOT NULL,
  change      jsonb NOT NULL,               -- e.g. {"exercise_id":"squat","rest_days_between":1,"add":"weight_shift_drill"}
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','approved','edited','dismissed')),
  decided_by  text,
  decided_at  timestamptz
);

CREATE TABLE notifications (
  id           bigserial PRIMARY KEY,
  time         timestamptz NOT NULL DEFAULT now(),
  therapist_id text NOT NULL REFERENCES therapists(id),
  patient_id   text REFERENCES patients(id),
  kind         text NOT NULL,     -- alert | call_update | insight | proposal | milestone | coach | data_guard
  severity     text NOT NULL CHECK (severity IN ('urgent','high','normal','low')),
  title        text NOT NULL,
  body         text,
  link         text,              -- e.g. /dashboard#/replay/<session>?t=<ts>, /dashboard#/proposals/<id>
  source_table text, source_id text,
  dedupe_key   text UNIQUE,       -- e.g. 'alert:<alert_id>:escalated' → never notify twice
  read_at      timestamptz,
  acked_at     timestamptz,
  acked_by     text
);
CREATE INDEX ON notifications (therapist_id, id DESC);
CREATE INDEX ON notifications (therapist_id) WHERE read_at IS NULL;

CREATE TABLE agent_actions (time timestamptz NOT NULL DEFAULT now(), agent text, action text,
                            patient_id text, session_id uuid, detail jsonb);
CREATE TABLE ai_calls      (time timestamptz NOT NULL DEFAULT now(), agent text, model text,
                            ms int, ok boolean, cached boolean, error text);
CREATE TABLE data_checks   (time timestamptz NOT NULL DEFAULT now(), check_name text,
                            status text, details jsonb);

-- Hypertables (new by_range syntax). Create them BEFORE inserting data.
SELECT create_hypertable('pose_frames',  by_range('time', INTERVAL '1 day'));
SELECT create_hypertable('form_events',  by_range('time', INTERVAL '7 days'));
SELECT create_hypertable('alerts',       by_range('time', INTERVAL '7 days'));
SELECT create_hypertable('heart_rate',   by_range('time', INTERVAL '1 day'));
SELECT create_hypertable('wrist_motion', by_range('time', INTERVAL '1 day'));

CREATE INDEX ON pose_frames (session_id, time DESC);
CREATE INDEX ON pose_frames (patient_id, time DESC);
CREATE INDEX ON form_events (patient_id, time DESC);
CREATE INDEX ON alerts (alert_id, time DESC);

-- Columnstore settings (2.18+ names). This only sets HOW chunks compress; it doesn't compress anything yet.
ALTER TABLE pose_frames SET (
  timescaledb.enable_columnstore,
  timescaledb.segmentby = 'patient_id, session_id',
  timescaledb.orderby   = 'time'
);

-- Plan 1 (2026-10-11): patient identity. Mirrors db/migrations/001_patient_identity.sql.
CREATE TABLE IF NOT EXISTS schema_migrations (
  name        text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pairing_codes (
  code          text PRIMARY KEY,                       -- 6 chars A-Z0-9 (no 0/O/1/I), upper case
  patient_id    text NOT NULL REFERENCES patients(id),
  therapist_id  text REFERENCES therapists(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz,                            -- NULL = never (demo code)
  used_at       timestamptz,
  reusable      boolean NOT NULL DEFAULT false          -- the demo code can pair many phones
);

CREATE TABLE IF NOT EXISTS patient_devices (
  token_hash    text PRIMARY KEY,                       -- sha256 hex of the bearer token (never the token)
  patient_id    text NOT NULL REFERENCES patients(id),
  device_name   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen     timestamptz,
  revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS patient_devices_patient_idx ON patient_devices (patient_id);
-- Final review C1: phones paired with a reusable (demo) code. Mirrors db/migrations/004_patient_devices_reusable.sql.
ALTER TABLE patient_devices ADD COLUMN IF NOT EXISTS via_reusable boolean NOT NULL DEFAULT false;

-- Plan 2 (2026-10-11): whole-session analysis. Mirrors db/migrations/002_session_analysis.sql.
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

-- Plan 3 (2026-10-11): two-way messages. Mirrors db/migrations/003_messages.sql.
CREATE TABLE IF NOT EXISTS messages (
  id          bigserial PRIMARY KEY,
  patient_id  text NOT NULL REFERENCES patients(id),
  sender      text NOT NULL CHECK (sender IN ('patient','therapist','mira')),
  kind        text NOT NULL DEFAULT 'message' CHECK (kind IN ('message','note','visit_highlight')),
  body        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);
CREATE INDEX IF NOT EXISTS messages_patient_id_idx ON messages (patient_id, id);

-- tri-watch (2026-10-11): Apple Watch. Mirrors db/migrations/005_watch.sql (grants are in roles.sql).
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS patient_id text;
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS accel real[];
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tremor_monitoring boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS hr_limit_set_by text;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS watch_clock_offset_ms int;
CREATE TABLE IF NOT EXISTS hr_recovery (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  set_index   int  NOT NULL,
  peak_bpm    smallint NOT NULL,
  bpm_60s     smallint NOT NULL,
  drop_bpm    smallint NOT NULL,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, set_index)
);
CREATE INDEX IF NOT EXISTS hr_recovery_patient_idx ON hr_recovery (patient_id, time DESC);
CREATE TABLE IF NOT EXISTS rotation_sets (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  exercise_id text NOT NULL,
  set_index   int  NOT NULL,
  angles      real[] NOT NULL,
  best_deg    real,
  avg_deg     real,
  goal_deg    real,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, exercise_id, set_index)
);
CREATE INDEX IF NOT EXISTS rotation_sets_patient_idx ON rotation_sets (patient_id, time DESC);
CREATE TABLE IF NOT EXISTS tremor_daily (
  patient_id  text NOT NULL REFERENCES patients(id),
  day         date NOT NULL,
  none_min    int NOT NULL DEFAULT 0,
  slight_min  int NOT NULL DEFAULT 0,
  mild_min    int NOT NULL DEFAULT 0,
  strong_min  int NOT NULL DEFAULT 0,
  unknown_min int NOT NULL DEFAULT 0,
  dyskinesia  text CHECK (dyskinesia IN ('none','few','some','many')),
  source      text NOT NULL CHECK (source IN ('device','simulated')),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (patient_id, day)
);
