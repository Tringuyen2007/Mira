"""HapticCoach: rep/set_done engine events become `haptic` events for the watch (§7)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from server.agents.haptics import HapticCoach
from server.state import AppState, LiveSession, SessionRegistry, default_profile, set_state


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
def coach(hub: FakeHub) -> HapticCoach:
    return HapticCoach(hub)


@pytest.fixture
def live_session() -> Iterator[LiveSession]:
    """Puts a current session into a fresh AppState so HapticCoach sees a live session."""
    sessions = SessionRegistry()
    session = LiveSession(session_id="sess-1", patient_id="maya", exercise_id="squat", started_at=0.0,
                          profile=default_profile("maya"))
    sessions.current = session
    sessions._sessions[session.session_id] = session
    state = AppState(cfg=None, hub=None, bridge=None, sessions=sessions)  # type: ignore[arg-type]
    set_state(state)
    yield session
    set_state(None)


async def test_good_rep_emits_good_rep_haptic(coach: HapticCoach, hub: FakeHub, live_session: LiveSession) -> None:
    await coach.on_event({"type": "rep", "quality": "good", "session_id": live_session.session_id, "t": 1.0})
    assert hub.events == [{"type": "haptic", "pattern": "good_rep", "session_id": live_session.session_id, "t": 1.0}]


async def test_non_good_rep_emits_fix_form_haptic(coach: HapticCoach, hub: FakeHub,
                                                  live_session: LiveSession) -> None:
    await coach.on_event({"type": "rep", "quality": "poor", "session_id": live_session.session_id, "t": 2.0})
    assert hub.events[0]["pattern"] == "fix_form"


async def test_set_done_emits_set_done_haptic(coach: HapticCoach, hub: FakeHub, live_session: LiveSession) -> None:
    await coach.on_event({"type": "set_done", "session_id": live_session.session_id, "t": 3.0})
    assert hub.events[0]["pattern"] == "set_done"


async def test_other_events_are_ignored(coach: HapticCoach, hub: FakeHub, live_session: LiveSession) -> None:
    await coach.on_event({"type": "gesture", "name": "raised_hand", "session_id": live_session.session_id})
    assert hub.events == []


async def test_nothing_when_no_session(coach: HapticCoach, hub: FakeHub) -> None:
    set_state(None)
    await coach.on_event({"type": "rep", "quality": "good", "session_id": "sess-1", "t": 1.0})
    assert hub.events == []


async def test_nothing_when_state_not_initialised(coach: HapticCoach, hub: FakeHub) -> None:
    set_state(None)
    await coach.on_event({"type": "set_done", "session_id": "sess-1", "t": 1.0})
    assert hub.events == []


async def test_countdown_emits_countdown_tick(coach: HapticCoach, hub: FakeHub, live_session: LiveSession) -> None:
    await coach.on_event({"type": "countdown", "n": 3, "session_id": live_session.session_id, "t": 4.0})
    assert hub.events == [{"type": "haptic", "pattern": "countdown_tick", "session_id": live_session.session_id,
                           "t": 4.0}]


async def test_a_short_watch_counted_turn_has_no_haptic(coach: HapticCoach, hub: FakeHub,
                                                        live_session: LiveSession) -> None:
    sid = live_session.session_id
    await coach.on_event({"type": "rep", "quality": "partial", "source": "watch", "session_id": sid, "t": 5.0})
    assert hub.events == []
    await coach.on_event({"type": "rep", "quality": "good", "source": "watch", "session_id": sid, "t": 6.0})
    assert hub.events[0]["pattern"] == "good_rep"


def test_haptic_patterns_include_the_watch_ones() -> None:
    from server.agents.haptics import HAPTIC_PATTERNS

    assert HAPTIC_PATTERNS == ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather")
