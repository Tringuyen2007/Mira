"""Dashboard notification backend (§8.7): the single place that turns an alert, call step, proposal,
milestone, Insight note, Coach adaptation or Data Guard failure into a therapist notification.

`create()` applies the §8.5 policy (quiet hours, the daily non-urgent push budget), stores the row
(`writes.insert_notification`, `ON CONFLICT (dedupe_key) DO NOTHING`) and publishes it on the hub's
"notifications" channel.

Storage: with a database, the `notifications` table is the source of truth — ids are the table's ids and
REST reads, the bell count and the `/ws/notifications?since_id=` replay all query it, so history and unread
state survive a restart. If the INSERT itself fails (database down), the row is still delivered live with a
negative, process-local id so an urgent alert is never lost; such rows show up in the panel list and count
but not in a since_id replay. Without a database, an in-memory ring with its own id sequence is the store.
The ring also always holds this process's recent rows for the urgent re-send loop and the replay hand-off.

Every other feature reaches this module through a lazy import and calls the module-level `create()`;
that is the whole producer API, exactly as fixed in the plan's Interfaces section. This module also
supplies the Guardian `on_alert_step` hook that turns the safety path into notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.agents.incident_snapshot import status_text
from server.config import cfg
from server.db import queries, writes
from server.db.pool import Database, DBError
from server.hub import SEND_TIMEOUT_S, WebSocketLike, to_json
from server.logging_setup import get_logger, mask_phone
from server.notify import policy as policy_mod
from server.notify import templates
from server.state import get_state

log = get_logger("notifications")

MAX_HISTORY = 500  # in-memory ring: the no-database store, re-send state and the replay hand-off
REPLAY_MAX = 1000  # newest rows replayed on a since_id reconnect (older ones: page with REST since_id)
CHANNEL = "notifications"

_KIND_TO_POLICY = {"milestone": policy_mod.MILESTONE, "proposal": policy_mod.PROPOSAL,
                   "data_guard": policy_mod.DATA_GUARD}

# Guardian step (§7.2/§8.6) -> (notification kind, severity). Everything else is not notified.
_STEP_KIND_SEVERITY = {"escalated": ("alert", "urgent"), "resolved": ("alert", "low")}
_CALL_KINDS = ("alert", "call_update")
_TIME_FIELDS = ("time", "read_at", "acked_at")


def _policy_kind(kind: str, severity: str) -> str:
    """How §8.5 routes this row: severity decides urgency; otherwise map by notification kind."""
    if severity == policy_mod.URGENT:
        return policy_mod.URGENT
    return _KIND_TO_POLICY.get(kind, policy_mod.DIGEST)


def _aware(epoch_s: float) -> datetime:
    """An aware datetime for the policy, which converts to America/Chicago itself."""
    return datetime.fromtimestamp(epoch_s, tz=UTC)


def _epoch(value: Any) -> Any:
    return value.timestamp() if isinstance(value, datetime) else value


def _matches(row: dict[str, Any], therapist_id: str | None, unread: bool) -> bool:
    return (therapist_id is None or row["therapist_id"] == therapist_id) and not (unread and row["read_at"])


class NotificationService:
    """The live object behind `state.notifications`; one per server process."""

    def __init__(self, hub: Any, db: Database | None, *, policy: policy_mod.NotificationPolicy | None = None,
                 notifier: Any | None = None, guardian: Any | None = None, resend_min: float | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.hub = hub
        self.db = db
        self.policy = policy if policy is not None else policy_mod.NotificationPolicy()
        self.notifier = notifier  # F3 RetellNotifier: push_update on ack
        self.guardian = guardian  # F3 SafetyGuardian: find the open incident on ack
        self.resend_min = cfg.NOTIFY_RESEND_MIN if resend_min is None else resend_min
        self.clock = clock
        self._rows: dict[int, dict[str, Any]] = {}
        self._order: list[int] = []
        self._next_id = 1  # no-database id sequence
        self._next_local_id = -1  # rows a configured database failed to store (delivered live only)
        self._dedupe_seen: set[str] = set()
        self._acked_sources: set[tuple[str, str]] = set()  # (source_table, source_id) with an ack
        self._call_pushed: set[str] = set()  # alert_ids already told "her therapist has seen the alert"
        self._resend_tasks: dict[int, asyncio.Task[None]] = {}
        # Serializes id assignment + publish, so live rows reach clients in id order (the replay cutoff
        # in serve() relies on it). Never held by the Guardian: its hook runs as a background task.
        self._lock = asyncio.Lock()

    # ---- producer API ------------------------------------------------------------------------------

    async def create(self, kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                     body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                     dedupe_key: str | None) -> int | None:
        async with self._lock:
            if dedupe_key is not None and dedupe_key in self._dedupe_seen:
                return None
            now = self.clock()
            policy_kind = _policy_kind(kind, severity)
            decision = self.policy.decide(policy_kind, patient_id or "", _aware(now))
            row_id = await self._insert(kind, severity, therapist_id, patient_id, title, body, link,
                                        source_table, source_id, dedupe_key)
            if row_id is None:  # the database already had this dedupe_key (e.g. from before a restart)
                return None
            if dedupe_key is not None:
                self._dedupe_seen.add(dedupe_key)
            if decision.push and policy_kind != policy_mod.URGENT:
                self.policy.record_push(patient_id or "", _aware(now))  # only rows that really pushed
            row = {"id": row_id, "time": now, "kind": kind, "severity": severity, "therapist_id": therapist_id,
                   "patient_id": patient_id, "title": title, "body": body, "link": link,
                   "source_table": source_table, "source_id": source_id, "dedupe_key": dedupe_key,
                   "read_at": None, "acked_at": None, "acked_by": None, "push": decision.push}
            self._remember(row)
            self.hub.publish(CHANNEL, {"type": "notification", **row})
        log.info("notification created", extra={"notification_id": row_id, "kind": kind, "severity": severity,
                                                "push": decision.push, "reason": decision.reason})
        if severity == policy_mod.URGENT and kind != "call_update":
            self._schedule_resend(row_id)
        return row_id

    async def _insert(self, kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                      body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                      dedupe_key: str | None) -> int | None:
        if self.db is None:
            row_id = self._next_id
            self._next_id += 1
            return row_id
        try:
            return await writes.insert_notification_or_raise(self.db, kind, severity, therapist_id, patient_id,
                                                             title, body, link, source_table, source_id,
                                                             dedupe_key)
        except DBError as exc:
            row_id = self._next_local_id
            self._next_local_id -= 1
            log.warning("notification not stored; delivering it live only",
                        extra={"notification_id": row_id, "kind": kind, "error": str(exc)})
            return row_id

    def _remember(self, row: dict[str, Any]) -> None:
        self._rows[row["id"]] = row
        self._order.append(row["id"])
        if len(self._order) > MAX_HISTORY:
            self._rows.pop(self._order.pop(0), None)

    def _db_backed(self, notification_id: int) -> bool:
        return self.db is not None and notification_id > 0

    def _local_only(self) -> list[dict[str, Any]]:
        """Rows a configured database failed to store (negative ids), oldest first."""
        return [self._rows[i] for i in self._order if i < 0] if self.db is not None else []

    def _from_db(self, row: dict[str, Any]) -> dict[str, Any]:
        out = {k: _epoch(v) if k in _TIME_FIELDS else v for k, v in row.items()}
        out["id"] = int(out["id"])
        mem = self._rows.get(out["id"])
        out["push"] = bool(mem and mem["push"])
        return out

    # ---- urgent resend (§8.7.5) --------------------------------------------------------------------

    def _schedule_resend(self, row_id: int) -> None:
        if self.resend_min <= 0:
            return
        task = asyncio.get_running_loop().create_task(self._resend_loop(row_id),
                                                       name=f"notify-resend-{row_id}")
        self._resend_tasks[row_id] = task
        task.add_done_callback(lambda _t: self._resend_tasks.pop(row_id, None))

    def _settled(self, row: dict[str, Any] | None) -> bool:
        """No more re-sends: acked itself, or the therapist acked another row about the same alert."""
        if row is None or row["acked_at"] is not None:
            return True
        source = (row["source_table"], row["source_id"])
        return source[1] is not None and source in self._acked_sources

    async def _resend_loop(self, row_id: int) -> None:
        while True:
            await asyncio.sleep(self.resend_min * 60)
            row = self._rows.get(row_id)
            if row is None or self._settled(row):
                return
            log.info("resending unacked urgent notification", extra={"notification_id": row_id})
            self.hub.publish(CHANNEL, {"type": "notification", "resend": True, **row})

    # ---- reads (REST panel + polling fallback) -----------------------------------------------------

    async def list_notifications(self, *, therapist_id: str | None = None, unread: bool = False,
                                 since_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Panel (no since_id): newest first. Polling fallback (since_id): the oldest `limit` rows after
        since_id, ascending, so a client that keeps polling with its last id pages through without gaps."""
        if self.db is not None:
            try:
                rows = await queries.notifications(self.db, therapist_id=therapist_id, unread=unread,
                                                   since_id=since_id, limit=limit, newest_first=since_id is None)
            except DBError as exc:
                log.warning("notification list from the database failed; using memory", extra={"error": str(exc)})
            else:
                out = [self._from_db(r) for r in rows]
                if since_id is None:  # undelivered-to-DB rows still belong in the panel
                    local = [dict(r) for r in self._local_only() if _matches(r, therapist_id, unread)]
                    out = sorted(out + local, key=lambda r: r["time"], reverse=True)[:limit]
                return out
        rows = [dict(self._rows[i]) for i in self._order if _matches(self._rows[i], therapist_id, unread)
                and (since_id is None or i > since_id)]
        if since_id is not None:
            return rows[:limit]
        rows.reverse()
        return rows[:limit]

    async def count(self, therapist_id: str | None = None) -> dict[str, int]:
        rows = list(self._rows.values())
        out = {"unread": 0, "urgent_unacked": 0}
        if self.db is not None:
            try:
                out = await queries.notification_counts(self.db, therapist_id)
                rows = self._local_only()
            except DBError as exc:
                log.warning("notification count from the database failed; using memory", extra={"error": str(exc)})
        for row in rows:
            if therapist_id is None or row["therapist_id"] == therapist_id:
                out["unread"] += row["read_at"] is None
                out["urgent_unacked"] += row["severity"] == policy_mod.URGENT and row["acked_at"] is None
        return out

    async def _get(self, notification_id: int) -> dict[str, Any] | None:
        if self._db_backed(notification_id):
            try:
                row = await queries.notification(self.db, notification_id)  # type: ignore[arg-type]
                return self._from_db(row) if row else None
            except DBError as exc:
                log.warning("notification lookup failed; using memory", extra={"error": str(exc)})
        mem = self._rows.get(notification_id)
        return dict(mem) if mem else None

    # ---- WS replay + live stream (§8.7.3) -----------------------------------------------------------

    async def serve(self, ws: WebSocketLike, *, since_id: int | None, therapist_id: str | None) -> None:
        """Serve an accepted `/ws/notifications` socket: replay rows after `since_id`, then stream live.

        No gap, no duplicate: the database part of the replay is read *before* the client registers;
        registration, the hand-off from this process's ring (rows created after that query) and fixing the
        live `cutoff` all happen synchronously at the start of `on_connect`, so every row is either replayed
        or published to the already-registered client — and a live row the replay already sent (`id <=
        cutoff`) is dropped by the client's filter. `therapist_id` scopes the replay and the live stream.
        """
        replay: list[dict[str, Any]] = []
        if since_id is not None and self.db is not None:
            try:
                rows = await queries.notifications(self.db, therapist_id=therapist_id, since_id=since_id,
                                                   limit=REPLAY_MAX, newest_first=True)
                replay = [self._from_db(r) for r in reversed(rows)]
            except DBError as exc:
                log.warning("notification replay from the database failed; using memory", extra={"error": str(exc)})
        cutoff: int | None = None

        def accept(msg: dict[str, Any]) -> bool:
            if therapist_id is not None and msg.get("therapist_id") != therapist_id:
                return False
            row_id = msg.get("id")
            return (cutoff is None or msg.get("resend") is True or not isinstance(row_id, int)
                    or row_id <= 0 or row_id > cutoff)

        async def on_connect() -> None:
            nonlocal cutoff
            if since_id is None:
                return
            last = max([since_id, *(r["id"] for r in replay)])
            rows = replay + [dict(self._rows[i]) for i in self._order
                             if i > last and _matches(self._rows[i], therapist_id, False)]
            cutoff = max([last, *(r["id"] for r in rows)])
            for row in rows:
                await asyncio.wait_for(ws.send_text(to_json({"type": "notification", **row})), SEND_TIMEOUT_S)

        await self.hub.connect(CHANNEL, ws, on_connect=on_connect, accept=accept)

    # ---- read / ack (REST) --------------------------------------------------------------------------

    async def mark_read(self, notification_id: int) -> bool:
        if await self._get(notification_id) is None:
            return False
        mem = self._rows.get(notification_id)
        if mem is not None and mem["read_at"] is None:
            mem["read_at"] = self.clock()
        if self._db_backed(notification_id):
            await writes.mark_notification_read(self.db, notification_id)
        return True

    async def mark_all_read(self, therapist_id: str | None = None) -> int:
        now = self.clock()
        marked = 0
        for row in self._rows.values():
            if row["read_at"] is None and (therapist_id is None or row["therapist_id"] == therapist_id):
                row["read_at"] = now
                marked += 1
        if self.db is not None:
            stored = await writes.mark_all_notifications_read(self.db, therapist_id)
            if stored is not None:
                return stored + sum(1 for r in self._local_only() if r["read_at"] == now)
        return marked

    async def ack(self, notification_id: int, acked_by: str) -> dict[str, Any] | None:
        row = await self._get(notification_id)
        if row is None:
            return None
        first_ack = row["acked_at"] is None
        now = self.clock()
        mem = self._rows.get(notification_id)
        for target in (row, mem):
            if target is not None:
                if target["acked_at"] is None:
                    target["acked_at"], target["acked_by"] = now, acked_by
                if target["read_at"] is None:
                    target["read_at"] = now
        if row["source_id"] is not None:
            self._acked_sources.add((row["source_table"], row["source_id"]))
        if self._db_backed(notification_id):
            await writes.mark_notification_ack(self.db, notification_id, acked_by)
            row = await self._get(notification_id) or row
        if first_ack and row["kind"] in _CALL_KINDS and row["source_table"] == "alerts":
            await self._push_ack_to_call(row, acked_by)
        return row

    async def _push_ack_to_call(self, row: dict[str, Any], acked_by: str) -> None:
        """§8.7.4: acking an alert tells the live call her therapist has seen it — once per incident."""
        guardian, notifier = self.guardian, self.notifier
        if guardian is None or notifier is None:
            try:
                state = get_state()
            except RuntimeError:  # no app (e.g. a unit test with no lifespan); nothing to push into
                return
            guardian, notifier = guardian or state.guardian, notifier or state.notifier
        alert_id = str(row["source_id"])
        if guardian is None or notifier is None or alert_id in self._call_pushed:
            return
        inc = next((i for i in guardian.incidents.values() if str(i.alert_id) == alert_id), None)
        if inc is None or inc.state != "escalated":  # no live call to push into
            return
        self._call_pushed.add(alert_id)
        name = inc.profile.therapist_name or acked_by
        text = f"{inc.profile.pronoun_poss.capitalize()} therapist {name} has seen the alert."
        try:
            await notifier.push_update(inc, text, status_text(guardian.live_snapshot(inc)))
        except Exception:  # noqa: BLE001 - acking must never fail because the call update did
            log.exception("push_update on ack failed", extra={"alert_id": inc.alert_id})

    def stats(self) -> dict[str, Any]:
        return {"rows_in_memory": len(self._order), "pending_resends": len(self._resend_tasks),
                "local_only": sum(1 for i in self._order if i < 0)}

    async def stop(self) -> None:
        tasks = list(self._resend_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---- module-level producer API (what every other feature imports) ----------------------------------

async def create(kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                 body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                 dedupe_key: str | None) -> int | None:
    """The only way to create a notification (§8.7.2). Routes to `state.notifications`."""
    service = get_state().notifications
    if service is None:
        log.warning("notifications.create called before the service was wired", extra={"kind": kind})
        return None
    return await service.create(kind, severity, therapist_id, patient_id, title, body, link, source_table,
                                source_id, dedupe_key)


def _step_kind_severity(step: str) -> tuple[str, str] | None:
    if step in _STEP_KIND_SEVERITY:
        return _STEP_KIND_SEVERITY[step]
    if step.startswith("call_") or step == "acknowledged":
        return "call_update", "urgent"
    return None


def _dedupe_key(alert_id: str, step: str, text: str | None) -> str:
    """One row per (alert, step), except live call updates: one per distinct update text."""
    key = f"alert:{alert_id}:{step}"
    if step == "call_update":
        key += ":" + hashlib.sha1((text or "").encode()).hexdigest()[:10]
    return key


async def on_alert_step(inc: Any, step: str, payload: dict[str, Any] | None = None) -> None:
    """Guardian `on_alert_step` hook (§8.7.2): escalation, call progress and false alarms become rows.

    `detected`, `checked_in`, `patient_responded`, `therapist_call`, `call_watch_timeout`, `webhook_*` and
    `closed` are not notified: they are either too early to matter or already covered by the steps above.
    The Guardian runs this as a background task, so awaiting the database here never blocks the safety path.
    """
    kind_severity = _step_kind_severity(step)
    if kind_severity is None:
        return
    kind, severity = kind_severity
    therapist_id = inc.profile.therapist_id
    if not therapist_id:  # notifications.therapist_id is NOT NULL
        return
    text = (payload or {}).get("text") if step == "call_update" else None
    body = mask_phone(str(text)) if text else None
    title = templates.alert_message(inc.kind, step, inc.first_name, inc.profile.pronoun_subj)
    link = f"/dashboard#/replay/{inc.session_id}?t={inc.started_t}" if inc.session_id else None
    await create(kind, severity, therapist_id, inc.patient_id, title, body, link, "alerts", inc.alert_id,
                 _dedupe_key(inc.alert_id, step, text))
