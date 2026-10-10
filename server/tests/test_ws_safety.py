"""Client messages on /ws/events: only §4.3 client types reach the bridge, rate-limited per client; and the
whole app wires the Guardian so /demo/trigger_fall drives the alert ladder."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from retell.lib.webhook_auth import symmetric

from server import main
from server.hub import RateLimiter
from server.state import get_state
from server.tests.conftest import AUTH, TOKEN


def read_until(ws: Any, pred: Callable[[dict[str, Any]], bool], limit: int = 400) -> dict[str, Any]:
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message not received")


def test_rate_limiter_refills_over_time() -> None:
    now = [0.0]
    limiter = RateLimiter(20, clock=lambda: now[0])
    assert sum(limiter.allow() for _ in range(50)) == 20 and limiter.dropped == 30
    now[0] = 0.5
    assert sum(limiter.allow() for _ in range(50)) == 10


def test_client_handler_drops_messages_over_20_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    accepted: list[dict[str, Any]] = []
    monkeypatch.setattr(main, "_on_client_event", accepted.append)
    handle = main._client_event_handler()
    for i in range(60):
        handle({"type": "hr", "bpm": 90 + i})
    assert 20 <= len(accepted) <= 21
    other = main._client_event_handler()  # each connection has its own budget
    other({"type": "hr", "bpm": 90})
    assert len(accepted) <= 22


def test_clients_cannot_inject_engine_events_but_can_report_pain(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    guardian = get_state().guardian
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        for kind in ("fall", "fall_status", "gesture", "alert", "paused"):
            ws.send_json({"type": kind, "stage": "confirmed", "name": "thumbs_up"})
        ws.send_json({"type": "command", "name": "pain", "payload": {"level": 8}})
        alert = read_until(ws, lambda m: m.get("type") == "alert")
        assert alert["kind"] == "pain" and alert["step"] == "detected"
    assert [inc.kind for inc in guardian.incidents.values()] == ["pain"]


def test_demo_trigger_fall_drives_the_guardian_to_escalation(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock", DEMO_TOOLS_ENABLED="1", CHECKIN_TIMEOUT_S="0.3")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        assert client.post("/demo/trigger_fall", headers=AUTH, json={}).status_code == 200
        steps: list[str] = []
        read_until(ws, lambda m: m.get("type") == "alert" and (steps.append(m["step"]) or m["step"] == "escalated"))
        assert steps == ["detected", "checked_in", "escalated"]
    state = get_state()
    [inc] = state.guardian.incidents.values()
    assert inc.escalation["mode"] == "log_only" and inc.escalation["contact_call_id"] == f"sim_{inc.alert_id}"
    assert state.notifier.stats()["escalations"] == 1


def test_signed_webhook_is_recorded_on_the_incident(make_client: Callable[..., TestClient]) -> None:
    key = "retell-test-key-abcdef123456"
    client = make_client(MIRA_SOURCE="mock", DEMO_TOOLS_ENABLED="1", CHECKIN_TIMEOUT_S="0.1", RETELL_API_KEY=key)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        client.post("/demo/trigger_fall", headers=AUTH, json={})
        escalated = read_until(ws, lambda m: m.get("type") == "alert" and m["step"] == "escalated")
        [inc] = get_state().guardian.incidents.values()
        body = json.dumps({"event": "call_started", "call": {"call_id": inc.escalation["contact_call_id"]}})
        sig = symmetric["sign"](body, key)
        assert client.post("/retell/webhook", content=body, headers={"X-Retell-Signature": sig}).status_code == 200
        step = read_until(ws, lambda m: m.get("type") == "alert" and m["step"].startswith("webhook_"))
        assert step["step"] == "webhook_call_started" and step["alert_id"] == escalated["alert_id"]
