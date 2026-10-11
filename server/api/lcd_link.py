"""GET /lcd/{key}: a shareable link that opens the LCD kiosk already paired.

Whoever has the link gets the kiosk's watch-only device token (MIRA_DEVICE_TOKEN), so the key (LCD_LINK_KEY)
is a long random secret; any other path, or an unset key or device token, is a plain 404. The token travels in
the redirect's `#token=` fragment, which the kiosk saves and strips, exactly like a hand-typed pairing link.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from server.config import cfg

router = APIRouter()


@router.get("/lcd/{key}", include_in_schema=False)
async def lcd_link(key: str) -> RedirectResponse:
    want = cfg.LCD_LINK_KEY
    if not want or not cfg.MIRA_DEVICE_TOKEN or not secrets.compare_digest(key.encode(), want.encode()):
        raise HTTPException(status_code=404, detail="Not Found")
    return RedirectResponse(f"/device/#token={quote(cfg.MIRA_DEVICE_TOKEN, safe='')}", status_code=302,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
