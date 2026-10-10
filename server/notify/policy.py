"""Notification policy (§8.5): who gets what, quiet hours, rate limits. Pure rules plus small in-memory state.

- urgent (unanswered fall, pain ≥ 7, HR over limit): Retell call to the emergency contact + dashboard, always now.
- milestone / proposal / data_guard: dashboard card now; a push (toast/pop-up) only outside quiet hours
  and at most once per patient per local day.
- everything else (digest): into the daily digest.
Quiet hours are 9 PM–8 AM in cfg.TZ_NAME, except urgent. Calls: at most one per incident per person,
plus one retry if the first did not connect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from server.config import cfg

URGENT = "urgent"
MILESTONE = "milestone"
PROPOSAL = "proposal"
DATA_GUARD = "data_guard"
DIGEST = "digest"
KINDS = (URGENT, MILESTONE, PROPOSAL, DATA_GUARD, DIGEST)

QUIET_START = time(21, 0)
QUIET_END = time(8, 0)
MAX_NON_URGENT_PUSHES_PER_DAY = 1
MAX_CALL_RETRIES = 1


@dataclass(frozen=True)
class Decision:
    channels: tuple[str, ...]  # "call" | "dashboard" | "digest"
    push: bool  # show a desktop pop-up / toast now
    reason: str


def _local(now: datetime, tz_name: str | None = None) -> datetime:
    tz = ZoneInfo(tz_name or cfg.TZ_NAME)
    return now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)


def is_quiet_hours(now: datetime, tz_name: str | None = None) -> bool:
    t = _local(now, tz_name).time()
    return t >= QUIET_START or t < QUIET_END


def channels_for(kind: str) -> tuple[str, ...]:
    if kind == URGENT:
        return ("call", "dashboard")
    if kind in (MILESTONE, PROPOSAL, DATA_GUARD):
        return ("dashboard",)
    return ("digest",)


class NotificationPolicy:
    """Routing plus the per-patient daily push budget (in memory; resets with the process)."""

    def __init__(self, tz_name: str | None = None) -> None:
        self.tz_name = tz_name
        self._pushes: dict[tuple[str, date], int] = {}

    def decide(self, kind: str, patient_id: str, now: datetime) -> Decision:
        channels = channels_for(kind if kind in KINDS else DIGEST)
        if kind == URGENT:
            return Decision(channels, True, "urgent")
        if "dashboard" not in channels:
            return Decision(channels, False, "digest")
        if is_quiet_hours(now, self.tz_name):
            return Decision(channels, False, "quiet_hours")
        day = _local(now, self.tz_name).date()
        if self._pushes.get((patient_id, day), 0) >= MAX_NON_URGENT_PUSHES_PER_DAY:
            return Decision(channels, False, "daily_limit")
        return Decision(channels, True, "ok")

    def record_push(self, patient_id: str, now: datetime) -> None:
        key = (patient_id, _local(now, self.tz_name).date())
        self._pushes[key] = self._pushes.get(key, 0) + 1

    def notify(self, kind: str, patient_id: str, now: datetime) -> Decision:
        """decide() and, if it pushes a non-urgent notification, count it against today's budget."""
        decision = self.decide(kind, patient_id, now)
        if decision.push and kind != URGENT:
            self.record_push(patient_id, now)
        return decision


class CallLedger:
    """Never more than one call per incident per person, plus one retry if it did not connect."""

    def __init__(self) -> None:
        self._calls: dict[tuple[str, str], int] = {}

    def may_call(self, alert_id: str, role: str, *, retry: bool = False) -> bool:
        placed = self._calls.get((alert_id, role), 0)
        return placed == 0 if not retry else 1 <= placed <= MAX_CALL_RETRIES

    def record(self, alert_id: str, role: str) -> None:
        self._calls[(alert_id, role)] = self._calls.get((alert_id, role), 0) + 1
