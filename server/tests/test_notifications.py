"""Dashboard notification backend (§8.7): dedupe, policy, resend, REST, WS replay, ack → push_update."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from server.agents.safety_guardian import Incident
from server.notify import notifications as notifications_mod
from server.notify.notifications import NotificationService
from server.notify.policy import NotificationPolicy
from server.state import PatientProfile, get_state
from server.tests.conftest import AUTH, TOKEN

CHI = ZoneInfo("America/Chicago")


def chicago(hour: int, minute: int = 0, day: int = 8) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=CHI)


class FakeHub:
    """Records publish() calls per channel; no clients, no sockets."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, channel: str, msg: dict[str, Any]) -> None:
        assert channel == "notifications"
        self.published.append(msg)


def profile(**overrides: Any) -> PatientProfile:
    base = dict(patient_id="maya", first_name="Maya", pronoun_subj="she", pronoun_obj="her",
               pronoun_poss="her", phone="+15550100999", contact_name="Linh", contact_phone="+15550100111",
               therapist_id="okafor", therapist_name="Dr. Okafor", therapist_phone="+15550100222",
               condition="right knee replacement", affected_side="right", hr_limit=130,
               weeks_since_surgery=3, pain_today=None, falls_30d=0)
    base.update(overrides)
    return PatientProfile(**base)


def make_service(*, now: float = 1_700_000_000.0, resend_min: float = 5.0, **kwargs: Any
                 ) -> tuple[NotificationService, FakeHub]:
    hub = FakeHub()
    clock = [now]
    service = NotificationService(hub, None, policy=NotificationPolicy(), resend_min=resend_min,
                                  clock=lambda: clock[0], **kwargs)
    service._clock_box = clock  # type: ignore[attr-defined]  # test helper to advance time
    return service, hub


async def _create(service: NotificationService, **overrides: Any) -> int | None:
    kwargs: dict[str, Any] = dict(kind="milestone", severity="low", therapist_id="okafor", patient_id="maya",
                                  title="t", body=None, link=None, source_table=None, source_id=None,
                                  dedupe_key=None)
    kwargs.update(overrides)
    return await service.create(**kwargs)


# ---- create(): dedupe, in-memory id sequence without a database -----------------------------------

async def test_create_works_without_a_database_and_assigns_in_memory_ids() -> None:
    service, hub = make_service()
    first = await _create(service, dedupe_key="a")
    second = await _create(service, dedupe_key="b")
    assert first == 1 and second == 2
    assert len(hub.published) == 2
    assert hub.published[0]["type"] == "notification" and hub.published[0]["id"] == 1


async def test_dedupe_key_never_notifies_twice() -> None:
    service, hub = make_service()
    first = await _create(service, dedupe_key="alert:x:escalated")
    second = await _create(service, dedupe_key="alert:x:escalated")
    assert first is not None and second is None
    assert len(hub.published) == 1


async def test_rows_with_no_dedupe_key_are_never_suppressed() -> None:
    service, _ = make_service()
    assert await _create(service, dedupe_key=None) == 1
    assert await _create(service, dedupe_key=None) == 2


# ---- policy: quiet hours, urgent bypass, daily rate limit ------------------------------------------

async def test_urgent_pushes_during_quiet_hours() -> None:
    now = chicago(23, 0).timestamp()
    service, hub = make_service(now=now)
    await _create(service, kind="alert", severity="urgent", dedupe_key="k1")
    assert hub.published[0]["push"] is True


async def test_non_urgent_does_not_push_during_quiet_hours_but_the_row_is_still_stored() -> None:
    now = chicago(23, 0).timestamp()
    service, hub = make_service(now=now)
    notification_id = await _create(service, kind="milestone", severity="low", dedupe_key="k2")
    assert notification_id is not None  # the row is stored even though it doesn't push
    assert hub.published[0]["push"] is False


async def test_at_most_one_non_urgent_push_per_patient_per_day() -> None:
    now = chicago(12, 0).timestamp()
    service, hub = make_service(now=now)
    await _create(service, kind="milestone", severity="low", patient_id="maya", dedupe_key="m1")
    await _create(service, kind="proposal", severity="normal", patient_id="maya", dedupe_key="m2")
    assert hub.published[0]["push"] is True
    assert hub.published[1]["push"] is False  # daily budget already spent
    # a different patient still gets their first push today
    await _create(service, kind="milestone", severity="low", patient_id="other", dedupe_key="m3")
    assert hub.published[2]["push"] is True


# ---- urgent resend ----------------------------------------------------------------------------------

async def test_urgent_unacked_notification_is_resent_after_resend_min() -> None:
    service, hub = make_service(resend_min=0.001)  # 60 ms
    await _create(service, kind="alert", severity="urgent", dedupe_key="r1")
    assert len(hub.published) == 1
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(hub.published) > 1:
            break
    assert len(hub.published) >= 2
    assert hub.published[1]["resend"] is True
    await service.stop()


