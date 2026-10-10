"""THE ONLY place with INSERT/UPDATE SQL (besides writer.py COPY and the setup scripts).

Every function takes `db` first, uses fixed parameterized SQL, and never raises on database failure: it logs
and returns None/False. With `db=None` (no database configured) it returns immediately. Background agents
write only through these. For fire-and-forget use `fire(writes.insert_x(db, ...))`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar

from psycopg.types.json import Jsonb

from server.db.pool import Database, DBError
from server.logging_setup import get_logger

log = get_logger("db.writes")

T = TypeVar("T")
Timestamp = datetime | float | int | None

_tasks: set[asyncio.Task[Any]] = set()


class InvalidInput(ValueError):
    """Bad arguments from the caller (raised, unlike database failures)."""


def fire(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule a write without awaiting it (keeps a strong reference until it finishes)."""
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def ts(value: Timestamp) -> datetime:
    """Epoch seconds or datetime → aware datetime (UTC); None → now."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(float(value), UTC)


def _dumps(value: Any) -> str:
    """JSON for jsonb columns; datetimes, UUIDs and other non-JSON values become strings instead of raising."""
    return json.dumps(value, default=str)


def _jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=_dumps)


def _json(value: Any) -> Jsonb | None:
    return None if value is None else _jsonb(value)


async def _safe(db: Database | None, op: str, fn: Callable[[Database], Awaitable[T]],
                default: T, **log_fields: Any) -> T:
    if db is None:
        return default
    try:
        return await fn(db)
    except DBError as exc:
        log.warning(f"db write failed: {op}", extra={"error": str(exc), **log_fields})
        return default
    except InvalidInput:
        raise  # deliberate input validation (new_prescription_version), not a database failure
    except Exception as exc:  # noqa: BLE001 - writes never raise into callers (safety path, agents)
        log.exception(f"db write failed unexpectedly: {op}", extra={"error": repr(exc), **log_fields})
        return default


# ---- sessions ---------------------------------------------------------------------------------------

async def insert_session(db: Database | None, session_id: str, patient_id: str, exercise_id: str,
                         started_at: Timestamp, simulated: bool = False) -> bool:
    """True when the row exists afterwards (inserted now or already there); False on failure."""
    sql = ("INSERT INTO sessions (id, patient_id, exercise_id, started_at, simulated) "
           "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")
    return await _safe(db, "insert_session", lambda d: _done(d.execute(
        sql, (session_id, patient_id, exercise_id, ts(started_at), simulated))), False, session_id=session_id)


async def end_session(db: Database | None, session_id: str, ended_at: Timestamp,
                      reps_done: int | None = None) -> bool:
    sql = "UPDATE sessions SET ended_at = %s, reps_done = COALESCE(%s, reps_done) WHERE id = %s"
    return await _safe(db, "end_session", lambda d: _ok(d.execute(sql, (ts(ended_at), reps_done, session_id))),
                       False, session_id=session_id)


async def record_session_feedback(db: Database | None, session_id: str, *, pain_pre: int | None = None,
                                  pain_post: int | None = None, effort: int | None = None,
                                  note: str | None = None) -> bool:
    """Pain (0-10) before/after, effort and a note; None leaves a field unchanged."""
    sql = ("UPDATE sessions SET pain_pre = COALESCE(%s, pain_pre), pain_post = COALESCE(%s, pain_post), "
           "effort = COALESCE(%s, effort), note = COALESCE(%s, note) WHERE id = %s")
    return await _safe(db, "record_session_feedback", lambda d: _ok(d.execute(
        sql, (pain_pre, pain_post, effort, note, session_id))), False, session_id=session_id)


async def set_session_shared(db: Database | None, session_id: str, shared: bool = True) -> bool:
    sql = "UPDATE sessions SET shared = %s WHERE id = %s"
    return await _safe(db, "set_session_shared", lambda d: _ok(d.execute(sql, (shared, session_id))), False,
                       session_id=session_id)


async def update_session_ai(db: Database | None, session_id: str, *, ai_summary: str | None,
                            ai_note: str | None) -> None:
    """Post-set/session AI text (F2). None leaves a field unchanged. Never raises on database failure."""
    sql = ("UPDATE sessions SET ai_summary = COALESCE(%s, ai_summary), ai_note = COALESCE(%s, ai_note) "
           "WHERE id = %s")
    await _safe(db, "update_session_ai", lambda d: d.execute(sql, (ai_summary, ai_note, session_id)), 0,
                session_id=session_id)


async def insert_rep(db: Database | None, session_id: str, rep: int, *, set_no: int | None = None,
                     started_at: Timestamp = None, ended_at: Timestamp = None, peak_angle: float | None = None,
                     rom: float | None = None, hold_s: float | None = None, asym_avg: float | None = None,
                     asym_max: float | None = None, smoothness: float | None = None, quality: float | None = None,
                     flags: list[str] | None = None) -> bool:
    sql = ("INSERT INTO reps (session_id, rep, set_no, started_at, ended_at, peak_angle, rom, hold_s, asym_avg, "
           "asym_max, smoothness, quality, flags) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
           "ON CONFLICT (session_id, rep) DO NOTHING")
    params = (session_id, rep, set_no, ts(started_at) if started_at is not None else None,
              ts(ended_at) if ended_at is not None else None, peak_angle, rom, hold_s, asym_avg, asym_max,
              smoothness, quality, flags or [])
    return await _safe(db, "insert_rep", lambda d: _ok(d.execute(sql, params)), False, session_id=session_id)


async def insert_form_event(db: Database | None, time: Timestamp, patient_id: str | None, session_id: str | None,
                            rep: int | None, rule_id: str, severity: str, duration_s: float | None = None) -> bool:
    sql = ("INSERT INTO form_events (time, patient_id, session_id, rep, rule_id, severity, duration_s) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s)")
    return await _safe(db, "insert_form_event", lambda d: _ok(d.execute(
        sql, (ts(time), patient_id, session_id, rep, rule_id, severity, duration_s))), False, session_id=session_id)


# ---- safety, agents, AI ---------------------------------------------------------------------------------

async def insert_alert_step(db: Database | None, time: Timestamp, alert_id: str, patient_id: str | None,
                            session_id: str | None, kind: str, step: str, channel: str | None,
                            payload: dict[str, Any] | None) -> bool:
    sql = ("INSERT INTO alerts (time, alert_id, patient_id, session_id, kind, step, channel, payload) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)")
    return await _safe(db, "insert_alert_step", lambda d: _ok(d.execute(
        sql, (ts(time), alert_id, patient_id, session_id, kind, step, channel, _json(payload)))), False,
        alert_id=alert_id, step=step)


async def insert_agent_action(db: Database | None, agent: str, action: str, patient_id: str | None = None,
                              session_id: str | None = None, detail: dict[str, Any] | None = None,
                              time: Timestamp = None) -> bool:
    sql = ("INSERT INTO agent_actions (time, agent, action, patient_id, session_id, detail) "
           "VALUES (%s, %s, %s, %s, %s, %s)")
    return await _safe(db, "insert_agent_action", lambda d: _ok(d.execute(
        sql, (ts(time), agent, action, patient_id, session_id, _json(detail)))), False, agent=agent)


async def insert_ai_call(db: Database | None, agent: str, model: str | None, ms: int, ok: bool, cached: bool,
                         error: str | None = None, time: Timestamp = None) -> bool:
    sql = "INSERT INTO ai_calls (time, agent, model, ms, ok, cached, error) VALUES (%s, %s, %s, %s, %s, %s, %s)"
    return await _safe(db, "insert_ai_call", lambda d: _ok(d.execute(
        sql, (ts(time), agent, model, int(ms), ok, cached, error))), False, agent=agent)


async def insert_memory_event(db: Database | None, patient_id: str, kind: str, message: str,
                              evidence: dict[str, Any], dedupe_key: str | None = None, agent: str = "insight",
                              time: Timestamp = None) -> int | None:
    """Returns the new id, or None if the dedupe_key already exists (or on failure)."""
    sql = ("INSERT INTO memory_events (patient_id, time, kind, message, evidence, dedupe_key, agent) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id")
    return await _safe(db, "insert_memory_event", lambda d: _id(d.fetchrow(
        sql, (patient_id, ts(time), kind, message, _jsonb(evidence), dedupe_key, agent))), None,
        patient_id=patient_id)


async def insert_plan_proposal(db: Database | None, patient_id: str, agent: str, rationale: str,
                               evidence: dict[str, Any], change: dict[str, Any]) -> int | None:
    sql = ("INSERT INTO plan_proposals (patient_id, agent, rationale, evidence, change) "
           "VALUES (%s, %s, %s, %s, %s) RETURNING id")
    return await _safe(db, "insert_plan_proposal", lambda d: _id(d.fetchrow(
        sql, (patient_id, agent, rationale, _jsonb(evidence), _jsonb(change)))), None, patient_id=patient_id)


async def decide_proposal(db: Database | None, proposal_id: int, status: str, decided_by: str
                          ) -> dict[str, Any] | None:
    """pending → approved | edited | dismissed. Returns the updated row, or None if it wasn't pending."""
    if status not in ("approved", "edited", "dismissed"):
        raise ValueError(f"invalid proposal status {status!r}")
    sql = ("UPDATE plan_proposals SET status = %s, decided_by = %s, decided_at = now() "
           "WHERE id = %s AND status = 'pending' RETURNING *")
    return await _safe(db, "decide_proposal", lambda d: d.fetchrow(sql, (status, decided_by, proposal_id)), None,
                       proposal_id=proposal_id)


