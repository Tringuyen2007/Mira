-- Apple Watch (tri-watch). Every statement is idempotent (applied by server/db/migrate.py on every start).
-- wrist_motion stays a hypertable; it gains the patient and the raw user acceleration.
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS patient_id text;
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS accel real[];
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tremor_monitoring boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS hr_limit_set_by text;      -- therapist id (no FK: dashboard contract)
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS watch_clock_offset_ms int;  -- watch clock minus server clock

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
  angles      real[] NOT NULL,                 -- one per counted rep, degrees measured by the watch
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

-- copilot_ro (roles.sql) may not exist yet on a fresh setup: roles.sql grants these too.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_ro') THEN
    GRANT SELECT ON hr_recovery, rotation_sets, tremor_daily TO copilot_ro;
  END IF;
END
$$;

INSERT INTO schema_migrations (name) VALUES ('005_watch.sql') ON CONFLICT (name) DO NOTHING;