async def test_acked_urgent_notification_is_not_resent() -> None:
    service, hub = make_service(resend_min=0.001)
    notification_id = await _create(service, kind="alert", severity="urgent", dedupe_key="r2")
    assert notification_id is not None
    await service.ack(notification_id, "dr_okafor")
    await asyncio.sleep(0.15)
    assert len(hub.published) == 1  # the create, no resend
    await service.stop()


# ---- reads: list / count ------------------------------------------------------------------------

async def test_list_unread_and_since_id_filters() -> None:
    service, _ = make_service()
    for i in range(5):
        await _create(service, dedupe_key=f"d{i}")
    await service.mark_read(1)
    rows = service.list_notifications(unread=True)
    assert [r["id"] for r in rows] == [5, 4, 3, 2]  # newest first, id 1 excluded
    since = service.list_notifications(since_id=2)
    assert [r["id"] for r in since] == [3, 4, 5]  # ascending, catch-up order


async def test_count_unread_and_urgent_unacked() -> None:
    service, _ = make_service()
    await _create(service, kind="alert", severity="urgent", dedupe_key="u1")
    await _create(service, kind="milestone", severity="low", dedupe_key="u2")
    counts = service.count()
    assert counts == {"unread": 2, "urgent_unacked": 1}
    await service.mark_all_read()
    counts = service.count()
    assert counts["unread"] == 0 and counts["urgent_unacked"] == 1  # read != acked


# ---- ack: records acked_by, pushes into the live call --------------------------------------------

class FakeNotifier:
    def __init__(self) -> None:
        self.pushed: list[tuple[Any, str, str]] = []

    async def push_update(self, inc: Any, text: str, status: str) -> bool:
        self.pushed.append((inc, text, status))
        return True


def open_incident(alert_id: str) -> Incident:
    return Incident(alert_id=alert_id, kind="fall", session_id="s1", profile=profile(),
                    started_t=time.time(), trigger={}, state="escalated")


async def test_ack_records_acked_by_and_pushes_update_on_an_open_incident() -> None:
    inc = open_incident("alert-1")
    notifier = FakeNotifier()

    class FakeGuardian:
        incidents = {"s1": inc}

        def live_snapshot(self, _inc: Any) -> dict[str, Any]:
            return {"posture": None, "moving": None, "still_s": None, "hr": None}

    service, _ = make_service(notifier=notifier, guardian=FakeGuardian())
    notification_id = await _create(service, kind="alert", severity="urgent", source_table="alerts",
                                    source_id="alert-1", dedupe_key="ack1")
    assert notification_id is not None
    row = await service.ack(notification_id, "dr_okafor")
    assert row is not None and row["acked_by"] == "dr_okafor" and row["acked_at"] is not None
    assert len(notifier.pushed) == 1
    pushed_inc, text, _status = notifier.pushed[0]
    assert pushed_inc is inc and "has seen the alert" in text


async def test_ack_on_a_non_alert_kind_never_touches_the_call() -> None:
    notifier = FakeNotifier()
    service, _ = make_service(notifier=notifier, guardian=object())
    notification_id = await _create(service, kind="milestone", severity="low", dedupe_key="ack2")
    assert notification_id is not None
    await service.ack(notification_id, "dr_okafor")
    assert notifier.pushed == []


async def test_ack_with_no_matching_open_incident_does_not_raise() -> None:
    notifier = FakeNotifier()

    class EmptyGuardian:
        incidents: dict[str, Any] = {}

    service, _ = make_service(notifier=notifier, guardian=EmptyGuardian())
    notification_id = await _create(service, kind="alert", severity="urgent", source_table="alerts",
                                    source_id="no-such-alert", dedupe_key="ack3")
    assert notification_id is not None
    row = await service.ack(notification_id, "dr_okafor")
    assert row is not None
    assert notifier.pushed == []


async def test_ack_unknown_id_returns_none() -> None:
    service, _ = make_service()
    assert await service.ack(999, "dr_okafor") is None


# ---- Guardian on_alert_step producer -------------------------------------------------------------

async def test_on_alert_step_creates_a_notification_for_escalated(monkeypatch: pytest.MonkeyPatch) -> None:
    service, hub = make_service()

    class State:
        notifications = service

    monkeypatch.setattr(notifications_mod, "get_state", lambda: State())
    inc = open_incident(str(uuid.uuid4()))
    await notifications_mod.on_alert_step(inc, "escalated", {})
    assert len(hub.published) == 1
    row = hub.published[0]
    assert row["kind"] == "alert" and row["severity"] == "urgent"
    assert row["dedupe_key"] == f"alert:{inc.alert_id}:escalated"
    assert row["therapist_id"] == "okafor" and row["patient_id"] == "maya"
    assert row["link"] == f"/dashboard#/replay/s1?t={inc.started_t}"


