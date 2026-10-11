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


async def insert_heart_rate(db: Database | None, time: Timestamp, patient_id: str | None, session_id: str | None,
                            bpm: int) -> bool:
    sql = "INSERT INTO heart_rate (time, patient_id, session_id, bpm) VALUES (%s, %s, %s, %s)"
    return await _safe(db, "insert_heart_rate", lambda d: _ok(d.execute(
        sql, (ts(time), patient_id, session_id, bpm))), False, session_id=session_id)


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
            return await _insert_version(conn, patient_id, exercise_id, created_by=created_by, sets=sets, reps=reps,
                                         targets=(lambda _: targets) if targets is not None else None,
                                         limits=limits, rule_overrides=rule_overrides)

    return await _safe(db, "new_prescription_version", run, None, patient_id=patient_id)


class ProposalConflict(InvalidInput):
    """The proposal can't be decided: unknown (`status` None), no longer pending, or its exercise isn't active."""

    def __init__(self, message: str, status: str | None = "pending") -> None:
        super().__init__(message)
        self.status = status


async def _insert_version(conn: Any, patient_id: str, exercise_id: str, *, created_by: str, sets: int | None,
                          reps: int | None, targets: Callable[[dict[str, Any]], dict[str, Any]] | None,
                          limits: dict[str, Any] | None = None, rule_overrides: dict[str, Any] | None = None,
                          require_active: bool = False) -> dict[str, Any]:
    """Inside the caller's transaction: lock the plan, deactivate the active version, insert N+1. `targets` maps
    the previous version's targets to the new ones (None = carry over)."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"rx:{patient_id}:{exercise_id}",))
    cur = await conn.execute(
        "SELECT version, sets, reps, targets, limits, rule_overrides, active FROM prescriptions "
        "WHERE patient_id = %s AND exercise_id = %s ORDER BY active DESC, version DESC LIMIT 1",
        (patient_id, exercise_id))
    prev = await cur.fetchone()
    if require_active and (prev is None or not prev["active"]):
        raise ProposalConflict(f"{exercise_id!r} is not an active prescription")
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
         _jsonb(targets(base["targets"] or {}) if targets is not None else base["targets"]),
         _jsonb(limits if limits is not None else base["limits"]),
         _jsonb(rule_overrides if rule_overrides is not None else base["rule_overrides"]), created_by))
    return await cur.fetchone()


async def decide_proposal_and_apply(db: Database | None, proposal_id: int, status: str, decided_by: str, *,
                                    exercise_id: str | None = None, sets: int | None = None,
                                    reps: int | None = None,
                                    targets: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                                    agent: str = "copilot", detail: dict[str, Any] | None = None
                                    ) -> dict[str, Any] | None:
    """The therapist's decision, all or nothing in ONE transaction: the pending proposal → approved | edited |
    dismissed (decided_by, decided_at); for approved/edited the active version of `exercise_id` is deactivated
    and version N+1 inserted (created_by 'proposal:<id>', targets = targets(active targets)); and an agent_actions
    row 'proposal_<status>'. Returns {"proposal", "prescription"} (prescription None when dismissed).
    Raises ProposalConflict (unknown / not pending / exercise not active); None on database failure."""
    if status not in ("approved", "edited", "dismissed"):
        raise ValueError(f"invalid proposal status {status!r}")
    if status != "dismissed" and not exercise_id:
        raise InvalidInput("exercise_id is required to approve or edit")

    async def run(d: Database) -> dict[str, Any]:
        async with d.connection() as conn, conn.transaction():
            cur = await conn.execute("SELECT id, patient_id, status FROM plan_proposals WHERE id = %s FOR UPDATE",
                                     (proposal_id,))
            current = await cur.fetchone()
            if current is None:
                raise ProposalConflict("proposal not found", status=None)
            if current["status"] != "pending":
                raise ProposalConflict(f"proposal already {current['status']}", status=current["status"])
            rx = None
            if status != "dismissed":
                assert exercise_id is not None
                rx = await _insert_version(conn, current["patient_id"], exercise_id,
                                           created_by=f"proposal:{proposal_id}", sets=sets, reps=reps,
                                           targets=targets, require_active=True)
            cur = await conn.execute("UPDATE plan_proposals SET status = %s, decided_by = %s, decided_at = now() "
                                     "WHERE id = %s RETURNING *", (status, decided_by, proposal_id))
            decided = await cur.fetchone()
            action = {"proposal_id": proposal_id, "decided_by": decided_by, **(detail or {})}
            if rx is not None:
                action |= {"prescription_id": rx["id"], "version": rx["version"]}
            await conn.execute("INSERT INTO agent_actions (time, agent, action, patient_id, detail) "
                               "VALUES (now(), %s, %s, %s, %s)",
                               (agent, f"proposal_{status}", current["patient_id"], _jsonb(action)))
            return {"proposal": decided, "prescription": rx}

    return await _safe(db, "decide_proposal_and_apply", run, None, proposal_id=proposal_id)


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


# ---- session analysis (Plan 2) ------------------------------------------------------------------------------

async def upsert_session_analysis(db: Database | None, session_id: str, *, form_score: int | None,
                                  reps_total: int | None, good_reps: int | None, sets_done: int | None,
                                  spoken: str, note: str, highlights: list[Any], next_focus: str | None,
                                  flagged_moments: list[Any], comparison: dict[str, Any], audio_url: str | None,
                                  source: str, model: str | None) -> bool:
    """Whole-session analysis (§5.2); re-running on_stop for the same session overwrites with the latest
    values (ON CONFLICT DO UPDATE), never duplicates."""
    sql = ("INSERT INTO session_analyses (session_id, form_score, reps_total, good_reps, sets_done, spoken, "
           "note, highlights, next_focus, flagged_moments, comparison, audio_url, source, model) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
           "ON CONFLICT (session_id) DO UPDATE SET form_score = EXCLUDED.form_score, "
           "reps_total = EXCLUDED.reps_total, good_reps = EXCLUDED.good_reps, sets_done = EXCLUDED.sets_done, "
           "spoken = EXCLUDED.spoken, note = EXCLUDED.note, highlights = EXCLUDED.highlights, "
           "next_focus = EXCLUDED.next_focus, flagged_moments = EXCLUDED.flagged_moments, "
           "comparison = EXCLUDED.comparison, audio_url = EXCLUDED.audio_url, source = EXCLUDED.source, "
           "model = EXCLUDED.model")
    params = (session_id, form_score, reps_total, good_reps, sets_done, spoken, note, _jsonb(highlights),
              next_focus, _jsonb(flagged_moments), _jsonb(comparison), audio_url, source, model)
    return await _safe(db, "upsert_session_analysis", lambda d: _done(d.execute(sql, params)), False,
                       session_id=session_id)


# ---- patient identity (Plan 1) ------------------------------------------------------------------------------

async def touch_patient_device(db: Database | None, token_hash: str) -> bool:
    sql = "UPDATE patient_devices SET last_seen = now() WHERE token_hash = %s AND revoked_at IS NULL"
    return await _safe(db, "touch_patient_device", lambda d: _ok(d.execute(sql, (token_hash,))), False)


async def insert_patient_device(db: Database | None, token_hash: str, patient_id: str,
                                device_name: str | None, via_reusable: bool = False) -> bool:
    sql = "INSERT INTO patient_devices (token_hash, patient_id, device_name, via_reusable) VALUES (%s, %s, %s, %s)"
    return await _safe(db, "insert_patient_device", lambda d: _ok(d.execute(sql, (token_hash, patient_id,
                                                                                 device_name, via_reusable))),
                       False, patient_id=patient_id)


async def revoke_patient_devices(db: Database | None, patient_id: str) -> int | None:
    sql = "UPDATE patient_devices SET revoked_at = now() WHERE patient_id = %s AND revoked_at IS NULL"
    return await _safe(db, "revoke_patient_devices", lambda d: d.execute(sql, (patient_id,)), None,
                       patient_id=patient_id)


async def revoke_excess_patient_devices(db: Database | None, patient_id: str, keep: int = 10) -> int | None:
    """Keep only the `keep` most recently paired active demo (`via_reusable`) devices for a patient; revoke any
    older ones. Called right after a successful pair so a reusable demo code can't accumulate unbounded live
    tokens. A phone paired with a single-use (therapist-minted) code is never evicted: strangers pairing with the
    public demo code must not be able to sign the real patient's phone out."""
    sql = ("UPDATE patient_devices SET revoked_at = now() WHERE patient_id = %s AND revoked_at IS NULL "
           "AND via_reusable AND token_hash NOT IN (SELECT token_hash FROM patient_devices WHERE patient_id = %s "
           "AND revoked_at IS NULL AND via_reusable ORDER BY created_at DESC LIMIT %s)")
    return await _safe(db, "revoke_excess_patient_devices", lambda d: d.execute(sql, (patient_id, patient_id, keep)),
                       None, patient_id=patient_id)


