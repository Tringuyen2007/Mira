-- Final review C1: remember which phones paired with a reusable (demo) code, so they can be locked down on a public
-- server and so the device cap only ever evicts demo phones. Idempotent (applied by server/db/migrate.py every start).
ALTER TABLE patient_devices ADD COLUMN IF NOT EXISTS via_reusable boolean NOT NULL DEFAULT false;

INSERT INTO schema_migrations (name) VALUES ('004_patient_devices_reusable.sql') ON CONFLICT (name) DO NOTHING;
