from __future__ import annotations

from typing import Any

import pytest

from server.agents import session_coach
from server.agents.session_coach import DEFAULT_LIMITS, SessionCoach
from server.ai import gemini
from server.state import SessionRegistry


@pytest.fixture(autouse=True)
def restore_default_coach_fake() -> Any:
    """Tests that register their own fake must not leak it into later tests (module-global registry)."""
    yield
    gemini.register_fake("coach", session_coach._fake_coach)


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
async def session(hub: FakeHub):
    registry = SessionRegistry()
    s = await registry.start("maya", "squat")
    return registry, s


@pytest.fixture
def coach(hub: FakeHub, session: Any) -> SessionCoach:
    registry, _ = session
    return SessionCoach(hub, registry, db_provider=lambda: None)


async def _reps(coach: SessionCoach, sid: str, n: int, set_no: int = 1, flags: list[str] | None = None) -> None:
    for i in range(1, n + 1):
        await coach.on_event({"type": "rep", "session_id": sid, "set": set_no, "index": i, "quality": "good",
                              "flags": flags or []})


# ---- live rules ---------------------------------------------------------------------------------


async def test_fatigue_below_min_reps_does_not_end_set(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await _reps(coach, s.session_id, 3)  # below the default min_reps (8)
    await coach.on_event({"type": "fatigue", "session_id": s.session_id})
    assert hub.of("coach_adapt") == []


async def test_fatigue_at_or_above_min_reps_ends_set(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await _reps(coach, s.session_id, 9)
    await coach.on_event({"type": "fatigue", "session_id": s.session_id})
    adapts = hub.of("coach_adapt")
    assert len(adapts) == 1
    assert adapts[0]["action"] == "end_set_early"
    assert adapts[0]["detail"]["reps_done"] == 9
    assert adapts[0]["detail"]["min_reps"] == DEFAULT_LIMITS["min_reps"]
    # a fatigue cue is played
    assert hub.of("cue")[0]["id"] == "fatigue"


async def test_hr_rule_extends_rest_within_max(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session  # Maya: hr_limit=130, so bpm > 120 fires the rule
    await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 125})
    adapts = hub.of("coach_adapt")
    assert len(adapts) == 1
    assert adapts[0]["action"] == "extend_rest"
    applied = adapts[0]["detail"]["applied_rest_s"]
    assert DEFAULT_LIMITS["min_rest_s"] < applied <= DEFAULT_LIMITS["max_rest_s"]


async def test_hr_rule_never_exceeds_max_rest(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    for _ in range(10):  # repeated breaches must still clamp at max_rest_s
        await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 128})
    for adapt in hub.of("coach_adapt"):
        assert adapt["detail"]["applied_rest_s"] <= DEFAULT_LIMITS["max_rest_s"]


async def test_hr_below_threshold_does_nothing(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 110})
    assert hub.of("coach_adapt") == []


async def test_pain_5_skips_next_exercise_and_notifies(coach: SessionCoach, hub: FakeHub, session: Any,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    notified: list[tuple[Any, ...]] = []

    async def fake_create(*args: Any) -> int:
        notified.append(args)
        return 1

    import types

    fake_module = types.ModuleType("server.notify.notifications")
    fake_module.create = fake_create  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "server.notify.notifications", fake_module)

    await coach.on_event({"type": "command", "name": "pain", "session_id": s.session_id, "payload": {"level": 5}})
    await coach.drain()  # the notification is sent in the background
    adapts = hub.of("coach_adapt")
    assert len(adapts) == 1 and adapts[0]["action"] == "skip_next_exercise"
    assert len(notified) == 1
    assert notified[0][0] == "coach" and notified[0][1] == "low"


