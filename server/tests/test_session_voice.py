from __future__ import annotations

import json
import sys
import time
import types as pytypes
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from server.ai import cue_library, eleven, gemini, session_voice
from server.ai.session_voice import SessionVoice
from server.ai.validators import AI_DRAFT_LABEL
from server.config import cfg, reload_config
from server.state import LiveSession, SessionRegistry

from .conftest import AUTH, TOKEN


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class Live:
    reps_target: int | None = 10
    sets_target: int | None = 3


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    gemini.reset_for_tests()
    gemini.register_fake("greeting", session_voice._fake_greeting)
    gemini.register_fake("summary", session_voice._fake_summary)
    cue_library.reload()
    yield
    gemini.register_fake("greeting", session_voice._fake_greeting)
    gemini.register_fake("summary", session_voice._fake_summary)


async def _setup(db: Any = None) -> tuple[SessionVoice, FakeHub, LiveSession]:
    hub = FakeHub()
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    return SessionVoice(hub, sessions, live=Live(), db_provider=lambda: db), hub, session


async def _play_set(voice: SessionVoice, sid: str, flagged: int = 1, reps: int = 10) -> None:
    for i in range(1, reps + 1):
        bad = i <= flagged
        await voice.on_event({"type": "rep", "session_id": sid, "index": i, "set": 1,
                              "quality": "fix_form" if bad else "good", "flags": ["knee_cave_l"] if bad else []})
    await voice.on_event({"type": "hr", "session_id": sid, "bpm": 104})
    await voice.on_event({"type": "set_done", "session_id": sid, "set": 1, "reps": reps, "exercise_id": "squat"})
    await voice.drain()


# ---- greeting -------------------------------------------------------------------------------------

async def test_greeting_emitted_on_arrived() -> None:
    voice, hub, session = await _setup()
    voice.prepare_greeting(session)
    await voice.on_event({"type": "arrived", "session_id": session.session_id, "patient_id": "maya"})
    await voice.drain()
    [greeting] = hub.of("greeting")
    assert greeting["session_id"] == session.session_id
    assert "Maya" in greeting["text"] and greeting["source"] == "ai"
    assert greeting["audio_url"] is None  # no ElevenLabs key in tests


async def test_greeting_voiced_when_eleven_available(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setenv("ELEVEN_API_KEY", "k-not-real")
    monkeypatch.setenv("ELEVEN_VOICE_ID", "voice")
    monkeypatch.setenv("ELEVEN_RUNTIME_TTS", "1")
    reload_config()
    monkeypatch.setattr(eleven, "transport",
                        httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200, content=b"mp3")))
    voice, hub, session = await _setup()
    await voice.on_event({"type": "arrived", "session_id": session.session_id})
    await voice.drain()
    [greeting] = hub.of("greeting")
    assert greeting["audio_url"].startswith("/audio/") and len(requests) == 1
    assert json.loads(requests[0].content)["text"] == greeting["text"]


async def test_greeting_fallback_when_ai_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DISABLED", "1")
    reload_config()
    voice, hub, session = await _setup()
    await voice.on_event({"type": "arrived", "session_id": session.session_id})
    await voice.drain()
    [greeting] = hub.of("greeting")
    assert greeting["source"] == "template" and greeting["text"].startswith("Hi Maya!")


async def test_greeting_fallback_uses_generic_recorded_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini.register_fake("greeting", lambda m, t: gemini.Reply(text="You have a torn ligament, Maya."))
    mp3 = Path(cfg.DATA_DIR) / "cues" / "mp3"
    mp3.mkdir(parents=True)
    for n in (1, 2, 3):
        (mp3 / f"greeting_generic_{n}.mp3").write_bytes(b"x")
    cue_library.reload()
    voice, hub, session = await _setup()
    await voice.on_event({"type": "arrived", "session_id": session.session_id})
    await voice.drain()
    [greeting] = hub.of("greeting")
    assert greeting["source"] == "template" and greeting["audio_url"].startswith("/audio/greeting_generic_")
    assert "torn" not in greeting["text"]


# ---- summary --------------------------------------------------------------------------------------

async def test_summary_ready_after_set_done() -> None:
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id)
    [summary] = hub.of("summary_ready")
    assert summary["set"] == 1 and summary["source"] == "ai"
    assert "9 of 10" in summary["spoken"]
    assert summary["therapist_note"].startswith(AI_DRAFT_LABEL)
    assert "knee_cave_l ×1" in summary["therapist_note"]


