"""ElevenLabs text-to-speech (TRI §6.4). Every MP3 is generated once and cached on disk.

Files live under DATA_DIR/audio/<sha>.mp3 (greetings, summaries) or DATA_DIR/cues/mp3/ (cue library);
both are served at /audio/<file>.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from pathlib import Path
from typing import Any

import httpx

from server.config import cfg
from server.logging_setup import get_logger

log = get_logger("ai.eleven")

API_BASE = "https://api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_128"
TIMEOUT_S = 10.0

# Tests install an httpx.MockTransport here; None = real network.
transport: httpx.AsyncBaseTransport | None = None


def audio_dir() -> Path:
    return Path(cfg.DATA_DIR) / "audio"


def audio_name(text: str, cache_key: str) -> str:
    """File name for a text: changes when the text, voice or model changes, so stale audio is never reused."""
    blob = "\x1f".join((cache_key, text, cfg.ELEVEN_VOICE_ID or "", cfg.ELEVEN_MODEL))
    return hashlib.sha256(blob.encode()).hexdigest()[:32] + ".mp3"


def enabled() -> bool:
    return not cfg.ELEVEN_DISABLED and bool(cfg.ELEVEN_API_KEY) and bool(cfg.ELEVEN_VOICE_ID)


async def tts(text: str, cache_key: str) -> str | None:
    """`/audio/<file>.mp3` for `text`, generating it on a cache miss; None when voice is off or fails."""
    name = audio_name(text, cache_key)
    if await synthesize_to(text, audio_dir() / name):
        return f"/audio/{name}"
    return None


async def synthesize_to(text: str, dest: Path) -> bool:
    """Make sure `dest` holds `text` as MP3. An existing file is reused without a request."""
    if await asyncio.to_thread(dest.is_file):
        return True
    if not enabled():
        return False
    audio = await _request(text)
    if audio is None:
        return False
    try:
        await asyncio.to_thread(_write_atomic, dest, audio)
    except OSError as exc:
        log.warning("tts file write failed", extra={"error": repr(exc)})
        return False
    return True


async def _request(text: str) -> bytes | None:
    url = f"{API_BASE}/v1/text-to-speech/{cfg.ELEVEN_VOICE_ID}"
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as client:
            resp = await client.post(url, params={"output_format": OUTPUT_FORMAT},
                                     headers={"xi-api-key": cfg.ELEVEN_API_KEY or "", "accept": "audio/mpeg"},
                                     json={"text": text, "model_id": cfg.ELEVEN_MODEL})
    except httpx.HTTPError as exc:
        log.warning("tts request failed", extra={"error": type(exc).__name__})
        return None
    if resp.status_code != 200 or not resp.content:
        log.warning("tts rejected", extra={"status": resp.status_code, "body": resp.text[:200]})
        return None
    log.info("tts generated", extra={"chars": len(text), "bytes": len(resp.content)})
    return resp.content


def _write_atomic(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


async def credits() -> dict[str, Any] | None:
    """Characters used / limit / remaining this month, or None if unavailable. Costs no credits."""
    if not cfg.ELEVEN_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as client:
            resp = await client.get(f"{API_BASE}/v1/user/subscription", headers={"xi-api-key": cfg.ELEVEN_API_KEY})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("credits check failed", extra={"error": type(exc).__name__})
        return None
    used, limit = int(data.get("character_count", 0)), int(data.get("character_limit", 0))
    return {"used": used, "limit": limit, "remaining": limit - used}
