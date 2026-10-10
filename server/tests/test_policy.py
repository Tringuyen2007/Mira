from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from server.notify.policy import (DATA_GUARD, DIGEST, MILESTONE, PROPOSAL, URGENT, CallLedger, NotificationPolicy,
                                  channels_for, is_quiet_hours)

CHI = ZoneInfo("America/Chicago")


def at(hour: int, minute: int = 0, day: int = 8) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=CHI)


@pytest.mark.parametrize("hour, minute, quiet", [(20, 59, False), (21, 0, True), (23, 30, True), (0, 0, True),
                                                 (7, 59, True), (8, 0, False), (12, 0, False)])
def test_quiet_hours_are_9pm_to_8am_chicago(hour: int, minute: int, quiet: bool) -> None:
    assert is_quiet_hours(at(hour, minute)) is quiet


def test_quiet_hours_use_chicago_time_for_utc_input() -> None:
    assert is_quiet_hours(datetime(2026, 10, 9, 3, 0, tzinfo=UTC)) is True  # 10 PM CDT
    assert is_quiet_hours(datetime(2026, 10, 8, 18, 0, tzinfo=UTC)) is False  # 1 PM CDT


def test_routing_by_kind() -> None:
    assert channels_for(URGENT) == ("call", "dashboard")
    for kind in (MILESTONE, PROPOSAL, DATA_GUARD):
        assert channels_for(kind) == ("dashboard",)
    assert channels_for(DIGEST) == ("digest",) and channels_for("adherence_trend") == ("digest",)


def test_urgent_ignores_quiet_hours_and_rate_limits() -> None:
    policy = NotificationPolicy()
    for _ in range(3):
        decision = policy.notify(URGENT, "maya", at(2))
        assert decision.push and decision.channels == ("call", "dashboard") and decision.reason == "urgent"


def test_non_urgent_waits_out_quiet_hours() -> None:
    decision = NotificationPolicy().notify(MILESTONE, "maya", at(22))
    assert decision.push is False and decision.reason == "quiet_hours" and decision.channels == ("dashboard",)


def test_one_non_urgent_push_per_patient_per_day() -> None:
    policy = NotificationPolicy()
    assert policy.notify(MILESTONE, "maya", at(9)).push is True
    second = policy.notify(PROPOSAL, "maya", at(10))
    assert second.push is False and second.reason == "daily_limit" and second.channels == ("dashboard",)
    assert policy.notify(DATA_GUARD, "ben", at(10)).push is True  # other patients have their own budget
    assert policy.notify(MILESTONE, "maya", at(9, day=9)).push is True  # next local day


def test_digest_never_pushes() -> None:
    decision = NotificationPolicy().notify(DIGEST, "maya", at(12))
    assert decision.push is False and decision.channels == ("digest",)


def test_one_call_per_incident_per_person_plus_one_retry() -> None:
    ledger = CallLedger()
    assert ledger.may_call("a1", "emergency_contact")
    assert not ledger.may_call("a1", "emergency_contact", retry=True)  # nothing to retry yet
    ledger.record("a1", "emergency_contact")
    assert not ledger.may_call("a1", "emergency_contact")
    assert ledger.may_call("a1", "emergency_contact", retry=True)
    ledger.record("a1", "emergency_contact")
    assert not ledger.may_call("a1", "emergency_contact", retry=True)
    assert ledger.may_call("a1", "therapist") and ledger.may_call("a2", "emergency_contact")