async def new_prescription_version(db: Database | None, patient_id: str, exercise_id: str, *, created_by: str,
                                   sets: int | None = None, reps: int | None = None,
                                   targets: dict[str, Any] | None = None, limits: dict[str, Any] | None = None,
                                   rule_overrides: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Insert version N+1 (active) and deactivate older versions. Omitted fields carry over from the active
    version. Raises ValueError if there is no previous version and sets/reps are missing."""
    if db is None:
        return None

    async def run(d: Database) -> dict[str, Any] | None:
        async with d.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"rx:{patient_id}:{exercise_id}",))
            cur = await conn.execute(
                "SELECT version, sets, reps, targets, limits, rule_overrides FROM prescriptions "
                "WHERE patient_id = %s AND exercise_id = %s ORDER BY active DESC, version DESC LIMIT 1",
                (patient_id, exercise_id))
            prev = await cur.fetchone()
            if prev is None and (sets is None or reps is None):
                raise InvalidInput("sets and reps are required for a new exercise")
            base = prev or {"version": 0, "targets": {}, "limits": {}, "rule_overrides": {}}
            await conn.execute("UPDATE prescriptions SET active = false WHERE patient_id = %s AND exercise_id = %s "
                               "AND active", (patient_id, exercise_id))
            cur = await conn.execute(
                "INSERT INTO prescriptions (patient_id, exercise_id, version, sets, reps, targets, limits, "
                "rule_overrides, active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s) RETURNING *",
                (patient_id, exercise_id, base["version"] + 1,
                 sets if sets is not None else base["sets"], reps if reps is not None else base["reps"],
                 _jsonb(targets if targets is not None else base["targets"]),
                 _jsonb(limits if limits is not None else base["limits"]),
                 _jsonb(rule_overrides if rule_overrides is not None else base["rule_overrides"]), created_by))
            return await cur.fetchone()

    return await _safe(db, "new_prescription_version", run, None, patient_id=patient_id)


# ---- Data Guard ------------------------------------------------------------------------------------------

async def insert_data_check(db: Database | None, check_name: str, status: str, details: dict[str, Any],
                            time: Timestamp = None) -> bool:
    sql = "INSERT INTO data_checks (time, check_name, status, details) VALUES (%s, %s, %s, %s)"
    return await _safe(db, "insert_data_check", lambda d: _ok(d.execute(
        sql, (ts(time), check_name, status, _jsonb(details)))), False, check_name=check_name)


async def archive_finished_days(db: Database | None, older_than_days: int = 31) -> int | None:
    """Copy frozen days from progress_v into daily_progress_archive (never overwrites). Returns rows added."""
    sql = ("INSERT INTO daily_progress_archive SELECT * FROM progress_v "
           "WHERE day < now() - make_interval(days => %s) ON CONFLICT DO NOTHING")
    return await _safe(db, "archive_finished_days", lambda d: d.execute(sql, (older_than_days,), timeout_s=60),
                       None)


# ---- notifications (§8.7) ------------------------------------------------------------------------------

NOTIFICATION_INSERT_SQL = (
    "INSERT INTO notifications (kind, severity, therapist_id, patient_id, title, body, link, source_table, "
    "source_id, dedupe_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id")


async def insert_notification(db: Database | None, kind: str, severity: str, therapist_id: str,
                              patient_id: str | None, title: str, body: str | None, link: str | None,
                              source_table: str | None, source_id: str | None, dedupe_key: str | None
                              ) -> int | None:
    """Returns the new id, or None if dedupe_key was already used (or on failure)."""
    return await _safe(db, "insert_notification", lambda d: insert_notification_or_raise(
        d, kind, severity, therapist_id, patient_id, title, body, link, source_table, source_id, dedupe_key),
        None, kind=kind)


async def insert_notification_or_raise(db: Database, kind: str, severity: str, therapist_id: str,
                                       patient_id: str | None, title: str, body: str | None, link: str | None,
                                       source_table: str | None, source_id: str | None, dedupe_key: str | None
                                       ) -> int | None:
    """Same INSERT, but raises DBError on failure so the caller can tell a duplicate (None) from a failed
    write (notifications.create() still delivers an urgent row live when the database is down)."""
    return await _id(db.fetchrow(NOTIFICATION_INSERT_SQL, (kind, severity, therapist_id, patient_id, title, body,
                                                           link, source_table, source_id, dedupe_key)))


async def mark_notification_read(db: Database | None, notification_id: int) -> bool:
    sql = "UPDATE notifications SET read_at = COALESCE(read_at, now()) WHERE id = %s"
    return await _safe(db, "mark_notification_read", lambda d: _ok(d.execute(sql, (notification_id,))), False)


async def mark_notification_ack(db: Database | None, notification_id: int, acked_by: str) -> bool:
    sql = ("UPDATE notifications SET acked_at = COALESCE(acked_at, now()), acked_by = COALESCE(acked_by, %s), "
           "read_at = COALESCE(read_at, now()) WHERE id = %s")
    return await _safe(db, "mark_notification_ack", lambda d: _ok(d.execute(sql, (acked_by, notification_id))),
                       False)


async def mark_all_notifications_read(db: Database | None, therapist_id: str | None) -> int | None:
    """therapist_id=None marks every therapist's unread rows read."""
    sql = ("UPDATE notifications SET read_at = now() "
           "WHERE read_at IS NULL AND (%(t)s::text IS NULL OR therapist_id = %(t)s)")
    return await _safe(db, "mark_all_notifications_read", lambda d: d.execute(sql, {"t": therapist_id}), None)


# ---- helpers ---------------------------------------------------------------------------------------------

async def _ok(count: Awaitable[int]) -> bool:
    return await count > 0


async def _done(count: Awaitable[int]) -> bool:
    """True once the statement ran (an ON CONFLICT DO NOTHING that changed nothing still counts)."""
    await count
    return True


async def _id(row: Awaitable[dict[str, Any] | None]) -> int | None:
    found = await row
    return int(found["id"]) if found else None
