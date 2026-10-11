from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from server.ai import eleven
from server.config import cfg, reload_config

KEY = "eleven-test-key-not-real"


class Recorder:
    def __init__(self, status: int = 200, body: bytes = b"ID3fake-mp3") -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"character_count": 2000, "character_limit": 10000})
        return httpx.Response(self.status, content=self.body)


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    monkeypatch.setenv("ELEVEN_API_KEY", KEY)
    monkeypatch.setenv("ELEVEN_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    monkeypatch.setenv("ELEVEN_RUNTIME_TTS", "1")  # these tests exercise runtime generation
    reload_config()
    recorder = Recorder()
    monkeypatch.setattr(eleven, "transport", httpx.MockTransport(recorder))
    yield recorder


async def test_tts_generates_once_then_serves_cache(rec: Recorder) -> None:
    url = await eleven.tts("Hi Maya!", "greeting:maya")
    assert url is not None and url.startswith("/audio/") and url.endswith(".mp3")
    path = Path(cfg.DATA_DIR) / "audio" / url.removeprefix("/audio/")
    assert path.read_bytes() == b"ID3fake-mp3"

    assert await eleven.tts("Hi Maya!", "greeting:maya") == url
    assert len(rec.requests) == 1

    req = rec.requests[0]
    assert req.url.path == "/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL"
    assert req.headers["xi-api-key"] == KEY
    assert json.loads(req.content) == {"text": "Hi Maya!", "model_id": "eleven_flash_v2_5",
                                       "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                                          "style": 0.35, "use_speaker_boost": True}}


async def test_changed_text_gets_new_file(rec: Recorder) -> None:
    a = await eleven.tts("Hi Maya!", "greeting:maya")
    b = await eleven.tts("Hello Maya!", "greeting:maya")
    assert a != b and len(rec.requests) == 2


async def test_disabled_or_missing_key_returns_none(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVEN_DISABLED", "1")
    reload_config()
    assert await eleven.tts("Hi", "k") is None
    monkeypatch.setenv("ELEVEN_DISABLED", "0")
    monkeypatch.setenv("ELEVEN_API_KEY", "")
    reload_config()
    assert await eleven.tts("Hi", "k") is None
    assert rec.requests == []


async def test_cached_file_served_even_when_disabled(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    url = await eleven.tts("Hi", "k")
    monkeypatch.setenv("ELEVEN_DISABLED", "1")
    reload_config()
    assert await eleven.tts("Hi", "k") == url
    assert len(rec.requests) == 1


async def test_api_error_returns_none_and_writes_nothing(rec: Recorder) -> None:
    rec.status, rec.body = 401, b'{"detail": "quota_exceeded"}'
    assert await eleven.tts("Hi", "k") is None
    assert not list((Path(cfg.DATA_DIR) / "audio").glob("*.mp3"))


async def test_synthesize_to_explicit_path(rec: Recorder) -> None:
    dest = Path(cfg.DATA_DIR) / "cues" / "mp3" / "knees_out_1.mp3"
    assert await eleven.synthesize_to("Knees out.", dest)
    assert dest.is_file()
    assert await eleven.synthesize_to("Knees out.", dest)
    assert len(rec.requests) == 1


async def test_credits(rec: Recorder) -> None:
    assert await eleven.credits() == {"used": 2000, "limit": 10000, "remaining": 8000}