async def claim_pairing_code(db: Database | None, code: str) -> dict[str, Any] | None:
    """Atomically claim a pairing code: a reusable code is always claimable; a single-use code only while
    unused and unexpired. Returns {patient_id, therapist_id, reusable}, or None if it can't be claimed right
    now (lost the race to another request, or it was expired/used between the caller's lookup and this call).
    Replaces the separate SELECT-then-UPDATE in `pair.py::pair` for the actual claim, closing the TOCTOU race
    where two requests both see an unused single-use code."""
    sql = ("UPDATE pairing_codes SET used_at = CASE WHEN reusable THEN used_at ELSE now() END "
           "WHERE code = %s AND (reusable OR used_at IS NULL) AND (expires_at IS NULL OR expires_at > now()) "
           "RETURNING patient_id, therapist_id, reusable")
    return await _safe(db, "claim_pairing_code", lambda d: d.fetchrow(sql, (code,)), None)


async def upsert_pairing_code(db: Database | None, code: str, patient_id: str, therapist_id: str | None,
                              expires_at: Timestamp = None, reusable: bool = False) -> bool:
    sql = ("INSERT INTO pairing_codes (code, patient_id, therapist_id, expires_at, reusable) "
           "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET patient_id = EXCLUDED.patient_id, "
           "therapist_id = EXCLUDED.therapist_id, expires_at = EXCLUDED.expires_at, reusable = EXCLUDED.reusable, "
           "used_at = NULL")
    return await _safe(db, "upsert_pairing_code", lambda d: _done(d.execute(
        sql, (code, patient_id, therapist_id, ts(expires_at) if expires_at is not None else None, reusable))),
        False, patient_id=patient_id)