async def test_pain_7_coach_does_nothing(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await coach.on_event({"type": "command", "name": "pain", "session_id": s.session_id, "payload": {"level": 7}})
    assert hub.of("coach_adapt") == []


# ---- between-set agent ---------------------------------------------------------------------------


async def test_ai_requested_reps_clamped_to_min_and_logged(coach: SessionCoach, hub: FakeHub, session: Any,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        calls = sum(1 for m in messages if m.get("role") == "model" and m.get("function_call"))
        if calls == 0:
            return gemini.Reply(function_call=gemini.FunctionCall(name="adjust_next_set", args={"reps": 6}))
        return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "done"}))

    monkeypatch.setattr(gemini, "register_fake", gemini.register_fake)
    gemini.register_fake("coach", fake)
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    await coach.drain()
    adapts = hub.of("coach_adapt")
    assert len(adapts) == 1 and adapts[0]["action"] == "adjust_next_set"
    assert adapts[0]["detail"]["reps_requested"] == 6
    assert adapts[0]["detail"]["reps_applied"] == DEFAULT_LIMITS["min_reps"]


async def test_unknown_cue_id_rejected(coach: SessionCoach, hub: FakeHub, session: Any,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        calls = sum(1 for m in messages if m.get("role") == "model" and m.get("function_call"))
        if calls == 0:
            return gemini.Reply(function_call=gemini.FunctionCall(
                name="choose_focus_cue", args={"cue_id": "not_a_real_cue"}))
        return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "done"}))

    gemini.register_fake("coach", fake)
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    await coach.drain()
    assert hub.of("cue") == []
    assert hub.of("coach_adapt") == []


async def test_gemini_timeout_falls_back_to_rules_without_delay(coach: SessionCoach, hub: FakeHub, session: Any,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    import asyncio

    async def slow_ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        await asyncio.sleep(0.05)
        raise gemini.AIUnavailable("timeout")

    monkeypatch.setattr(gemini, "ask", slow_ask)
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    # on_event returns immediately; the agent keeps running in the background
    assert hub.of("coach_adapt") == []
    await coach.drain()
    assert hub.of("coach_adapt") == []  # the fallback makes no AI-driven adjustment


async def test_ai_result_dropped_after_rest_window_missed(coach: SessionCoach, hub: FakeHub, session: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    import asyncio

    import json

    gate = asyncio.Event()

    async def delayed_ask(agent: str, model: str, system: str, messages: list[dict[str, Any]],
                          tools: Any = None, timeout_s: float = 4.0, **kwargs: Any) -> gemini.Reply:
        data = json.loads(str(messages[0]["content"]).removeprefix("DATA:\n"))
        if data["set"] == 1:
            await gate.wait()  # set 1's agent is still thinking when set 2 finishes
            return gemini.Reply(function_call=gemini.FunctionCall(name="adjust_next_set", args={"reps": 6}))
        return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "done"}))

    monkeypatch.setattr(gemini, "ask", delayed_ask)
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    # a second set finishes before the first set's agent gets its answer
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 2, "reps": 10,
                          "exercise_id": "squat"})
    gate.set()
    await coach.drain()
    adjusts = [a for a in hub.of("coach_adapt") if a["action"] == "adjust_next_set"]
    assert adjusts == []  # the stale (set 1) result never gets applied


