"""Data Guard (TRI.md §5.10): make sure recovery history is never lost silently.

Checks: 1 refresh_ran, 2 missing_summaries, 3 raw_vs_summary (both directions), 4 frozen_unchanged (+ archive
insert), 5 safe_drop (implemented, never scheduled: no retention in the hackathon), 6 compress_verified
(fingerprint → convert_to_columnstore → fingerprint). Results go to `data_checks` via writes.insert_data_check.
SQL here is read-only except the archive insert (writes.py), data_checks, compression and safe_drop.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from server.db import writes
from server.db.pool import Database, DBError
from server.db.queries import TZ
from server.logging_setup import get_logger

log = get_logger("data_guard")

TOLERANCE = 0.01
REFRESH_MAX_AGE_MIN = 30
ARCHIVE_AFTER_DAYS = 31
EXAMPLES = 5
SLOW_S = 120.0

Result = dict[str, Any]

CHECK1_SQL = """
SELECT j.job_id, s.last_run_status, s.last_successful_finish, s.total_runs, s.total_failures,
       s.last_successful_finish < now() - make_interval(mins => %s) AS stale
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats s USING (job_id)
WHERE j.proc_name = 'policy_refresh_continuous_aggregate'"""

CHECK2_SQL = f"""
WITH session_days AS (
  SELECT DISTINCT f.patient_id, f.exercise_id,
         time_bucket(INTERVAL '1 day', f.time, '{TZ}') AS day
  FROM sessions s JOIN pose_frames f ON f.session_id = s.id
  WHERE s.ended_at < now() - INTERVAL '1 hour' AND f.confidence >= 0.6
    AND f.time < time_bucket(INTERVAL '1 day', now(), '{TZ}') - INTERVAL '1 day')
SELECT sd.*
FROM session_days sd LEFT JOIN {{mat}} d USING (patient_id, exercise_id, day)
WHERE d.day IS NULL
ORDER BY sd.day"""

# Day-aligned window; today and yesterday are skipped (real-time buckets would compare raw with raw).
BOUNDS_CTE = f"""
bounds AS (
  SELECT time_bucket(INTERVAL '1 day', now() - make_interval(days => %(days)s), '{TZ}') AS lo,
         time_bucket(INTERVAL '1 day', now(), '{TZ}') - INTERVAL '1 day'                   AS hi)"""

CHECK3_SQL = f"""
WITH {BOUNDS_CTE},
raw AS (
  SELECT patient_id, exercise_id,
         time_bucket(INTERVAL '1 day', time, '{TZ}') AS day,
         max(greatest(knee_l, knee_r)) AS max_knee, avg(load_l_pct) AS avg_load_left, count(*) AS frames
  FROM pose_frames, bounds
  WHERE confidence >= 0.6 AND time >= bounds.lo AND time < bounds.hi
  GROUP BY 1, 2, 3)
SELECT r.*, d.max_knee AS agg_max_knee, d.avg_load_left AS agg_avg_load_left, d.frames AS agg_frames,
       count(*) OVER () AS compared, 'raw_vs_summary' AS direction
FROM raw r LEFT JOIN {{mat}} d USING (patient_id, exercise_id, day)
WHERE d.day IS NULL OR abs(r.max_knee - d.max_knee) > {TOLERANCE}
   OR abs(r.avg_load_left - d.avg_load_left) > {TOLERANCE} OR r.frames <> d.frames"""

CHECK3_COUNT_SQL = f"""
WITH {BOUNDS_CTE}
SELECT count(*) AS rows, count(DISTINCT day) AS days FROM (
  SELECT DISTINCT patient_id, exercise_id, time_bucket(INTERVAL '1 day', time, '{TZ}') AS day
  FROM pose_frames, bounds
  WHERE confidence >= 0.6 AND time >= bounds.lo AND time < bounds.hi) x"""

CHECK3_REVERSE_SQL = f"""
WITH {BOUNDS_CTE},
raw_days AS (
  SELECT DISTINCT patient_id, exercise_id, time_bucket(INTERVAL '1 day', time, '{TZ}') AS day
  FROM pose_frames, bounds
  WHERE confidence >= 0.6 AND time >= bounds.lo AND time < bounds.hi)