async def test_on_alert_step_ignores_steps_outside_the_mapped_set(monkeypatch: pytest.MonkeyPatch) -> None:
    service, hub = make_service()

    class State:
        notifications = service

    monkeypatch.setattr(notifications_mod, "get_state", lambda: State())
    inc = open_incident("x")
    for step in ("detected", "checked_in", "patient_responded", "therapist_call", "closed", "webhook_call_ended"):
        await notifications_mod.on_alert_step(inc, step, {})
    assert hub.published == []


async def test_on_alert_step_maps_call_steps_to_call_update_urgent(monkeypatch: pytest.MonkeyPatch) -> None:
    service, hub = make_service()

    class State:
        notifications = service

    monkeypatch.setattr(notifications_mod, "get_state", lambda: State())
    inc = open_incident("y")
    await notifications_mod.on_alert_step(inc, "call_ongoing", {})
    assert hub.published[0]["kind"] == "call_update" and hub.published[0]["severity"] == "urgent"


async def test_on_alert_step_maps_resolved_to_low_severity_false_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    service, hub = make_service()

    class State:
        notifications = service

    monkeypatch.setattr(notifications_mod, "get_state", lambda: State())
    inc = open_incident("z")
    await notifications_mod.on_alert_step(inc, "resolved", {})
    assert hub.published[0]["kind"] == "alert" and hub.published[0]["severity"] == "low"


# ---- app-level: REST + WS, token required, replay after reconnect (since_id) ----------------------

def test_notifications_rest_requires_token(make_client: Any) -> None:
    client: TestClient = make_client()
    assert client.get("/notifications").status_code == 401
    assert client.get("/notifications/count").status_code == 401
    assert client.post("/notifications/1/read").status_code == 401
    assert client.post("/notifications/read-all").status_code == 401
    assert client.post("/notifications/1/ack", json={"therapist_id": "x"}).status_code == 401


def test_notifications_ws_requires_token(make_client: Any) -> None:
    client: TestClient = make_client()
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on the rejected handshake
        with client.websocket_connect("/ws/notifications"):
            pass


def test_notifications_count_and_read_rest(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    nid = asyncio.run(_create(service, kind="alert", severity="urgent", therapist_id="okafor",
                              dedupe_key="restA"))
    assert client.get("/notifications/count", headers=AUTH).json() == {"unread": 1, "urgent_unacked": 1}
    r = client.post(f"/notifications/{nid}/read", headers=AUTH)
    assert r.status_code == 200 and r.json()["read"] is True
    assert client.get("/notifications/count", headers=AUTH).json()["unread"] == 0
    assert client.post("/notifications/9999/read", headers=AUTH).status_code == 404


def test_notifications_read_all_and_list(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    for i in range(3):
        asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="okafor",
                            dedupe_key=f"restB{i}"))
    r = client.post("/notifications/read-all", json={"therapist_id": "okafor"}, headers=AUTH)
    assert r.json()["marked_read"] == 3
    rows = client.get("/notifications?unread=true", headers=AUTH).json()["notifications"]
    assert rows == []


def test_notifications_ack_rest_records_acked_by(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    nid = asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="okafor",
                              dedupe_key="restC"))
    r = client.post(f"/notifications/{nid}/ack", json={"therapist_id": "dr_okafor"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["acked_by"] == "dr_okafor" and body["acked_at"] is not None
    assert client.post("/notifications/9999/ack", json={"therapist_id": "x"}, headers=AUTH).status_code == 404


def test_ws_notifications_replays_missed_rows_after_reconnect_with_since_id(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    ids = [asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="okafor",
                               dedupe_key=f"wsA{i}")) for i in range(4)]
    since = ids[1]  # client already has the first two; reconnect should only replay the rest
    with client.websocket_connect(f"/ws/notifications?token={TOKEN}&since_id={since}") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert [first["id"], second["id"]] == ids[2:]


def test_ws_notifications_streams_new_rows_live(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    with client.websocket_connect(f"/ws/notifications?token={TOKEN}") as ws:
        nid = asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="okafor",
                                  dedupe_key="wsLive"))
        msg = ws.receive_json()
    assert msg["type"] == "notification" and msg["id"] == nid


def test_ws_notifications_scopes_replay_to_a_therapist_id(make_client: Any) -> None:
    client: TestClient = make_client()
    service = get_state().notifications
    asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="okafor", dedupe_key="scopeA"))
    asyncio.run(_create(service, kind="milestone", severity="low", therapist_id="other", dedupe_key="scopeB"))
    with client.websocket_connect(f"/ws/notifications?token={TOKEN}&therapist_id=other") as ws:
        msg = ws.receive_json()
    assert msg["therapist_id"] == "other"
