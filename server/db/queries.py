"""All read queries: hyperfunctions (§5.7), progress series with a time_bucket fallback (§5.6), REST reads and
the /stats queries (§5.13). Every function takes `db` first and raises DBError on failure."""

from __future__ import annotations

from typing import Any

from server.db.pool import Database, DBError
from server.logging_setup import get_logger

log = get_logger("db.queries")

TZ = "America/Chicago"  # local days (Houston) everywhere: rollup, Data Guard, charts
CLOSED_ALERT_STEPS = ("resolved", "closed", "resolved_by_therapist")
MAX_FULL_RATE_FRAMES = 36_000  # 20 min at 30 Hz

PROGRESS_COLUMNS = ("patient_id, exercise_id, day, max_knee, p95_knee, avg_load_left, tw_load_left, "
                    "affected_load_pct, avg_asymmetry, avg_trunk_lean, avg_confidence, frames")

PROGRESS_V_SQL = f"""
SELECT {PROGRESS_COLUMNS}
FROM progress_v
WHERE patient_id = %(pid)s AND (%(ex)s::text IS NULL OR exercise_id = %(ex)s)
  AND day >= time_bucket(INTERVAL '1 day', now() - make_interval(days => %(days)s), '{TZ}')
ORDER BY exercise_id, day"""

# Same columns as progress_v, computed live from pose_frames (slower, identical shape).
PROGRESS_FALLBACK_SQL = f"""
WITH d AS (
  SELECT f.patient_id, f.exercise_id,
         time_bucket(INTERVAL '1 day', f.time, '{TZ}')      AS day,
         max(greatest(f.knee_l, f.knee_r))                  AS max_knee,
         percentile_agg(greatest(f.knee_l, f.knee_r))       AS knee_dist,
         avg(f.load_l_pct)                                  AS avg_load_left,
         time_weight('Linear', f.time, f.load_l_pct)        AS tw_load_left,
         avg(abs(f.load_l_pct - 50))                        AS avg_asymmetry,
         avg(f.trunk_lean)                                  AS avg_trunk_lean,
         avg(f.confidence)                                  AS avg_confidence,
         count(*)                                           AS frames
  FROM pose_frames f
  WHERE f.confidence >= 0.6 AND f.patient_id = %(pid)s AND (%(ex)s::text IS NULL OR f.exercise_id = %(ex)s)
    AND f.time >= time_bucket(INTERVAL '1 day', now() - make_interval(days => %(days)s), '{TZ}')
  GROUP BY 1, 2, 3)
SELECT d.patient_id, d.exercise_id, d.day, d.max_knee,
       approx_percentile(0.95, d.knee_dist) AS p95_knee,
       d.avg_load_left,
       average(d.tw_load_left) AS tw_load_left,
       CASE p.affected_side WHEN 'right' THEN 100 - average(d.tw_load_left)
                            WHEN 'left' THEN average(d.tw_load_left) ELSE NULL END AS affected_load_pct,
       d.avg_asymmetry, d.avg_trunk_lean, d.avg_confidence, d.frames
FROM d JOIN patients p ON p.id = d.patient_id
ORDER BY d.exercise_id, d.day"""


# ---- progress ----------------------------------------------------------------------------------------------

async def progress_series(db: Database, patient_id: str, exercise_id: str | None = None,
                          days: int = 90) -> tuple[str, list[dict[str, Any]]]:
    """Daily series from progress_v; falls back to the live time_bucket query if the rollup is broken.
    Returns (source, rows) with source "progress_v" or "fallback"."""
    params = {"pid": patient_id, "ex": exercise_id, "days": days}
    try:
        return "progress_v", await db.fetch(PROGRESS_V_SQL, params)
    except DBError as exc:
        log.warning("progress_v failed, using time_bucket fallback", extra={"error": str(exc)})
    return "fallback", await db.fetch(PROGRESS_FALLBACK_SQL, params, timeout_s=20)


# ---- sessions (§5.7) ---------------------------------------------------------------------------------------

async def get_session(db: Database, session_id: str) -> dict[str, Any] | None:
    return await db.fetchrow("SELECT * FROM sessions WHERE id = %s", (session_id,))


async def session_reps(db: Database, session_id: str) -> list[dict[str, Any]]:
    return await db.fetch("SELECT * FROM reps WHERE session_id = %s ORDER BY rep", (session_id,))


async def session_form_events(db: Database, session_id: str) -> list[dict[str, Any]]:
    return await db.fetch("SELECT time, rep, rule_id, severity, duration_s FROM form_events "
                          "WHERE session_id = %s ORDER BY time", (session_id,))


