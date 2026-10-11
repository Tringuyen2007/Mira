"""MediaMTX external authentication (docs/vision-media-contract.md, "Access and sharing").

MediaMTX is configured with ``authMethod: http`` and ``authHTTPAddress: http://127.0.0.1:8000/media/auth``. For every
publish (WHIP from the camera laptop) and read (WHEP from the therapist dashboard) it POSTs the caller's credentials
here and only proceeds on 200. Publishing needs MIRA_VISION_TOKEN, playback needs the dashboard/API token, both
as ``Authorization: Bearer <token>`` (MediaMTX forwards it as ``token``) or as the password of a basic/query
credential. Paths are restricted to ``mira/<session_id>``. No video is ever stored.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.config import cfg
from server.logging_setup import get_logger

log = get_logger("media")
router = APIRouter()

PATH_RE = re.compile(r"^mira/[A-Za-z0-9-]{1,64}$")
PUBLISH_ACTIONS = {"publish"}
READ_ACTIONS = {"read", "playback"}


class MediaAuthIn(BaseModel):
    action: str = Field(max_length=16)
    path: str = Field(default="", max_length=128)
    user: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=256)
    token: str = Field(default="", max_length=256)
    protocol: str = Field(default="", max_length=16)
    ip: str = Field(default="", max_length=64)
    query: str = Field(default="", max_length=512)


def _matches(presented: str, expected: str) -> bool:
    return bool(expected) and bool(presented) and secrets.compare_digest(presented, expected)


def media_auth_ok(body: MediaAuthIn) -> bool:
    """Pure decision: which credential unlocks which action on which path."""
    if body.action == "api":  # MediaMTX control API is never exposed through this hook
        return False
    if body.action in PUBLISH_ACTIONS or body.action in READ_ACTIONS:
        if not PATH_RE.match(body.path):
            return False
    presented = body.token or body.password
    if body.action in PUBLISH_ACTIONS:
        return _matches(presented, cfg.MIRA_VISION_TOKEN) and cfg.vision_token_problem() is None
    if body.action in READ_ACTIONS:
        return _matches(presented, cfg.MIRA_API_TOKEN)
    return False


@router.post("/media/auth")
async def media_auth(body: MediaAuthIn) -> dict[str, Any]:
    if not media_auth_ok(body):
        log.info("media auth refused", extra={"action": body.action, "path": body.path, "ip": body.ip})
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"ok": True}
