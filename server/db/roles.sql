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
                prescriptions TO copilot_ro;
-- NOT alerts, agent_actions, ai_calls, notifications, therapists.
-- patients column by column: the emergency contact (name, phone) never reaches the Copilot or Gemini. The
-- Data Guard finds notification recipients through patients.therapist_id, which stays granted.
REVOKE SELECT ON patients FROM copilot_ro;
GRANT SELECT (id, name, age, height_cm, mass_kg, affected_side, condition, therapist_id, hr_limit, simulated,
              created_at) ON patients TO copilot_ro;
-- Deviation from §9.3 (project goal: background agents READ only through copilot_ro): the Data Guard's checks
-- 1-4 also read raw frames, the materialized rollup, the archive and earlier check results. The Copilot stays
-- confined to the list above by sql_guard's table allow-list; these grants are for the fixed Data Guard SQL.
GRANT SELECT ON pose_frames, daily_progress_archive, data_checks TO copilot_ro;
-- daily_progress's materialization hypertable (its name differs per database). Grants on a hypertable
-- propagate to its chunks. timescaledb_information.jobs / job_stats / continuous_aggregates are readable by
-- everyone; job_errors only shows rows to the job owner, so check 1 reports job_stats fields instead.
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT materialization_hypertable_schema AS s, materialization_hypertable_name AS n
           FROM timescaledb_information.continuous_aggregates WHERE view_name = 'daily_progress' LOOP
    EXECUTE format('GRANT SELECT ON %I.%I TO copilot_ro', r.s, r.n);
  END LOOP;
END
$$;
ALTER ROLE copilot_ro SET statement_timeout = '3s';
ALTER ROLE copilot_ro SET default_transaction_read_only = on;    -- belt and braces
ALTER ROLE copilot_ro SET idle_in_transaction_session_timeout = '10s';
