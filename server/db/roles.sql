-- Read-only Copilot role (TRI.md §9.3). Run as the service owner, statement by statement:
--   COPILOT_RO_PASSWORD=... python -m server.db.apply_sql server/db/roles.sql "$DSN"
-- The password is never stored in git: {{copilot_ro_password}} is substituted at apply time from
-- COPILOT_RO_PASSWORD (or the password in COPILOT_DSN). Re-runnable: the role may already exist.
-- The app itself connects as the service owner (tsdbadmin on Tiger, postgres locally).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_ro') THEN
    CREATE ROLE copilot_ro LOGIN;
  END IF;
END
$$;
ALTER ROLE copilot_ro WITH LOGIN PASSWORD {{copilot_ro_password}};
GRANT USAGE ON SCHEMA public TO copilot_ro;
GRANT SELECT ON progress_v, sessions, reps, form_events, memory_events,
                patients, prescriptions TO copilot_ro;           -- NOT alerts, agent_actions, ai_calls, pose_frames
ALTER ROLE copilot_ro SET statement_timeout = '3s';
ALTER ROLE copilot_ro SET default_transaction_read_only = on;    -- belt and braces
ALTER ROLE copilot_ro SET idle_in_transaction_session_timeout = '10s';
-- If you also grant daily_progress directly and see "permission denied", grant SELECT on its
-- materialization hypertable too (name from timescaledb_information.continuous_aggregates).
-- Reading through progress_v avoids this, because views run with the owner's privileges.
