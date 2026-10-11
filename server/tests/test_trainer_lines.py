"""Scripted LCD trainer lines, the trainer voice_settings and the ELEVEN_RUNTIME_TTS gate (free-tier budget)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server.ai import cue_library, eleven
from server.config import cfg, reload_config


@pytest.fixture
def eleven_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_API_KEY", "eleven-test-key-not-real")
    monkeypatch.setenv("ELEVEN_VOICE_ID", "cgSgspJ2msm6clMCkdW9")
    monkeypatch.setenv("ELEVEN_DISABLED", "0")
    monkeypatch.setenv("ELEVEN_RUNTIME_TTS", "0")
    reload_config()


def test_scripted_lines_pickable_before_audio_exists() -> None:
    cue_library.reload()  # DATA_DIR is empty in tests → committed default cues.json
    assert cue_library.pick("count_7") == {"id": "count_7", "text": "Seven!", "audio_url": None}
    assert "hand" in cue_library.pick("raise_hand")["text"]  # type: ignore[index]
    entries = cue_library.library().entries
    words = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
             "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty"]
    for n, word in enumerate(words, start=1):
        assert entries[f"count_{n}"] == [{"text": f"{word}!", "file": f"count_{n}_1.mp3"}]
    for cue_id in ("raise_hand", "lets_go", "halfway", "last_rep", "session_done"):
        assert 2 <= len(entries[cue_id]) <= 3, cue_id
    for cue_id in ("calling_contact", "contact_on_call", "call_failed"):
        assert len(entries[cue_id]) == 1, cue_id
    assert entries["calling_contact"][0]["text"].startswith("I'm calling your emergency contact now.")


def test_committed_cues_json_matches_scripted_cues() -> None:
    committed = json.loads(cue_library.DEFAULT_PATH.read_text(encoding="utf-8"))
    for cue_id, lines in cue_library.SCRIPTED_CUES.items():
        assert committed[cue_id] == [{"text": t, "file": f"{cue_id}_{i + 1}.mp3"} for i, t in enumerate(lines)]


def test_scripted_ids_stay_out_of_ai_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = Path(cfg.DATA_DIR) / "rules"
    rules.mkdir(parents=True)
    (rules / "r.json").write_text(json.dumps({"rules": [{"id": "count_3", "cue": "x"}]}))
    ids = cue_library.all_cue_ids()
    assert not set(ids) & set(cue_library.SCRIPTED_CUES)


def test_voice_settings_defaults() -> None:
    assert eleven.voice_settings() == {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35,
                                       "use_speaker_boost": True}


async def test_runtime_tts_off_never_calls_elevenlabs(eleven_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        pytest.fail("ElevenLabs must not be called while ELEVEN_RUNTIME_TTS is off")

    monkeypatch.setattr(eleven, "transport", httpx.MockTransport(handler))
    assert cfg.ELEVEN_RUNTIME_TTS is False
    assert await eleven.tts("hi", "k") is None
    assert hits == []


async def test_runtime_tts_off_still_serves_existing_file(eleven_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eleven, "transport", httpx.MockTransport(lambda r: pytest.fail("no request expected")))
    name = eleven.audio_name("hi", "k")
    eleven._write_atomic(eleven.audio_dir() / name, b"ID3cached")
    assert await eleven.tts("hi", "k") == f"/audio/{name}"


async def test_force_bypasses_gate_and_sends_voice_settings(eleven_on: None,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[httpx.Request] = []
    monkeypatch.setattr(eleven, "transport",
                        httpx.MockTransport(lambda r: hits.append(r) or httpx.Response(200, content=b"ID3fake")))
    url = await eleven.tts("hi", "k", force=True)
    assert url is not None and url.startswith("/audio/")
    assert len(hits) == 1
    body = json.loads(hits[0].content)
    assert body["voice_settings"]["stability"] == 0.45
    assert body["voice_settings"] == eleven.voice_settings()
    assert body["model_id"] == "eleven_flash_v2_5"
