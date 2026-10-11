"""SQL for the therapist dashboard's `GET /caseload` and `GET /patients/{id}/memory` (dashboard spec
2026-10-11, Task 8). Kept separate from server/db/queries.py per the spec's "Server additions" so the
`wire-tri` merge (another branch editing server/db/queries.py) stays clean.

Every function takes `db` first and raises DBError on failure (server.db.pool.Database)."""

from __future__ import annotations

from typing import Any

from server.db.pool import Database
from server.db.queries import CLOSED_ALERT_STEPS, TZ

CASELOAD_LIMIT = 500
CASELOAD_TIMEOUT_S = 3.0
MEMORY_DAYS_FOR_SIGNALS = 21  # the needs_you/watch memory rules only look at unacknowledged events this recent
MEMORY_EVENTS_LIMIT = 100

# One statement: patients of a therapist + the signals `dashboard_triage.triage()` needs for each one.
#   pts        patients of the given therapist
#   last2      their last two FINISHED sessions (rn=1 latest, rn=2 the one before), for the pain/flags rules
#   lastinfo   rn=1 row: last_session_id, last_session_at, last_pain_post
#   previnfo   rn=2 row: prev_pain_post
#   first_sess first ever session start (week number falls back to created_at when there is none)
#   eff        the newest 3 non-null efforts, newest first (array_agg honours the ORDER BY)
#   reps2      reps belonging to last2's two sessions, tagged with rn
#   flag_cnt   reps/flagged-reps counts per (patient, rn)
#   top_flag   the most common flag in the last session (ties broken arbitrarily by count DESC)
#   al         the latest OPEN alert per patient: alert_id groups with no row whose step closes it
#   mem        unacknowledged memory_events from the last 21 days, newest first, as a jsonb array
#   missed     days since the last session, capped at 7, but only counted (else 0) when an active prescription
#              targets >= 5 sessions/week (otherwise "missed" doesn't mean much for a 1x/week plan)
CASELOAD_SQL = f"""
WITH pts AS (
  SELECT id, name, age, condition, affected_side, simulated, created_at
  FROM patients WHERE therapist_id = %(therapist_id)s
), last2 AS (
  SELECT s.id, s.patient_id, s.started_at, s.pain_post,
         row_number() OVER (PARTITION BY s.patient_id ORDER BY s.started_at DESC) AS rn
  FROM sessions s JOIN pts p ON p.id = s.patient_id
  WHERE s.ended_at IS NOT NULL
), lastinfo AS (
  SELECT patient_id, id AS last_session_id, started_at AS last_session_at, pain_post AS last_pain_post
  FROM last2 WHERE rn = 1
), previnfo AS (
  SELECT patient_id, pain_post AS prev_pain_post FROM last2 WHERE rn = 2
), first_sess AS (
  SELECT s.patient_id, min(s.started_at) AS first_session_at
  FROM sessions s JOIN pts p ON p.id = s.patient_id
  GROUP BY s.patient_id
), eff_ranked AS (
  SELECT s.patient_id, s.effort,
         row_number() OVER (PARTITION BY s.patient_id ORDER BY s.started_at DESC) AS rn
  FROM sessions s JOIN pts p ON p.id = s.patient_id
  WHERE s.effort IS NOT NULL AND s.ended_at IS NOT NULL
), eff AS (
  SELECT patient_id, array_agg(effort ORDER BY rn) AS last_effort
  FROM eff_ranked WHERE rn <= 3 GROUP BY patient_id
), reps2 AS (
  SELECT l.patient_id, l.rn, r.flags
  FROM last2 l JOIN reps r ON r.session_id = l.id
  WHERE l.rn <= 2
), flag_cnt AS (
  SELECT patient_id, rn, count(*) AS reps_n,
         count(*) FILTER (WHERE flags IS NOT NULL AND array_length(flags, 1) > 0) AS flagged_n
  FROM reps2 GROUP BY patient_id, rn
), fl AS (
  SELECT c1.patient_id, c1.reps_n AS reps_last, c1.flagged_n AS flagged_reps_last,
         COALESCE(c2.flagged_n, 0) AS flagged_reps_prev
  FROM flag_cnt c1 LEFT JOIN flag_cnt c2 ON c2.patient_id = c1.patient_id AND c2.rn = 2
  WHERE c1.rn = 1
), flag_votes AS (
  SELECT patient_id, flag, count(*) AS n
  FROM (SELECT patient_id, unnest(flags) AS flag FROM reps2 WHERE rn = 1 AND flags IS NOT NULL) u
  GROUP BY patient_id, flag
), top_flag AS (
  SELECT DISTINCT ON (patient_id) patient_id, flag AS top_flag_last
  FROM flag_votes ORDER BY patient_id, n DESC
), al AS (
  SELECT DISTINCT ON (a.patient_id) a.patient_id, a.kind AS open_alert_kind
  FROM alerts a JOIN pts p ON p.id = a.patient_id
  WHERE a.alert_id NOT IN (SELECT alert_id FROM alerts WHERE step = ANY(%(closed_steps)s))
  ORDER BY a.patient_id, a.time DESC
), mem AS (
  SELECT m.patient_id,
         jsonb_agg(jsonb_build_object('kind', m.kind, 'message', m.message, 'evidence', m.evidence,
                                       'time', m.time) ORDER BY m.time DESC) AS memory
  FROM memory_events m JOIN pts p ON p.id = m.patient_id
  WHERE NOT m.acknowledged AND m.time >= now() - make_interval(days => %(mem_days)s)
  GROUP BY m.patient_id
), missed AS (
  SELECT p.id AS patient_id,
         CASE WHEN EXISTS (
                SELECT 1 FROM prescriptions pr WHERE pr.patient_id = p.id AND pr.active
                  AND (pr.targets->>'sessions_per_week')::numeric >= 5)
              THEN LEAST(7, COALESCE(extract(day FROM now() - fs.last_session_at)::int, 7))
              ELSE 0 END AS missed_sessions
  FROM pts p
  LEFT JOIN (SELECT patient_id, max(started_at) AS last_session_at FROM sessions GROUP BY patient_id) fs
    ON fs.patient_id = p.id
)
SELECT p.id, p.name, p.age, p.condition, p.affected_side, p.simulated, p.created_at,
       coalesce(fsess.first_session_at, p.created_at) AS first_session_at,
       li.last_session_id, li.last_session_at, li.last_pain_post, pi.prev_pain_post,
       coalesce(eff.last_effort, ARRAY[]::smallint[]) AS last_effort,
       coalesce(fl.flagged_reps_last, 0) AS flagged_reps_last,
       coalesce(fl.flagged_reps_prev, 0) AS flagged_reps_prev,
       coalesce(fl.reps_last, 0) AS reps_last,
       tf.top_flag_last, al.open_alert_kind,
       coalesce(mem.memory, '[]'::jsonb) AS memory,
       coalesce(ms.missed_sessions, 0) AS missed_sessions
FROM pts p
LEFT JOIN lastinfo li ON li.patient_id = p.id
LEFT JOIN previnfo pi ON pi.patient_id = p.id
LEFT JOIN first_sess fsess ON fsess.patient_id = p.id
LEFT JOIN eff ON eff.patient_id = p.id
LEFT JOIN fl ON fl.patient_id = p.id
LEFT JOIN top_flag tf ON tf.patient_id = p.id
LEFT JOIN al ON al.patient_id = p.id
LEFT JOIN mem ON mem.patient_id = p.id
LEFT JOIN missed ms ON ms.patient_id = p.id
ORDER BY p.name
LIMIT {CASELOAD_LIMIT}
"""

