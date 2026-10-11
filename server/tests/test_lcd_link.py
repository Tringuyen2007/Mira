"""GET /lcd/{key}: the shareable link that opens the LCD kiosk already paired."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from server.config import cfg, reload_config
from server.tests.conftest import DEVICE_TOKEN, TOKEN

KEY = "lcd-link-key-0123456789abcdefXYZ"


def test_the_link_redirects_to_the_kiosk_with_the_device_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_DEVICE_TOKEN=DEVICE_TOKEN, LCD_LINK_KEY=KEY)
    r = client.get(f"/lcd/{KEY}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/device/#token={DEVICE_TOKEN}"  # fragment: never sent back to the server
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("env, path", [
    ({"MIRA_DEVICE_TOKEN": DEVICE_TOKEN, "LCD_LINK_KEY": KEY}, f"/lcd/{KEY[:-1]}"),  # wrong key
    ({"MIRA_DEVICE_TOKEN": DEVICE_TOKEN, "LCD_LINK_KEY": KEY}, "/lcd/"),
    ({"MIRA_DEVICE_TOKEN": DEVICE_TOKEN}, f"/lcd/{KEY}"),  # link not configured
    ({"MIRA_DEVICE_TOKEN": "", "LCD_LINK_KEY": KEY}, f"/lcd/{KEY}"),  # no device token to hand out
])
def test_anything_else_is_a_plain_404(make_client: Callable[..., TestClient], env: dict[str, str],
                                      path: str) -> None:
    r = make_client(**env).get(path, follow_redirects=False)
    assert r.status_code == 404 and DEVICE_TOKEN not in r.text


def test_the_link_never_needs_or_leaks_the_api_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_DEVICE_TOKEN=DEVICE_TOKEN, LCD_LINK_KEY=KEY)
    r = client.get(f"/lcd/{KEY}", follow_redirects=False)
    assert TOKEN not in r.headers["location"] and TOKEN not in r.text


@pytest.mark.parametrize("key, problem", [
    ("short-key", "shorter than"),
    (TOKEN, "must differ"),
    (DEVICE_TOKEN, "must differ"),
])
def test_weak_link_keys_are_refused(monkeypatch: pytest.MonkeyPatch, key: str, problem: str) -> None:
    monkeypatch.setenv("MIRA_DEVICE_TOKEN", DEVICE_TOKEN)
    monkeypatch.setenv("LCD_LINK_KEY", key)
    reload_config()
    assert problem in (cfg.lcd_link_problem() or "")


def test_the_link_key_is_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LCD_LINK_KEY", KEY)
    reload_config()
    assert KEY in cfg.secret_values() and cfg.redacted()["LCD_LINK_KEY"] == "set"


def test_the_static_web_copy_is_served_without_any_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_DEVICE_TOKEN=DEVICE_TOKEN, LCD_LINK_KEY=KEY)
    page = client.get("/lcd-web/")
    assert page.status_code == 200 and 'src="js/demo.js"' in page.text and "screen-nav" in page.text
    for path in ("js/demo.js", "js/state.js", "js/view.js", "js/mascot.js", "css/web.css", "assets/mira/mascot.json"):
        r = client.get(f"/lcd-web/{path}")
        assert r.status_code == 200, path
        assert DEVICE_TOKEN not in r.text and TOKEN not in r.text and KEY not in r.text
    demo = client.get("/lcd-web/js/demo.js").text
    assert "WebSocket" not in demo and "/ws/" not in demo  # demo data only, no server connection