async def session_tw_load(db: Database, session_id: str) -> float | None:
    row = await db.fetchrow("SELECT average(time_weight('Linear', time, load_l_pct)) AS tw_load_left_pct "
                            "FROM pose_frames WHERE session_id = %s", (session_id,))
    return row["tw_load_left_pct"] if row else None


async def session_tw_load_per_set(db: Database, session_id: str) -> list[dict[str, Any]]:
    return await db.fetch("SELECT set_no, average(time_weight('Linear', time, load_l_pct)) AS tw_load_left_pct "
                          "FROM pose_frames WHERE session_id = %s GROUP BY set_no ORDER BY set_no", (session_id,))


async def session_knee_lttb(db: Database, session_id: str, points: int = 300) -> list[dict[str, Any]]:
    """The knee curve downsampled with lttb (it returns a timevector, hence unnest)."""
    return await db.fetch("SELECT time, value FROM unnest((SELECT lttb(time, knee_r, %s) FROM pose_frames "
                          "WHERE session_id = %s AND knee_r IS NOT NULL))", (points, session_id))


async def session_frames(db: Database, session_id: str, t0: Any = None, t1: Any = None) -> list[dict[str, Any]]:
    """Full-rate frames for the 3D replay; landmarks are reshaped to joints[33][3] (None when not stored)."""
    rows = await db.fetch(
        "SELECT time, landmarks, load_l_pct, knee_l, knee_r, set_no FROM pose_frames "
        "WHERE session_id = %s AND (%s::timestamptz IS NULL OR time >= %s) "
        "AND (%s::timestamptz IS NULL OR time <= %s) ORDER BY time LIMIT %s",
        (session_id, t0, t0, t1, t1, MAX_FULL_RATE_FRAMES))
    for row in rows:
        lm = row.pop("landmarks")
        row["joints"] = [lm[i:i + 3] for i in range(0, len(lm), 3)] if lm else None
    return rows


async def session_hr_tw(db: Database, session_id: str) -> float | None:
    row = await db.fetchrow("SELECT average(time_weight('Linear', time, bpm)) AS bpm FROM heart_rate "
                            "WHERE session_id = %s", (session_id,))
    return row["bpm"] if row else None


# ---- patients & prescriptions ----------------------------------------------------------------------------

async def list_patients(db: Database) -> list[dict[str, Any]]:
    return await db.fetch("""
        SELECT p.id, p.name, p.age, p.affected_side, p.condition, p.therapist_id, p.simulated,
               s.last_session_at, COALESCE(s.sessions_7d, 0) AS sessions_7d
        FROM patients p
        LEFT JOIN LATERAL (
          SELECT max(started_at) AS last_session_at,
                 count(*) FILTER (WHERE started_at >= now() - INTERVAL '7 days') AS sessions_7d
          FROM sessions WHERE patient_id = p.id) s ON true
        ORDER BY p.name""")


async def get_patient(db: Database, patient_id: str) -> dict[str, Any] | None:
    return await db.fetchrow("""
        SELECT p.*, t.name AS therapist_name
        FROM patients p LEFT JOIN therapists t ON t.id = p.therapist_id
        WHERE p.id = %s""", (patient_id,))


async def recent_sessions(db: Database, patient_id: str, limit: int = 10) -> list[dict[str, Any]]:
    return await db.fetch("SELECT id, exercise_id, started_at, ended_at, reps_done, pain_pre, pain_post, effort, "
                          "shared, simulated FROM sessions WHERE patient_id = %s ORDER BY started_at DESC LIMIT %s",
                          (patient_id, limit))


async def profile_row(db: Database, patient_id: str, timeout_s: float = 0.9) -> dict[str, Any] | None:
    """Everything the cached PatientProfile needs, in one round trip."""
    return await db.fetchrow(f"""
        SELECT p.id, p.name, p.age, p.condition, p.affected_side, p.hr_limit,
               p.emergency_contact_name, p.emergency_contact_phone,
               t.id AS therapist_id, t.name AS therapist_name, t.phone AS therapist_phone,
               (SELECT s.pain_pre FROM sessions s WHERE s.patient_id = p.id AND s.pain_pre IS NOT NULL
                  AND s.started_at >= time_bucket(INTERVAL '1 day', now(), '{TZ}')
                ORDER BY s.started_at DESC LIMIT 1) AS pain_today,
               (SELECT count(DISTINCT a.alert_id) FROM alerts a WHERE a.patient_id = p.id AND a.kind = 'fall'
                  AND a.time >= now() - INTERVAL '30 days') AS falls_30d
        FROM patients p LEFT JOIN therapists t ON t.id = p.therapist_id
        WHERE p.id = %s""", (patient_id,), timeout_s=timeout_s)