SESSIONS_TODAY_SQL = f"""
SELECT count(*) AS n FROM sessions
WHERE patient_id = ANY(%(patient_ids)s)
  AND started_at >= time_bucket(INTERVAL '1 day', now(), '{TZ}')
"""

MEMORY_EVENTS_SQL = """
SELECT id, time, kind, message, evidence, acknowledged
FROM memory_events
WHERE patient_id = %(patient_id)s AND time >= now() - make_interval(days => %(days)s)
ORDER BY time DESC
LIMIT %(limit)s
"""


async def caseload_signals(db: Database, therapist_id: str) -> list[dict[str, Any]]:
    """One row per patient of `therapist_id`, with everything `dashboard_triage.Signals` needs. Bounded to
    CASELOAD_LIMIT patients; CASELOAD_TIMEOUT_S for the whole statement."""
    return await db.fetch(CASELOAD_SQL, {"therapist_id": therapist_id, "closed_steps": list(CLOSED_ALERT_STEPS),
                                         "mem_days": MEMORY_DAYS_FOR_SIGNALS},
                         timeout_s=CASELOAD_TIMEOUT_S)


async def sessions_done_today(db: Database, patient_ids: list[str]) -> int:
    """Sessions those patients started since local midnight (server.db.queries.TZ)."""
    if not patient_ids:
        return 0
    row = await db.fetchrow(SESSIONS_TODAY_SQL, {"patient_ids": patient_ids})
    return int(row["n"]) if row else 0


async def memory_events(db: Database, patient_id: str, days: int) -> list[dict[str, Any]]:
    """The patient's Therapist Memory list: memory_events from the last `days` days, newest first."""
    return await db.fetch(MEMORY_EVENTS_SQL, {"patient_id": patient_id, "days": days, "limit": MEMORY_EVENTS_LIMIT})