SELECT d.patient_id, d.exercise_id, d.day
FROM {{mat}} d CROSS JOIN bounds b
LEFT JOIN raw_days r ON r.patient_id = d.patient_id AND r.exercise_id = d.exercise_id AND r.day = d.day
WHERE d.day >= b.lo AND d.day < b.hi AND r.day IS NULL"""

CHECK4_SQL = f"""
SELECT a.patient_id, a.exercise_id, a.day, a.max_knee AS archived_max_knee, v.max_knee AS current_max_knee
FROM daily_progress_archive a
LEFT JOIN progress_v v USING (patient_id, exercise_id, day)
WHERE (v.day IS NULL OR abs(v.max_knee - a.max_knee) > {TOLERANCE})
  AND (%(before)s::timestamptz IS NULL OR a.day < %(before)s)"""

FINGERPRINT_SQL = """
SELECT count(*) AS rows,
       md5(string_agg(md5(f::text), '' ORDER BY time, patient_id, session_id, md5(f::text))) AS fingerprint
FROM pose_frames f
WHERE time >= %s AND time < %s"""


def _result(check_name: str, status: str, **details: Any) -> Result:
    return {"check_name": check_name, "status": status, "details": details}


def _examples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: (str(v) if k == "day" else v) for k, v in r.items() if k not in ("compared", "direction")}
            for r in rows[:EXAMPLES]]


class DataGuard:
    """Checks 2, 3 and 5 read the MATERIALIZED rows from daily_progress's materialization hypertable: with
    materialized_only = false, daily_progress itself serves raw-computed rows for any day that was never
    materialized, which would make those checks pass vacuously."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._mat: sql.Identifier | None = None

    async def _fetch_mat(self, query: str, params: Any = None, timeout_s: float = SLOW_S) -> list[dict[str, Any]]:
        if self._mat is None:
            row = await self.db.fetchrow(
                "SELECT materialization_hypertable_schema AS s, materialization_hypertable_name AS n "
                "FROM timescaledb_information.continuous_aggregates WHERE view_name = 'daily_progress'")
            if row is None:
                raise DBError("continuous aggregate daily_progress does not exist")
            self._mat = sql.Identifier(row["s"], row["n"])
        return await self.db.fetch(sql.SQL(query).format(mat=self._mat), params, timeout_s=timeout_s)

    # ---- check 1 ------------------------------------------------------------------------------------------

    async def check_refresh_ran(self) -> Result:
        rows = await self.db.fetch(CHECK1_SQL, (REFRESH_MAX_AGE_MIN,))
        if not rows:
            return _result("refresh_ran", "fail", reason="no refresh policy job for daily_progress")
        job = rows[0]
        last = job["last_successful_finish"]
        details = {"job_id": job["job_id"], "last_run_status": job["last_run_status"],
                   "last_success": last.isoformat() if last else None,
                   "total_failures": job["total_failures"]}
        if not job["total_runs"]:
            return _result("refresh_ran", "warn", reason="refresh policy has not run yet", **details)
        if job["last_run_status"] != "Success" or job["stale"] or last is None:
            errors = await self.db.fetch("SELECT finish_time, sqlerrcode, err_message "
                                         "FROM timescaledb_information.job_errors WHERE job_id = %s "
                                         "ORDER BY finish_time DESC LIMIT 5", (job["job_id"],))
            return _result("refresh_ran", "fail", errors=[{**e, "finish_time": str(e["finish_time"])}
                                                          for e in errors], **details)
        return _result("refresh_ran", "ok", **details)

    # ---- check 2 ------------------------------------------------------------------------------------------

    async def check_missing_summaries(self) -> Result:
        rows = await self._fetch_mat(CHECK2_SQL, timeout_s=SLOW_S)
        return _result("missing_summaries", "fail" if rows else "ok", missing=len(rows), examples=_examples(rows))

    # ---- check 3 ------------------------------------------------------------------------------------------

    async def check_raw_vs_summary(self, days: int = 30) -> Result:
        params = {"days": days}
        mismatches = await self._fetch_mat(CHECK3_SQL, params, timeout_s=SLOW_S)
        orphans = await self._fetch_mat(CHECK3_REVERSE_SQL, params, timeout_s=SLOW_S)
        counts = await self.db.fetchrow(CHECK3_COUNT_SQL, params, timeout_s=SLOW_S) or {"rows": 0, "days": 0}
        bad = len(mismatches) + len(orphans)
        return _result("raw_vs_summary", "fail" if bad else "ok", window_days=days, days_compared=counts["days"],
                       rows_compared=counts["rows"], mismatches=len(mismatches), summaries_without_raw=len(orphans),
                       examples=_examples(mismatches) + _examples(orphans))

    # ---- archive + check 4 ----------------------------------------------------------------------------------

    async def archive(self, older_than_days: int = ARCHIVE_AFTER_DAYS) -> int | None:
        return await writes.archive_finished_days(self.db, older_than_days)

    async def check_frozen_unchanged(self, before: Any = None) -> Result:
        rows = await self.db.fetch(CHECK4_SQL, {"before": before}, timeout_s=SLOW_S)
        verified = await self.db.fetchrow("SELECT count(*) AS n, count(DISTINCT day) AS days "
                                          "FROM daily_progress_archive")
        return _result("frozen_unchanged", "fail" if rows else "ok", rows_verified=verified["n"] if verified else 0,
                       days_verified=verified["days"] if verified else 0, changed_or_missing=len(rows),
                       examples=_examples(rows))

    # ---- run_all -----------------------------------------------------------------------------------------------

    async def run_all(self, record: bool = True) -> list[Result]:
        """Archive insert, then checks 1-4. Each check is isolated: one failure doesn't hide the others."""
        try:
            archived = await self.archive()
        except DBError as exc:
            archived = None
            log.warning("archive insert failed", extra={"error": str(exc)})
        results = []
        for check in (self.check_refresh_ran, self.check_missing_summaries, self.check_raw_vs_summary,
                      self.check_frozen_unchanged):
            try:
                result = await check()
            except DBError as exc:
                result = _result(check.__name__.removeprefix("check_"), "error", error=str(exc))
            if result["check_name"] == "frozen_unchanged":
                result["details"]["archived_now"] = archived
            results.append(result)
        if record:
            for r in results:
                await writes.insert_data_check(self.db, r["check_name"], r["status"], r["details"])
        log.info("data guard run", extra={"results": {r["check_name"]: r["status"] for r in results}})
        return results

    # ---- check 5 (only if retention is ever enabled; not scheduled) ------------------------------------------

    async def safe_drop(self, older_than_days: int) -> Result:
        """Drop raw frames older than N local days only after their summaries exist, are archived and match."""
        cutoff_row = await self.db.fetchrow(
            f"SELECT time_bucket(INTERVAL '1 day', now() - make_interval(days => %s), '{TZ}') AS cutoff",
            (older_than_days,))
        assert cutoff_row is not None
        cutoff = cutoff_row["cutoff"]
        missing = await self._fetch_mat(f"""
            SELECT r.patient_id, r.exercise_id, r.day FROM (
              SELECT DISTINCT patient_id, exercise_id, time_bucket(INTERVAL '1 day', time, '{TZ}') AS day
              FROM pose_frames WHERE confidence >= 0.6 AND time < %(cutoff)s) r
            LEFT JOIN {{mat}} d USING (patient_id, exercise_id, day)
            WHERE d.day IS NULL""", {"cutoff": cutoff}, timeout_s=SLOW_S)
        if missing:
            result = _result("safe_to_drop", "fail", cutoff=str(cutoff), reason="summary missing; nothing dropped",
                             examples=_examples(missing))
        else:
            await writes.archive_finished_days(self.db, older_than_days)
            changed = await self.check_frozen_unchanged(before=cutoff)
            unarchived = await self.db.fetch("""
                SELECT v.patient_id, v.exercise_id, v.day FROM progress_v v
                LEFT JOIN daily_progress_archive a USING (patient_id, exercise_id, day)
                WHERE v.day < %s AND a.day IS NULL""", (cutoff,), timeout_s=SLOW_S)
            if changed["status"] != "ok" or unarchived:
                result = _result("safe_to_drop", "fail", cutoff=str(cutoff),
                                 reason="archive does not match the summaries; nothing dropped",
                                 changed=changed["details"]["changed_or_missing"], unarchived=len(unarchived))
            else:
                dropped = await self.db.fetch("SELECT drop_chunks('pose_frames', older_than => %s) AS chunk",
                                              (cutoff,), timeout_s=SLOW_S)
                result = _result("safe_to_drop", "ok", cutoff=str(cutoff), chunks_dropped=len(dropped))
        await writes.insert_data_check(self.db, result["check_name"], result["status"], result["details"])
        return result

    # ---- check 6: compression with fingerprints ----------------------------------------------------------------

    async def fingerprint(self, start: Any, end: Any) -> dict[str, Any]:
        row = await self.db.fetchrow(FINGERPRINT_SQL, (start, end), timeout_s=SLOW_S)
        return {"rows": row["rows"], "fingerprint": row["fingerprint"]} if row else {"rows": 0, "fingerprint": None}

    async def compress_finished_chunks(self, min_age_days: int = 1, record: bool = True) -> list[Result]:
        """Convert every finished, uncompressed pose_frames chunk, proving each one lossless."""
        chunks = await self.db.fetch("""
            SELECT chunk_schema, chunk_name, range_start, range_end FROM timescaledb_information.chunks
            WHERE hypertable_name = 'pose_frames' AND NOT is_compressed
              AND range_end <= now() - make_interval(days => %s)
            ORDER BY range_start""", (min_age_days,))
        results = []
        for chunk in chunks:
            result = await self.compress_chunk(chunk)
            results.append(result)
            if record:
                await writes.insert_data_check(self.db, "compress_verified", result["status"], result["details"])
        return results

    async def compress_chunk(self, chunk: dict[str, Any]) -> Result:
        name = f"{chunk['chunk_schema']}.{chunk['chunk_name']}"
        before = await self.fingerprint(chunk["range_start"], chunk["range_end"])
        await self.db.execute(sql.SQL("CALL convert_to_columnstore({}, if_not_columnstore => true)")
                              .format(sql.Literal(name)), timeout_s=SLOW_S)
        after = await self.fingerprint(chunk["range_start"], chunk["range_end"])
        size = await self.db.fetchrow(
            "SELECT before_compression_total_bytes AS before, after_compression_total_bytes AS after "
            "FROM chunk_columnstore_stats('pose_frames') WHERE chunk_schema = %s AND chunk_name = %s",
            (chunk["chunk_schema"], chunk["chunk_name"]))
        match = before == after
        details = {"chunk": name, "range_start": str(chunk["range_start"]), "rows": before["rows"],
                   "fingerprint_match": match, "fingerprint": before["fingerprint"],
                   "bytes_before": size["before"] if size else None, "bytes_after": size["after"] if size else None}
        if not match:
            details["rows_after"], details["fingerprint_after"] = after["rows"], after["fingerprint"]
            log.error("compression fingerprint mismatch", extra=details)
        return _result("compress_verified", "ok" if match else "fail", **details)