async def prescriptions(db: Database, patient_id: str, history: bool = False) -> list[dict[str, Any]]:
    return await db.fetch("SELECT * FROM prescriptions WHERE patient_id = %s AND (%s OR active) "
                          "ORDER BY exercise_id, version DESC", (patient_id, history))


# ---- alerts ------------------------------------------------------------------------------------------------

async def alert_steps(db: Database, open_only: bool = True, days: int = 30) -> list[dict[str, Any]]:
    return await db.fetch("""
        SELECT time, alert_id, patient_id, session_id, kind, step, channel, payload
        FROM alerts
        WHERE time >= now() - make_interval(days => %(days)s)
          AND (NOT %(open)s OR alert_id NOT IN (
                SELECT alert_id FROM alerts WHERE step = ANY(%(closed)s)
                  AND time >= now() - make_interval(days => %(days)s)))
        ORDER BY alert_id, time""", {"days": days, "open": open_only, "closed": list(CLOSED_ALERT_STEPS)})


async def alert_latest(db: Database, alert_id: str) -> dict[str, Any] | None:
    return await db.fetchrow("SELECT * FROM alerts WHERE alert_id = %s ORDER BY time DESC LIMIT 1", (alert_id,))


# ---- /stats (§5.13) ----------------------------------------------------------------------------------------

async def total_rows(db: Database) -> int | None:
    """Exact count: fast on columnstore chunks (per-batch row counts); approximate_row_count() undercounts
    compressed chunks on TimescaleDB 2.30 (900k rows reported as ~15k)."""
    row = await db.fetchrow("SELECT count(*) AS n FROM pose_frames", timeout_s=4)
    return int(row["n"]) if row else None


async def compression(db: Database) -> dict[str, Any]:
    row = await db.fetchrow("""
        SELECT sum(total_chunks) AS total_chunks, sum(number_compressed_chunks) AS compressed_chunks,
               sum(before_compression_total_bytes) AS before_bytes,
               sum(after_compression_total_bytes) AS after_bytes
        FROM hypertable_columnstore_stats('pose_frames')""", timeout_s=2)
    row = row or {}
    before, after = row.get("before_bytes"), row.get("after_bytes")
    return {
        "total_chunks": int(row.get("total_chunks") or 0),
        "compressed_chunks": int(row.get("compressed_chunks") or 0),
        "before_bytes": int(before) if before else None,
        "after_bytes": int(after) if after else None,
        "ratio": round(float(before) / float(after), 2) if before and after else None,
    }


async def explain_ms(db: Database, sql: str, params: dict[str, Any], timeout_s: float = 5) -> float | None:
    """Execution Time (ms) from EXPLAIN (ANALYZE, FORMAT JSON)."""
    row = await db.fetchrow("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params, timeout_s=timeout_s)
    if not row:
        return None
    plan = next(iter(row.values()))
    return round(float(plan[0]["Execution Time"]), 2)


async def rows_by_origin(db: Database) -> dict[str, int]:
    """pose_frames rows split by sessions.simulated (always label simulated data)."""
    rows = await db.fetch("SELECT s.simulated, count(*) AS n FROM pose_frames f "
                          "JOIN sessions s ON s.id = f.session_id GROUP BY s.simulated", timeout_s=8)
    out = {"simulated": 0, "live": 0}
    for r in rows:
        out["simulated" if r["simulated"] else "live"] = int(r["n"])
    return out


async def latest_data_checks(db: Database) -> list[dict[str, Any]]:
    return await db.fetch("SELECT DISTINCT ON (check_name) time, check_name, status, details FROM data_checks "
                          "ORDER BY check_name, time DESC", timeout_s=2)


async def ai_calls_today(db: Database) -> dict[str, Any]:
    row = await db.fetchrow(f"""
        SELECT count(*) AS calls, count(*) FILTER (WHERE cached) AS cached, count(*) FILTER (WHERE NOT ok) AS failures
        FROM ai_calls WHERE time >= time_bucket(INTERVAL '1 day', now(), '{TZ}')""", timeout_s=2)
    calls = int(row["calls"]) if row else 0
    cached = int(row["cached"]) if row else 0
    return {"calls": calls, "cached": cached, "failures": int(row["failures"]) if row else 0,
            "cached_pct": round(100 * cached / calls, 1) if calls else None}