async def test_summary_sends_only_structured_numbers() -> None:
    seen: list[dict[str, Any]] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        seen.append(json.loads(messages[0]["content"].removeprefix("DATA:\n")))
        return session_voice._fake_summary(messages, tools)

    gemini.register_fake("summary", fake)
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id, flagged=2)
    assert seen == [{"exercise": "squat", "set": 1, "reps": 10, "good_form_reps": 8,
                     "flags": {"knee_cave_l": 2}, "reps_target": 10, "sets_target": 3, "peak_hr": 104}]


async def test_invented_number_falls_back_to_template() -> None:
    gemini.register_fake("summary", lambda m, t: gemini.Reply(text=json.dumps(
        {"spoken": "Great set! You did 14 reps.", "therapist_note": "14 reps."})))
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id)
    [summary] = hub.of("summary_ready")
    assert summary["source"] == "template"
    assert summary["spoken"] == ("Set 1 done: 9 of 10 reps with good form. "
                                 "Next set, try to push your left knee out. Take a good rest.")
    assert "14" not in summary["therapist_note"] and not summary["therapist_note"].startswith(AI_DRAFT_LABEL)


async def test_bad_json_retried_once() -> None:
    answers = iter(["not json", json.dumps({"spoken": "Nice set!", "therapist_note": "Steady set."})])
    calls: list[list[dict[str, Any]]] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        calls.append(messages)
        return gemini.Reply(text=next(answers))

    gemini.register_fake("summary", fake)
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id)
    [summary] = hub.of("summary_ready")
    assert len(calls) == 2 and "rejected" in calls[1][-1]["content"]
    assert summary["spoken"] == "Nice set!" and summary["therapist_note"] == f"{AI_DRAFT_LABEL}: Steady set."


async def test_summary_template_when_ai_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DISABLED", "1")
    reload_config()
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id, flagged=0)
    [summary] = hub.of("summary_ready")
    assert summary["source"] == "template" and "Nice steady work." in summary["spoken"]


async def test_summary_stored_through_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[tuple[Any, ...]] = []

    async def update_session_ai(db: Any, session_id: str, *, ai_summary: str, ai_note: str) -> None:
        stored.append((db, session_id, ai_summary, ai_note))

    writes = pytypes.ModuleType("server.db.writes")
    writes.update_session_ai = update_session_ai  # type: ignore[attr-defined]
    pkg = pytypes.ModuleType("server.db")
    pkg.writes = writes  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.db", pkg)
    monkeypatch.setitem(sys.modules, "server.db.writes", writes)
    db = object()
    voice, hub, session = await _setup(db)
    await _play_set(voice, session.session_id)
    [summary] = hub.of("summary_ready")
    assert stored == [(db, session.session_id, summary["spoken"], summary["therapist_note"])]


async def test_events_without_session_are_ignored() -> None:
    voice, hub, _ = await _setup()
    await voice.on_event({"type": "set_done", "set": 1, "reps": 10})
    await voice.drain()
    assert hub.events == []


# ---- wired into the app ---------------------------------------------------------------------------

