-- Plan 3: two-way messages (therapist notes, visit highlights, patient/therapist/mira chat). Every statement
-- is idempotent (applied by server/db/migrate.py on every start).
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

INSERT INTO schema_migrations (name) VALUES ('003_messages.sql') ON CONFLICT (name) DO NOTHING;
