"""Dashboard notification backend (§8.7): the single place that turns an alert, call step, proposal,
milestone, Insight note, Coach adaptation or Data Guard failure into a therapist notification.

`create()` applies the §8.5 policy (quiet hours, the daily non-urgent push budget), stores the row
(`writes.insert_notification`, `ON CONFLICT (dedupe_key) DO NOTHING`) and publishes it on the hub's
"notifications" channel. It works with **or without** a database: rows and their ids live in an
in-memory ring buffer, because the server is exactly one process (§ global constraints) and the hub
already *is* the real-time fan-out — a database row, when one exists, is only an audit trail.

Every other feature reaches this module through a lazy import and calls the module-level `create()`;
that is the whole producer API, exactly as fixed in the plan's Interfaces section. This module also
supplies the Guardian `on_alert_step` hook that turns the safety path into notifications.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.agents.incident_snapshot import status_text
from server.config import cfg
from server.db import writes
from server.db.pool import Database
from server.logging_setup import get_logger
from server.notify import policy as policy_mod
from server.notify import templates
from server.state import get_state

log = get_logger("notifications")

MAX_HISTORY = 500  # in-memory ring for REST reads and WS replay (one process; see module docstring)

_KIND_TO_POLICY = {"milestone": policy_mod.MILESTONE, "proposal": policy_mod.PROPOSAL,
                   "data_guard": policy_mod.DATA_GUARD}

# Guardian step (§7.2/§8.6) -> (notification kind, severity). Everything else is not notified.
_STEP_KIND_SEVERITY = {"escalated": ("alert", "urgent"), "resolved": ("alert", "low")}


def _policy_kind(kind: str, severity: str) -> str:
    """How §8.5 routes this row: severity decides urgency; otherwise map by notification kind."""
    if severity == policy_mod.URGENT:
        return policy_mod.URGENT
    return _KIND_TO_POLICY.get(kind, policy_mod.DIGEST)


def _local_dt(epoch_s: float) -> datetime:
    return datetime.fromtimestamp(epoch_s, tz=UTC)


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
        self._next_id = 1
        self._dedupe_seen: set[str] = set()
        self._resend_tasks: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # ---- producer API ------------------------------------------------------------------------------

    async def create(self, kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                     body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                     dedupe_key: str | None) -> int | None:
        async with self._lock:
            if dedupe_key is not None and dedupe_key in self._dedupe_seen:
                return None
            now = self.clock()
            decision = self.policy.notify(_policy_kind(kind, severity), patient_id or "", _local_dt(now))
            row_id = await self._insert(kind, severity, therapist_id, patient_id, title, body, link,
                                        source_table, source_id, dedupe_key)
            if row_id is None:
                return None
            if dedupe_key is not None:
                self._dedupe_seen.add(dedupe_key)
        row = {"id": row_id, "time": now, "kind": kind, "severity": severity, "therapist_id": therapist_id,
               "patient_id": patient_id, "title": title, "body": body, "link": link,
               "source_table": source_table, "source_id": source_id, "dedupe_key": dedupe_key,
               "read_at": None, "acked_at": None, "acked_by": None, "push": decision.push}
        self._remember(row)
        self.hub.publish("notifications", {"type": "notification", **row})
        log.info("notification created", extra={"notification_id": row_id, "kind": kind, "severity": severity,
                                                "push": decision.push, "reason": decision.reason})
        if severity == policy_mod.URGENT:
            self._schedule_resend(row_id)
        return row_id

    async def _insert(self, kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                      body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                      dedupe_key: str | None) -> int | None:
        if self.db is not None:
            return await writes.insert_notification(self.db, kind, severity, therapist_id, patient_id, title,
                                                     body, link, source_table, source_id, dedupe_key)
        row_id = self._next_id  # in-memory id sequence when there is no database
        self._next_id += 1
        return row_id

    def _remember(self, row: dict[str, Any]) -> None:
        self._rows[row["id"]] = row
        self._order.append(row["id"])
        if len(self._order) > MAX_HISTORY:
            self._rows.pop(self._order.pop(0), None)

    # ---- urgent resend (§8.7.5) --------------------------------------------------------------------

    def _schedule_resend(self, row_id: int) -> None:
        if self.resend_min <= 0:
            return
        task = asyncio.get_running_loop().create_task(self._resend_loop(row_id),
                                                       name=f"notify-resend-{row_id}")
        self._resend_tasks[row_id] = task
        task.add_done_callback(lambda _t: self._resend_tasks.pop(row_id, None))

    async def _resend_loop(self, row_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(self.resend_min * 60)
                row = self._rows.get(row_id)
                if row is None or row["acked_at"] is not None:
                    return
                log.info("resending unacked urgent notification", extra={"notification_id": row_id})
                self.hub.publish("notifications", {"type": "notification", "resend": True, **row})
        except asyncio.CancelledError:
            raise

    # ---- reads (REST panel + WS replay) ------------------------------------------------------------

    def list_notifications(self, *, therapist_id: str | None = None, unread: bool = False,
                           since_id: int | None = None, limit: int | None = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row_id in self._order:
            row = self._rows[row_id]
            if therapist_id is not None and row["therapist_id"] != therapist_id:
                continue
            if unread and row["read_at"] is not None:
                continue
            if since_id is not None and row_id <= since_id:
                continue
            rows.append(row)
        if since_id is not None:  # reconnect replay / polling fallback: oldest-missed-first, no gaps
            return rows[-limit:] if limit else rows
        rows.reverse()  # panel list: newest first
        return rows[:limit] if limit else rows

    def count(self, therapist_id: str | None = None) -> dict[str, int]:
        unread = urgent_unacked = 0
        for row_id in self._order:
            row = self._rows[row_id]
            if therapist_id is not None and row["therapist_id"] != therapist_id:
                continue
            unread += row["read_at"] is None
            urgent_unacked += row["severity"] == policy_mod.URGENT and row["acked_at"] is None
        return {"unread": unread, "urgent_unacked": urgent_unacked}

    # ---- read / ack (REST) --------------------------------------------------------------------------

    async def mark_read(self, notification_id: int) -> bool:
        row = self._rows.get(notification_id)
        if row is None:
            return False
        if row["read_at"] is None:
            row["read_at"] = self.clock()
        writes.fire(writes.mark_notification_read(self.db, notification_id))
        return True

    async def mark_all_read(self, therapist_id: str | None = None) -> int:
        now = self.clock()
        marked = 0
        for row_id in self._order:
            row = self._rows[row_id]
            if row["read_at"] is None and (therapist_id is None or row["therapist_id"] == therapist_id):
                row["read_at"] = now
                marked += 1
        if therapist_id is not None:  # the fixed write needs one therapist_id; scoped calls only
            writes.fire(writes.mark_all_notifications_read(self.db, therapist_id))
        return marked

    async def ack(self, notification_id: int, acked_by: str) -> dict[str, Any] | None:
        row = self._rows.get(notification_id)
        if row is None:
            return None
        now = self.clock()
        if row["acked_at"] is None:
            row["acked_at"] = now
            row["acked_by"] = acked_by
        if row["read_at"] is None:
            row["read_at"] = now
        writes.fire(writes.mark_notification_ack(self.db, notification_id, acked_by))
        if row["kind"] in ("alert", "call_update"):
            await self._push_ack_to_call(row, acked_by)
        return dict(row)

    async def _push_ack_to_call(self, row: dict[str, Any], acked_by: str) -> None:
        """§8.7.4: acking an alert-kind notification tells the live call her therapist has seen it."""
        guardian, notifier = self.guardian, self.notifier
        if guardian is None or notifier is None:
            try:
                state = get_state()
            except RuntimeError:  # no app (e.g. a unit test with no lifespan); nothing to push into
                return
            guardian, notifier = guardian or state.guardian, notifier or state.notifier
        if guardian is None or notifier is None or row.get("source_id") is None:
            return
        inc = next((i for i in guardian.incidents.values() if str(i.alert_id) == str(row["source_id"])), None)
        if inc is None or inc.state != "escalated":  # no live call to push into
            return
        name = inc.profile.therapist_name or acked_by
        text = f"{inc.profile.pronoun_poss.capitalize()} therapist {name} has seen the alert."
        try:
            await notifier.push_update(inc, text, status_text(guardian.live_snapshot(inc)))
        except Exception:  # noqa: BLE001 - acking must never fail because the call update did
            log.exception("push_update on ack failed", extra={"alert_id": inc.alert_id})

    def stats(self) -> dict[str, Any]:
        return {"rows": len(self._order), "pending_resends": len(self._resend_tasks)}

    async def stop(self) -> None:
        tasks = list(self._resend_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


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


async def on_alert_step(inc: Any, step: str, payload: dict[str, Any] | None = None) -> None:
    """Guardian `on_alert_step` hook (§8.7.2): escalation, call progress and false alarms become rows.

    `detected`, `checked_in`, `patient_responded`, `therapist_call`, `call_watch_timeout`, `webhook_*` and
    `closed` are not notified: they are either too early to matter or already covered by the steps above.
    """
    kind_severity = _step_kind_severity(step)
    if kind_severity is None:
        return
    kind, severity = kind_severity
    therapist_id = inc.profile.therapist_id
    if not therapist_id:  # notifications.therapist_id is NOT NULL
        return
    title = templates.alert_message(inc.kind, step, inc.first_name, inc.profile.pronoun_subj)
    link = f"/dashboard#/replay/{inc.session_id}?t={inc.started_t}" if inc.session_id else None
    await create(kind, severity, therapist_id, inc.patient_id, title, None, link, "alerts", inc.alert_id,
                f"alert:{inc.alert_id}:{step}")