async def test_coach_adapt_events_during_mock_style_run(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await _reps(coach, s.session_id, 10, set_no=1, flags=["knee_cave_l"])
    await coach.on_event({"type": "set_done", "session_id": s.session_id, "set": 1, "reps": 10,
                          "exercise_id": "squat"})
    await coach.drain()
    actions = {a["action"] for a in hub.of("coach_adapt")}
    assert "adjust_next_set" in actions
    assert "choose_focus_cue" in actions


# ---- review regressions -----------------------------------------------------------------------------


def _script(*calls: tuple[str, dict[str, Any]]) -> Any:
    """A coach fake that plays the given tool calls in order, then finishes."""

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        n = sum(1 for m in messages if m.get("role") == "model" and m.get("function_call"))
        name, args = calls[n] if n < len(calls) else ("finish", {"reason": "done"})
        return gemini.Reply(function_call=gemini.FunctionCall(name=name, args=args))

    return fake


async def _set_done(coach: SessionCoach, sid: str, set_no: int = 1) -> None:
    await coach.on_event({"type": "set_done", "session_id": sid, "set": set_no, "reps": 10, "exercise_id": "squat"})


@pytest.mark.parametrize("args", [{"reps": True}, {"reps": "6"}, {"rest_s": float("nan")}, {"rest_s": "90"},
                                  {"reps": 10, "sets": 5}, {"reps": -3}, {"rest_s": 1e9}])
async def test_adjust_next_set_rejects_malformed_args(coach: SessionCoach, hub: FakeHub, session: Any,
                                                      args: dict[str, Any]) -> None:
    _, s = session
    gemini.register_fake("coach", _script(("adjust_next_set", args)))
    await _set_done(coach, s.session_id)
    await coach.drain()
    assert hub.of("coach_adapt") == []


async def test_adjust_next_set_clamps_rest_and_reps_to_max(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    gemini.register_fake("coach", _script(("adjust_next_set", {"reps": 40, "rest_s": 300.0})))
    await _set_done(coach, s.session_id)
    await coach.drain()
    [adapt] = hub.of("coach_adapt")
    assert adapt["detail"] == {"reps_requested": 40, "reps_applied": DEFAULT_LIMITS["max_reps"],
                               "rest_s_requested": 300.0, "rest_s_applied": DEFAULT_LIMITS["max_rest_s"]}


async def test_focus_cue_must_be_a_form_cue(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session  # check_in / resume exist in cues.json but belong to the Guardian and the engine
    gemini.register_fake("coach", _script(("choose_focus_cue", {"cue_id": "check_in"}),
                                          ("choose_focus_cue", {"cue_id": "resume"})))
    await _set_done(coach, s.session_id)
    await coach.drain()
    assert hub.of("cue") == [] and hub.of("coach_adapt") == []


async def test_unknown_tool_rejected_and_loop_bounded(coach: SessionCoach, hub: FakeHub, session: Any,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    asked: list[int] = []

    async def ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        asked.append(1)
        return gemini.Reply(function_call=gemini.FunctionCall(name="change_prescription", args={"reps": 30}))

    monkeypatch.setattr(gemini, "ask", ask)
    await _set_done(coach, s.session_id)
    await coach.drain()
    assert len(asked) == session_coach.MAX_TOOL_STEPS
    assert hub.of("coach_adapt") == []


async def test_no_adaptation_while_guardian_holds_session(hub: FakeHub, session: Any) -> None:
    registry, s = session
    coach = SessionCoach(hub, registry, db_provider=lambda: None, incident_open=lambda sid: True)
    await _reps(coach, s.session_id, 10, flags=["knee_cave_l"])
    await coach.on_event({"type": "fatigue", "session_id": s.session_id})
    await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 125})
    await coach.on_event({"type": "command", "name": "pain", "session_id": s.session_id, "payload": {"level": 5}})
    await _set_done(coach, s.session_id)
    await coach.drain()
    assert hub.of("coach_adapt") == [] and hub.of("cue") == []


async def test_paused_event_drops_in_flight_agent_result(coach: SessionCoach, hub: FakeHub, session: Any,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    import asyncio

    gate = asyncio.Event()

    async def ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        await gate.wait()
        return gemini.Reply(function_call=gemini.FunctionCall(name="choose_focus_cue", args={"cue_id": "knees_out"}))

    monkeypatch.setattr(gemini, "ask", ask)
    await _set_done(coach, s.session_id)
    await coach.on_event({"type": "paused", "reason": "pain", "session_id": s.session_id})
    gate.set()
    await coach.drain()
    assert hub.of("cue") == [] and hub.of("coach_adapt") == []


async def test_pain_7_holds_the_coach_until_next_set(coach: SessionCoach, hub: FakeHub, session: Any) -> None:
    _, s = session
    await coach.on_event({"type": "command", "name": "pain", "session_id": s.session_id, "payload": {"level": 8}})
    await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 125})
    assert hub.of("coach_adapt") == []
    await coach.on_event({"type": "countdown", "n": 3, "set": 2, "session_id": s.session_id})
    await coach.on_event({"type": "hr", "session_id": s.session_id, "bpm": 125})
    assert [a["action"] for a in hub.of("coach_adapt")] == ["extend_rest"]


async def test_next_set_countdown_closes_the_rest_window(coach: SessionCoach, hub: FakeHub, session: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    import asyncio

    gate = asyncio.Event()

    async def ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        await gate.wait()
        return gemini.Reply(function_call=gemini.FunctionCall(name="adjust_next_set", args={"reps": 10}))

    monkeypatch.setattr(gemini, "ask", ask)
    await _set_done(coach, s.session_id)
    await coach.on_event({"type": "countdown", "n": 3, "set": 2, "session_id": s.session_id})
    gate.set()
    await coach.drain()
    assert hub.of("coach_adapt") == []


async def test_agent_bounded_by_rest_window_budget(coach: SessionCoach, hub: FakeHub, session: Any,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    import asyncio

    async def ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        await asyncio.sleep(0.03)
        return gemini.Reply(function_call=gemini.FunctionCall(name="get_set_stats", args={}))

    monkeypatch.setattr(session_coach, "AI_TIMEOUT_S", 0.05)
    monkeypatch.setattr(session_coach, "DEFAULT_LIMITS", {**DEFAULT_LIMITS, "min_rest_s": 0})
    monkeypatch.setattr(gemini, "ask", ask)
    await _set_done(coach, s.session_id)
    await asyncio.wait_for(coach.drain(), 1.0)  # 5 steps x 0.03 s > the 0.05 s budget: cut off, no hang


def test_clean_limits_rejects_malformed_values() -> None:
    clean = session_coach._clean_limits
    assert clean(None) == DEFAULT_LIMITS
    assert clean({"min_reps": "10", "max_reps": True, "max_rest_s": float("inf")}) == DEFAULT_LIMITS
    assert clean({"min_reps": 20, "max_reps": 10}) == DEFAULT_LIMITS  # inconsistent pair -> defaults
    assert clean({"min_reps": 10, "max_rest_s": 90.0}) == {**DEFAULT_LIMITS, "min_reps": 10, "max_rest_s": 90}


async def test_fatigue_ends_a_set_once_and_notifies_per_set(coach: SessionCoach, hub: FakeHub, session: Any,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _, s = session
    keys: list[str] = []

    async def fake_notify(*args: Any, dedupe_key: str | None = None, **kwargs: Any) -> None:
        keys.append(str(dedupe_key))

    monkeypatch.setattr(session_coach, "_notify", fake_notify)
    for set_no in (1, 2):
        await _reps(coach, s.session_id, 9, set_no=set_no)
        await coach.on_event({"type": "fatigue", "session_id": s.session_id})
        await coach.on_event({"type": "fatigue", "session_id": s.session_id})
    await coach.drain()
    assert [a["detail"]["set"] for a in hub.of("coach_adapt")] == [1, 2]
    assert len(set(keys)) == 2  # distinct dedupe keys: the second set's notification is not swallowed


async def test_new_session_drops_previous_state_and_tasks(coach: SessionCoach, hub: FakeHub, session: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    registry, s = session
    import asyncio

    async def ask(*args: Any, **kwargs: Any) -> gemini.Reply:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    monkeypatch.setattr(gemini, "ask", ask)
    await _set_done(coach, s.session_id)
    s2 = await registry.start("maya", "squat")
    await coach.on_event({"type": "hr", "session_id": s2.session_id, "bpm": 90})
    await asyncio.wait_for(coach.drain(), 1.0)  # the old session's agent was cancelled
    assert list(coach._state) == [s2.session_id]
