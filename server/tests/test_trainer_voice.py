"""TrainerVoice: engine/hub events -> `say` lines from the cue library (fake hub, fake pick, no network)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server.agents.trainer_voice import TrainerVoice

SID = "s_01"


class FakeHub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []
        self.emit_observers: list[Any] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.emitted.append((event_type, payload))


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def fake_pick(cue_id: str) -> dict[str, Any] | None:
    if cue_id == "unknown_line":
        return None
    return {"id": cue_id, "text": cue_id.upper(), "audio_url": f"/audio/{cue_id}_1.mp3"}


def make(clock: Clock | None = None, pick: Any = fake_pick) -> tuple[TrainerVoice, FakeHub, Clock]:
    hub = FakeHub()
    clock = clock or Clock()
    return TrainerVoice(hub, None, pick=pick, now=clock), hub, clock


def said(hub: FakeHub) -> list[str]:
    return [p["id"] for kind, p in hub.emitted if kind == "say"]


def ev(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": SID, **fields}


async def feed(tv: TrainerVoice, clock: Clock, *events: dict[str, Any], step_s: float = 2.0) -> None:
    for e in events:
        await tv.on_event(e)
        clock.t += step_s


async def test_gesture_prompt_and_go() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("awaiting_gesture", gesture="thumbs_up"), ev("gesture", name="thumbs_up"))
    assert said(hub) == ["raise_hand", "lets_go"]


async def test_countdown_three_two_one_only() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, *(ev("countdown", n=n, reps_target=10) for n in (5, 3, 2, 1)))
    assert said(hub) == ["countdown_3", "countdown_2", "countdown_1"]


async def test_good_reps_counted_fix_form_silent() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("rep", index=1, quality="good"), ev("rep", index=2, quality="good"),
               ev("rep", index=3, quality="fix_form"), ev("rep", index=4, quality="good"))
    assert said(hub) == ["count_1", "count_2", "count_4"]


async def test_count_capped_at_twenty() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("rep", index=25, quality="good"))
    assert said(hub) == ["count_20"]


async def test_halfway_and_last_rep_at_target_ten() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("countdown", n=1, reps_target=10))
    await feed(tv, clock, *(ev("rep", index=i, quality="good") for i in range(1, 11)))
    lines = said(hub)
    assert lines[lines.index("count_5") + 1] == "halfway"
    assert lines[lines.index("count_9") + 1] == "last_rep"
    assert lines.count("halfway") == 1 and lines.count("last_rep") == 1


async def test_reps_target_from_rep_event_and_unknown_target_skips_extras() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, *(ev("rep", index=i, quality="good") for i in range(1, 6)))
    assert "halfway" not in said(hub) and "last_rep" not in said(hub)
    tv2, hub2, clock2 = make()
    await feed(tv2, clock2, ev("rep", index=3, quality="good", reps_target=4))
    assert said(hub2) == ["count_3", "last_rep"]


async def test_pause_resume_and_session_done() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("paused", reason="command"), ev("resumed"), ev("session_summary"))
    assert said(hub) == ["pause", "resume", "session_done"]


async def test_set_done_spoken_after_one_second_without_summary_audio() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("set_done", set=1, reps=10))
    await tv.on_event(ev("summary_ready", audio_url=None))  # runtime TTS off: no audio, does not cancel
    assert said(hub) == []
    await asyncio.sleep(1.05)
    assert said(hub) == ["set_done"]
    await tv.close()


async def test_set_done_suppressed_when_summary_has_audio() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("set_done", set=1, reps=10))
    await asyncio.sleep(0.2)
    await tv.on_event(ev("summary_ready", audio_url="/audio/x.mp3"))
    await asyncio.sleep(1.0)
    assert said(hub) == []
    await tv.close()


async def test_incident_hold_only_call_flow_lines() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("cue", id="check_in", text="Are you OK?"), ev("rep", index=1, quality="good"),
               ev("countdown", n=3), ev("alert", step="escalated", alert_id="a1"),
               ev("alert", step="call_ongoing", alert_id="a1"))
    assert said(hub) == ["calling_contact", "contact_on_call"]
    await feed(tv, clock, ev("alert", step="closed", alert_id="a1"), ev("rep", index=2, quality="good"))
    assert said(hub)[-1] == "count_2"


@pytest.mark.parametrize("opener", [ev("fall", stage="confirmed"), ev("alert", step="detected", alert_id="a2")])
async def test_fall_and_alert_detected_open_the_hold(opener: dict[str, Any]) -> None:
    tv, hub, clock = make()
    await feed(tv, clock, opener, ev("gesture", name="thumbs_up"), ev("alert", step="call_failed", alert_id="a2"))
    assert said(hub) == ["call_failed"]


async def test_resolved_without_session_id_clears_the_hold() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("fall", stage="confirmed"), {"type": "alert", "step": "resolved_by_therapist"},
               ev("rep", index=1, quality="good"))
    assert said(hub) == ["count_1"]


async def test_set_done_timer_silenced_by_incident() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("set_done", set=1))
    await tv.on_event(ev("fall", stage="confirmed"))
    await asyncio.sleep(1.05)
    assert said(hub) == []
    await tv.close()


async def test_dedupe_same_id_within_window() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("paused"))
    clock.t += 1.0
    await tv.on_event(ev("paused"))
    assert said(hub) == ["pause"]
    clock.t += 1.0  # 2.0 s after the first
    await tv.on_event(ev("paused"))
    assert said(hub) == ["pause", "pause"]


async def test_same_event_from_both_streams_is_handled_once() -> None:
    tv, hub, clock = make()
    alert = ev("alert", step="escalated", alert_id="a3", t=55.5)
    await tv.on_event(alert)
    clock.t += 5.0
    await tv.on_event(dict(alert))
    assert said(hub) == ["calling_contact"]


async def test_observer_schedules_agent_events_and_ignores_say() -> None:
    tv, hub, clock = make()
    tv.observe({"type": "say", "session_id": SID, "id": "pause"})
    tv.observe(ev("session_summary", t=1.0))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert said(hub) == ["session_done"]


async def test_unknown_pick_emits_nothing() -> None:
    tv, hub, clock = make(pick=lambda _id: None)
    await feed(tv, clock, ev("gesture", name="thumbs_up"), ev("rep", index=1, quality="good"))
    assert hub.emitted == []


async def test_say_payload_shape() -> None:
    tv, hub, clock = make(Clock(1760000000.0))
    await tv.on_event(ev("rep", index=3, quality="good"))
    kind, payload = hub.emitted[0]
    assert kind == "say"
    assert payload == {"session_id": SID, "t": 1760000000.0, "id": "count_3", "text": "COUNT_3",
                       "audio_url": "/audio/count_3_1.mp3"}


async def test_count_is_emitted_before_last_rep_on_the_same_rep() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("rep", index=9, quality="good", reps_target=10))
    assert said(hub) == ["count_9", "last_rep"]


async def test_session_summary_cancels_the_pending_set_done() -> None:
    tv, hub, clock = make()
    await tv.on_event(ev("set_done", set=3, reps=10))
    await asyncio.sleep(0.2)
    await tv.on_event(ev("session_summary"))
    await asyncio.sleep(1.0)
    assert said(hub) == ["session_done"]
    await tv.close()


async def test_stopped_drops_the_sessions_state() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("countdown", n=3, reps_target=10), ev("rep", index=1, quality="good"),
               ev("alert", step="escalated", alert_id="a1"))
    await tv.on_event(ev("set_done", set=1))
    await tv.on_event(ev("stopped", reason="command"))
    assert tv._reps_target == {} and tv._last_said == {} and tv._call_lines_said == {}
    assert tv._set_done_tasks == {}
    await asyncio.sleep(1.05)
    assert "set_done" not in said(hub)


async def test_stopped_keeps_an_open_incident_hold() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("fall", stage="confirmed"), ev("alert", step="escalated", alert_id="a1"),
               ev("stopped", reason="command"), ev("alert", step="call_registered", alert_id="a1"),
               ev("rep", index=1, quality="good"))
    assert said(hub) == ["calling_contact"]


async def test_arrived_for_a_new_session_drops_other_sessions() -> None:
    tv, hub, clock = make()
    await feed(tv, clock, ev("countdown", n=3, reps_target=10), ev("rep", index=1, quality="good"),
               ev("fall", stage="confirmed"), ev("alert", step="escalated", alert_id="a1"))
    await feed(tv, clock, {"type": "arrived", "session_id": "s_02"}, {"type": "rep", "session_id": "s_02",
                                                                      "index": 1, "quality": "good"})
    assert set(tv._reps_target) <= {"s_02"} and tv._incident == set() and tv._call_lines_said == {}
    assert {k[0] for k in tv._last_said} == {"s_02"}
    assert said(hub)[-1] == "count_1"
