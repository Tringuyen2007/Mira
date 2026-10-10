-- Daily rollup (TRI.md §5.6). Run ONE statement at a time with autocommit ON:
--   python -m server.db.apply_sql server/db/aggregates.sql "$DSN"

CREATE MATERIALIZED VIEW daily_progress
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS   -- false = real-time; default is TRUE now
SELECT
  patient_id,
  exercise_id,
  time_bucket(INTERVAL '1 day', time, 'America/Chicago') AS day,   -- local days (Houston), not UTC
  max(greatest(knee_l, knee_r))              AS max_knee,          -- deepest bend (sensitive to glitches)
  percentile_agg(greatest(knee_l, knee_r))   AS knee_dist,         -- sketch → p95 depth (robust to glitches)
  avg(load_l_pct)                            AS avg_load_left,     -- SIGNED: < 50 means more weight on the right
  time_weight('Linear', time, load_l_pct)    AS tw_load_left,      -- time-weighted summary (fair with uneven frames)
  avg(abs(load_l_pct - 50))                  AS avg_asymmetry,     -- unevenness, either side
  avg(trunk_lean)                            AS avg_trunk_lean,    -- leaning = another compensation sign
  avg(confidence)                            AS avg_confidence,    -- data quality
  count(*)                                   AS frames             -- volume (count sessions from `sessions`)
FROM pose_frames
WHERE confidence >= 0.6                                            -- keep glitchy frames out of the summary
GROUP BY patient_id, exercise_id, day
WITH NO DATA;                                                      -- fill it explicitly (§5.11)

-- Refresh the last 30 days every 15 minutes; leave the newest hour to real-time mode.
SELECT add_continuous_aggregate_policy('daily_progress',
  start_offset      => INTERVAL '30 days',
  end_offset        => INTERVAL '1 hour',
  schedule_interval => INTERVAL '15 minutes');

-- Friendly view: applies the Toolkit accessors and computes load on the AFFECTED leg.
-- Dashboard, Insight agent and Copilot read THIS, not daily_progress directly.
CREATE VIEW progress_v AS
SELECT d.patient_id, d.exercise_id, d.day,
       d.max_knee,
       approx_percentile(0.95, d.knee_dist)             AS p95_knee,
       d.avg_load_left,
       average(d.tw_load_left)                          AS tw_load_left,
       CASE p.affected_side
         WHEN 'right' THEN 100 - average(d.tw_load_left)
         WHEN 'left'  THEN average(d.tw_load_left)
         ELSE NULL                                       -- 'none': no affected leg, so no compensation metric
       END                                              AS affected_load_pct,  -- Maya: 48 → 36 = compensating
       d.avg_asymmetry, d.avg_trunk_lean, d.avg_confidence, d.frames
FROM daily_progress d
JOIN patients p ON p.id = d.patient_id;

-- Data Guard archive (§5.10): frozen copy of finished days, plain numbers that are never recalculated.
-- Created here because it is shaped like progress_v; the Data Guard fills it.
CREATE TABLE IF NOT EXISTS daily_progress_archive AS SELECT * FROM progress_v WITH NO DATA;
ALTER TABLE daily_progress_archive ADD PRIMARY KEY (patient_id, exercise_id, day);