async def mark_pairing_code_used(db: Database | None, code: str) -> bool:
    sql = "UPDATE pairing_codes SET used_at = now() WHERE code = %s AND NOT reusable"
    return await _safe(db, "mark_pairing_code_used", lambda d: _done(d.execute(sql, (code,))), False)


PROFILE_FIELDS = ("name", "height_cm", "mass_kg", "affected_side", "condition", "emergency_contact_name",
                  "emergency_contact_phone", "emergency_contact_relationship", "alert_therapist",
                  "share_by_default", "device_prefs")


async def update_patient_profile(db: Database | None, patient_id: str, **fields: Any) -> bool:
    """Partial update of the patient's own fields (PROFILE_FIELDS); None leaves a column unchanged. Raises
    InvalidInput for unknown fields."""
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise InvalidInput(f"unknown profile fields: {sorted(unknown)}")
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return True
    assignments = ", ".join(f"{k} = %({k})s" for k in changes)
    params: dict[str, Any] = {k: (_jsonb(v) if k == "device_prefs" else v) for k, v in changes.items()}
    params["pid"] = patient_id
    sql = f"UPDATE patients SET {assignments} WHERE id = %(pid)s"
    return await _safe(db, "update_patient_profile", lambda d: _ok(d.execute(sql, params)), False,
                       patient_id=patient_id)


async def purge_patient_data(db: Database | None, patient_id: str) -> dict[str, int] | None:
    """DELETE /me: the patient's own (non-simulated) sessions and everything hanging off them, their alerts,
    memory events, notifications, agent actions, messages and phones; profile fields are blanked. Labeled simulated demo
    history is kept: it is not the patient's data. The patient row and prescriptions stay (the therapist's plan).
    One transaction; None on database failure."""
    if db is None:
        return None

    async def run(d: Database) -> dict[str, int]:
        async with d.connection() as conn, conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = 120000")
            cur = await conn.execute("SELECT id FROM sessions WHERE patient_id = %s AND NOT simulated", (patient_id,))
            ids = [r["id"] for r in await cur.fetchall()]
            counts: dict[str, int] = {"sessions": len(ids)}
            if ids:
                for table in ("pose_frames", "reps", "form_events", "wrist_motion", "heart_rate"):
                    cur = await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY(%s)", (ids,))  # noqa: S608
                    counts[table] = cur.rowcount
            for table in ("alerts", "memory_events", "notifications", "agent_actions", "plan_proposals", "messages"):
                cur = await conn.execute(f"DELETE FROM {table} WHERE patient_id = %s", (patient_id,))  # noqa: S608
                counts[table] = cur.rowcount
            if ids:
                await conn.execute("DELETE FROM sessions WHERE id = ANY(%s)", (ids,))
            cur = await conn.execute("UPDATE patient_devices SET revoked_at = now() WHERE patient_id = %s "
                                     "AND revoked_at IS NULL", (patient_id,))
            counts["devices_revoked"] = cur.rowcount
            await conn.execute("UPDATE patients SET height_cm = NULL, mass_kg = NULL, emergency_contact_name = NULL, "
                               "emergency_contact_phone = NULL, emergency_contact_relationship = NULL, "
                               "device_prefs = '{}' WHERE id = %s", (patient_id,))
            return counts

    return await _safe(db, "purge_patient_data", run, None, patient_id=patient_id)


# ---- messages (Plan 3 §6.4) -------------------------------------------------------------------------------

async def insert_message(db: Database | None, patient_id: str, sender: str, kind: str,
                         body: str) -> dict[str, Any] | None:
    sql = ("INSERT INTO messages (patient_id, sender, kind, body) VALUES (%s, %s, %s, %s) "
           "RETURNING id, sender, kind, body, created_at, read_at")
    return await _safe(db, "insert_message", lambda d: d.fetchrow(sql, (patient_id, sender, kind, body)), None,
                       patient_id=patient_id)


async def mark_messages_read(db: Database | None, patient_id: str, sender_not: str) -> int | None:
    """Marks every unread message of this patient's thread NOT sent by `sender_not` as read (the other side)."""
    sql = ("UPDATE messages SET read_at = now() WHERE patient_id = %s AND kind = 'message' "
           "AND sender != %s AND read_at IS NULL")
    return await _safe(db, "mark_messages_read", lambda d: d.execute(sql, (patient_id, sender_not)), None,
                       patient_id=patient_id)


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
