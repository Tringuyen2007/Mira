"""RetellNotifier: the log-only simulator end to end, and the live path against a fake SDK client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.agents.safety_guardian import SafetyGuardian
from server.config import cfg, reload_config
from server.logging_setup import setup_logging, shutdown_logging
from server.notify.retell_client import RetellNotifier
from server.state import SessionRegistry
from server.tests.test_guardian import FakeBridge, FakeHub, ev, fall, until

CONTACT_PHONE = "+15550100111"  # conftest DEMO_EMERGENCY_PHONE
THERAPIST_PHONE = "+15550100222"


class FakeCalls:
    """Stands in for `Retell().call`: scripted statuses per call id, records every request."""

    def __init__(self, script: dict[str, list[tuple[str, Any]]], fail_creates: int = 0) -> None:
        self.script = script
        self.fail_creates = fail_creates
        self.created: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.polls: dict[str, int] = {}

    def create_phone_call(self, **kwargs: Any) -> Any:
        if self.fail_creates:
            self.fail_creates -= 1
            raise ConnectionError("temporary")
        self.created.append(kwargs)
        return SimpleNamespace(call_id=f"call_{len(self.created)}")

    def retrieve(self, call_id: str) -> Any:
        steps = self.script[call_id]
        i = min(self.polls.get(call_id, 0), len(steps) - 1)
        self.polls[call_id] = i + 1
        status, extra = steps[i]
        analysis = None
        if extra is not None:
            analysis = SimpleNamespace(custom_analysis_data=extra.get("data"), in_voicemail=extra.get("voicemail", False))
        return SimpleNamespace(call_status=status, call_analysis=analysis,
                               disconnection_reason=(extra or {}).get("reason", ""))

    def update_live(self, call_id: str, **kwargs: Any) -> Any:
        self.updates.append((call_id, kwargs))
        return SimpleNamespace()


async def make(notifier: RetellNotifier, timeout_s: float = 0.02) -> tuple[SafetyGuardian, FakeHub, FakeBridge, str,
                                                                          list[str]]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    hub, bridge = FakeHub(), FakeBridge()
    steps: list[str] = []
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, timeout_s, update_interval_s=0.2,
                              alert_hooks=[lambda inc, step, payload: steps.append(step)])
    return guardian, hub, bridge, session.session_id, steps


def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLS_DISABLED", "0")
    monkeypatch.setenv("RETELL_API_KEY", "key_test_not_real_0123456789")
    monkeypatch.setenv("RETELL_FROM_NUMBER", "+15550100999")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_test")
    reload_config()


async def test_simulated_call_runs_the_whole_ladder_and_closes_the_incident() -> None:
    notifier = RetellNotifier(sim_duration_s=0.5, sim_ring_s=0.1, poll_interval_s=0.05)
    guardian, hub, bridge, sid, steps = await make(notifier)
    assert notifier.mode == "log_only"
    await guardian.on_event(fall(sid))
    await guardian.on_event(fall(sid))
    await until(lambda: "call_ongoing" in steps)
    inc = guardian.incidents[sid]
    assert inc.escalation is not None and inc.escalation["contact_call_id"] == f"sim_{inc.alert_id}"
    assert inc.escalation["dynamic_variables"]["contact_name"] == "Linh"
    await guardian.on_event(ev(sid, "gesture", name="thumbs_up"))
    await until(lambda: "closed" in steps, timeout=3)
    assert steps == ["detected", "checked_in", "escalated", "call_registered", "call_ongoing", "patient_responded",
                     "call_update", "call_ended", "closed"]
    assert bridge.to_engine == [{"type": "incident_closed"}]
    assert sid not in guardian.incidents
    update = next(e for e in hub.of("alert") if e["step"] == "call_update")
    assert update["call_id"] == inc.call_id
    assert notifier.stats()["escalations"] == 1


async def test_escalate_is_idempotent_per_alert() -> None:
    notifier = RetellNotifier(sim_duration_s=5, poll_interval_s=0.05)
    guardian, _, _, sid, _ = await make(notifier, timeout_s=60)
    await guardian.on_event(fall(sid))
    inc = guardian.incidents[sid]
    inc.snapshot = guardian.live_snapshot(inc)
    first = await notifier.escalate(inc)
    second = await notifier.escalate(inc)
    assert first is second and len(notifier._sims) == 1
    await notifier.stop()
    await guardian.stop()


async def test_log_only_logs_masked_variables_and_the_rendered_opening() -> None:
    setup_logging()
    try:
        notifier = RetellNotifier(sim_duration_s=0.1, sim_ring_s=0.05, poll_interval_s=0.05)
        guardian, _, _, sid, steps = await make(notifier)
        await guardian.on_event(fall(sid))
        await until(lambda: "closed" in steps, timeout=3)
    finally:
        shutdown_logging()
    text = (Path(cfg.LOG_DIR) / "server.log").read_text(encoding="utf-8")
    assert CONTACT_PHONE not in text and THERAPIST_PHONE not in text and "0100111" not in text
    lines = [json.loads(line) for line in text.splitlines()]
    sim = next(e for e in lines if e["msg"].startswith("CALLS_DISABLED: simulating Retell call"))
    assert sim["to"] == "***0111" and sim["retell_llm_dynamic_variables"]["therapist_phone"] == "***0222"
    assert sim["opening"].startswith("Hi Linh, this is Mira") and "{{" not in sim["opening"]
    assert "{{" not in sim["voicemail"]
    steps_logged = [e["step"] for e in lines if e.get("component") == "guardian" and "step" in e]
    assert steps_logged[:3] == ["detected", "checked_in", "escalated"] and steps_logged[-1] == "closed"
    assert all(e.get("alert_id") for e in lines if e.get("component") == "guardian")


async def test_live_mode_places_one_call_with_the_exact_sdk_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    live_env(monkeypatch)
    calls = FakeCalls({"call_1": [("registered", None), ("ongoing", None), ("ongoing", None), ("ongoing", None),
                                  ("ended", None), ("ended", {"data": {"will_check_on_patient": True,
                                                                      "contact_reached": True,
                                                                      "notes": "Going over now."}})]},
                      fail_creates=1)
    notifier = RetellNotifier(client=SimpleNamespace(call=calls), poll_interval_s=0.05, retry_delays_s=(0, 0.01, 0.01))
    guardian, _, _, sid, steps = await make(notifier)
    assert notifier.mode == "live"
    await guardian.on_event(fall(sid))
    await until(lambda: "call_ongoing" in steps)
    inc = guardian.incidents[sid]
    await guardian.on_event(ev(sid, "command", name="im_ok", payload={}))
    await until(lambda: "closed" in steps, timeout=3)

    assert len(calls.created) == 1
    created = calls.created[0]
    assert created["from_number"] == "+15550100999" and created["to_number"] == CONTACT_PHONE
    assert created["override_agent_id"] == "agent_test"
    assert created["idempotency_key"] == f"{inc.alert_id}:emergency_contact"
    assert created["metadata"] == {"alert_id": inc.alert_id, "role": "emergency_contact"}
    assert all(isinstance(v, str) and v for v in created["retell_llm_dynamic_variables"].values())
    assert "error_emergency_contact_0" in inc.escalation  # the first attempt failed and was retried

    call_id, update = calls.updates[0]
    assert call_id == "call_1" and len(calls.updates) == 1
    assert update["call_control"]["trigger_response"] is True
    assert update["call_control"]["additional_context"].startswith("UPDATE ")
    assert update["call_control"]["additional_context"].endswith(": Maya just pressed I'm OK on her phone.")
    assert update["fields_to_override"] == {"override_dynamic_variables": {"current_status": "Responded."}}
    assert steps[-4:] == ["call_update", "call_ended", "acknowledged", "closed"]


async def test_voicemail_falls_back_to_one_therapist_call(monkeypatch: pytest.MonkeyPatch) -> None:
    live_env(monkeypatch)
    calls = FakeCalls({"call_1": [("ongoing", None), ("ended", {"data": {}, "voicemail": True,
                                                                "reason": "voicemail_reached"})],
                       "call_2": [("registered", None)]})
    notifier = RetellNotifier(client=SimpleNamespace(call=calls), poll_interval_s=0.02)
    guardian, _, _, sid, steps = await make(notifier)
    await guardian.on_event(fall(sid))
    await until(lambda: "closed" in steps, timeout=3)
    assert [c["metadata"]["role"] for c in calls.created] == ["emergency_contact", "therapist"]
    assert calls.created[1]["to_number"] == THERAPIST_PHONE
    assert calls.created[1]["retell_llm_dynamic_variables"]["contact_name"] == "Dr. Okafor"
    assert "therapist_call" in steps and "acknowledged" not in steps


async def test_not_connected_retries_once_then_calls_the_therapist(monkeypatch: pytest.MonkeyPatch) -> None:
    live_env(monkeypatch)
    calls = FakeCalls({"call_1": [("not_connected", None)], "call_2": [("not_connected", None)],
                       "call_3": [("registered", None)]})
    notifier = RetellNotifier(client=SimpleNamespace(call=calls), poll_interval_s=0.02, not_connected_retry_s=0.01)
    guardian, _, _, sid, steps = await make(notifier)
    await guardian.on_event(fall(sid))
    await until(lambda: "closed" in steps, timeout=3)
    keys = [c["idempotency_key"].split(":", 1)[1] for c in calls.created]
    assert keys == ["emergency_contact", "emergency_contact:retry", "therapist"]
    assert steps.count("call_retry") == 1 and steps.count("call_not_connected") == 2


async def test_watch_timeout_still_closes_the_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    live_env(monkeypatch)
    calls = FakeCalls({"call_1": [("ended", None)]})  # analysis never arrives
    notifier = RetellNotifier(client=SimpleNamespace(call=calls), poll_interval_s=0.01, max_polls=5)
    guardian, _, _, sid, steps = await make(notifier)
    await guardian.on_event(fall(sid))
    await until(lambda: "closed" in steps, timeout=3)
    assert steps[-2:] == ["call_watch_timeout", "closed"]


async def test_calls_enabled_without_credentials_stays_log_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLS_DISABLED", "0")
    reload_config()
    assert RetellNotifier().mode == "log_only"


async def test_webhook_events_are_recorded_on_the_matching_incident() -> None:
    notifier = RetellNotifier(sim_duration_s=5, poll_interval_s=0.05)
    guardian, _, _, sid, steps = await make(notifier)
    await guardian.on_event(fall(sid))
    await until(lambda: "escalated" in steps)
    call_id = guardian.incidents[sid].call_id or guardian.incidents[sid].escalation["contact_call_id"]
    await notifier.on_webhook("call_started", {"call_id": call_id})
    await notifier.on_webhook("call_started", {"call_id": "someone-elses"})
    assert steps.count("webhook_call_started") == 1
    await notifier.stop()
    await guardian.stop()
