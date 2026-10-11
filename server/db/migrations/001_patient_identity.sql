-- Plan 1: patient identity. Every statement is idempotent (applied by server/db/migrate.py on every start).
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

ALTER TABLE patients ADD COLUMN IF NOT EXISTS emergency_contact_relationship text;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS alert_therapist boolean NOT NULL DEFAULT true;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS next_visit_at timestamptz;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS share_by_default boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS device_prefs jsonb NOT NULL DEFAULT '{}';
ALTER TABLE therapists ADD COLUMN IF NOT EXISTS clinic text;

INSERT INTO schema_migrations (name) VALUES ('001_patient_identity.sql') ON CONFLICT (name) DO NOTHING;