def test_app_emits_greeting_and_summary(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    from server.state import get_state

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        state = get_state()
        sid = state.sessions.current.session_id  # type: ignore[union-attr]

        async def arrive() -> None:
            state.bridge.inject({"type": "arrived", "patient_id": "maya"})
            state.bridge.inject({"type": "set_done", "set": 7, "reps": 3, "exercise_id": "squat"})

        client.portal.call(arrive)  # type: ignore[union-attr]
        got: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + 10
        while len(got) < 2 and time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] in ("greeting", "summary_ready") and (msg["type"] != "summary_ready" or msg["set"] == 7):
                got[msg["type"]] = msg
        assert got["greeting"]["session_id"] == sid and "Maya" in got["greeting"]["text"]
        assert got["summary_ready"]["session_id"] == sid
    assert "ai" in state.stat_providers
    stats = client.get("/stats", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert stats["ai"]["mode"] == "fake" and stats["ai"]["calls"]["fake"] >= 2


# ---- review fixes ---------------------------------------------------------------------------------

def _record_discards(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    discarded: list[str | None] = []

    async def discard(reply: gemini.Reply) -> None:
        discarded.append(reply.text)

    monkeypatch.setattr(gemini, "discard", discard)
    return discarded


async def test_rejected_ai_answers_are_discarded_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    discarded = _record_discards(monkeypatch)
    gemini.register_fake("greeting", lambda m, t: gemini.Reply(text="You have a torn ligament, Maya."))
    bad_summary = json.dumps({"spoken": "Great set! You did 14 reps.", "therapist_note": "14 reps."})
    gemini.register_fake("summary", lambda m, t: gemini.Reply(text=bad_summary))
    voice, hub, session = await _setup()
    voice.prepare_greeting(session)
    await voice.on_event({"type": "arrived", "session_id": session.session_id})
    await _play_set(voice, session.session_id)
    assert discarded == ["You have a torn ligament, Maya.", bad_summary]


async def test_only_unparseable_first_answer_discarded_after_good_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    discarded = _record_discards(monkeypatch)
    good = json.dumps({"spoken": "Nice set!", "therapist_note": "Steady set."})
    answers = iter(["not json", good])
    gemini.register_fake("summary", lambda m, t: gemini.Reply(text=next(answers)))
    voice, hub, session = await _setup()
    await _play_set(voice, session.session_id)
    assert discarded == ["not json"] and hub.of("summary_ready")[0]["source"] == "ai"


async def test_greeting_still_emitted_when_preparation_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(text: str, cache_key: str) -> str | None:
        raise RuntimeError("tts bug")

    monkeypatch.setattr(eleven, "tts", boom)
    voice, hub, session = await _setup()
    voice.prepare_greeting(session)
    await voice.on_event({"type": "arrived", "session_id": session.session_id})
    await voice.drain()
    [greeting] = hub.of("greeting")
    assert greeting["source"] == "template" and greeting["text"].startswith("Hi Maya!")


async def test_tts_write_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_API_KEY", "test-eleven-key")
    monkeypatch.setenv("ELEVEN_VOICE_ID", "voice")
    monkeypatch.setenv("ELEVEN_RUNTIME_TTS", "1")  # reach the disk write
    reload_config()
    monkeypatch.setattr(eleven, "transport", httpx.MockTransport(lambda r: httpx.Response(200, content=b"mp3")))

    def no_disk(dest: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(eleven, "_write_atomic", no_disk)
    assert await eleven.tts("Hello", "k") is None


def test_mock_run_emits_greeting_and_enriched_cue(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    from server.state import get_state

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        state = get_state()
        client.portal.call(lambda: _inject_cue(state))  # type: ignore[union-attr]
        got: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + 10
        while len(got) < 2 and time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "greeting" or (msg["type"] == "cue" and msg["id"] == "knees_out"):
                got[msg["type"]] = msg
    assert "Maya" in got["greeting"]["text"]  # from the mock's own `arrived`, nothing injected
    lines = [e["text"] for e in cue_library.library().entries["knees_out"]]
    assert got["cue"]["text"] in lines and "audio_url" in got["cue"]


async def _inject_cue(state: Any) -> None:
    state.bridge.inject({"type": "cue", "id": "knees_out"})


# ---- REST-started sessions in mock mode (the bug this file's tests were added to catch) -----------

def test_rest_session_start_emits_arrived_and_greeting(make_client: Callable[..., TestClient]) -> None:
    """POST /sessions in mock mode must restart the mock program for the new session, so it `arrived`s
    and SessionVoice greets it — not just the session auto-started at boot."""
    client = make_client()
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        resp = client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)
        assert resp.status_code == 200
        new_sid = resp.json()["session_id"]

        got: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + 10
        while len(got) < 2 and time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] in ("arrived", "greeting") and msg.get("session_id") == new_sid:
                got[msg["type"]] = msg
        assert got.keys() == {"arrived", "greeting"}
        assert got["arrived"]["patient_id"] == "maya"
        assert "Maya" in got["greeting"]["text"]


def test_mock_fall_still_works_after_rest_session_start(make_client: Callable[..., TestClient]) -> None:
    """mock:fall must still script a fall after a REST session start restarts the squat program. The
    automatic fall is scheduled 20 simulated seconds in (too slow for a unit test), so trigger one instead
    to confirm the fall script still works for the new, REST-started session."""
    client = make_client(MIRA_SOURCE="mock:fall")
    from server.state import get_state

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        resp = client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)
        assert resp.status_code == 200
        new_sid = resp.json()["session_id"]

        got_arrived = got_fall = False
        deadline = time.monotonic() + 10
        triggered = False
        while not (got_arrived and got_fall) and time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "arrived" and msg.get("session_id") == new_sid:
                got_arrived = True
                if not triggered:
                    triggered = True
                    client.portal.call(lambda: get_state().bridge.trigger_fall(None))  # type: ignore[union-attr]
            elif msg["type"] == "fall" and msg.get("session_id") == new_sid:
                got_fall = True
        assert got_arrived and got_fall
