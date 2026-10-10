from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from retell.lib.webhook_auth import symmetric
from starlette.websockets import WebSocketDisconnect

from server.config import PLACEHOLDER_TOKEN
from server.security import MAX_WEBHOOK_BYTES, require_token
from server.tests.conftest import AUTH, TOKEN

TUNNEL = "mira-test.trycloudflare.com"
RETELL_KEY = "retell-test-key-abcdef123456"
PUBLIC_ROUTES = {"/health", "/retell/webhook"}


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/stats", "/health/data"])
def test_rest_without_token_is_401(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("header", ["Bearer wrong-token", f"Basic {TOKEN}", TOKEN, "Bearer ",
                                    "Bearer tøken".encode("latin-1")])
def test_rest_with_wrong_token_is_401(client: TestClient, header: str | bytes) -> None:
    assert client.get("/stats", headers={"Authorization": header}).status_code == 401


def test_rest_with_token_is_200(client: TestClient) -> None:
    resp = client.get("/stats", headers=AUTH)
    assert resp.status_code == 200
    assert "event_loop_lag_ms" in resp.json()["bridge"]


def test_every_rest_route_requires_token_except_public(client: TestClient) -> None:
    for route in client.app.routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute) or route.path in PUBLIC_ROUTES:
            continue
        calls = {dep.call for dep in route.dependant.dependencies}
        assert require_token in calls, f"{route.path} is missing require_token"


@pytest.mark.parametrize("path", ["/ws/pose", "/ws/live", "/ws/events", "/ws/notifications"])
@pytest.mark.parametrize("query", ["", "?token=wrong", "?token=", "?token=wrong&since_id=0"])
def test_ws_without_valid_token_is_refused(client: TestClient, path: str, query: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path + query):
            pass
    assert exc.value.code == 1008


def test_ws_with_token_receives_data(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/pose?token={TOKEN}") as ws:
        msg = ws.receive_json()
    assert set(msg) == {"t", "joints", "confidence", "load_l_pct", "flags"}


def test_tunnel_host_blocked_except_webhook(make_client: Callable[..., TestClient]) -> None:
    client = make_client(PUBLIC_WEBHOOK_HOST=TUNNEL)
    assert client.get("/patients", headers={**AUTH, "Host": TUNNEL}).status_code == 403
    assert client.get("/stats", headers={**AUTH, "Host": f"{TUNNEL}:443"}).status_code == 403
    assert client.get("/stats", headers={**AUTH, "X-Forwarded-Host": TUNNEL}).status_code == 403
    assert client.get("/health", headers={"Host": TUNNEL}).status_code == 403
    # Allowed through the filter, then stopped by the signature check (no RETELL_API_KEY).
    assert client.post("/retell/webhook", headers={"Host": TUNNEL}, content=b"{}").status_code == 401
    # LAN requests are unaffected.
    assert client.get("/stats", headers=AUTH).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Host": f"{TUNNEL}."},                                  # trailing-dot FQDN
    {"Host": f"{TUNNEL}.:443"},
    {"Host": TUNNEL.upper()},
    {"X-Forwarded-Host": f"proxy.local, {TUNNEL}"},          # tunnel host not first in the list
])
def test_tunnel_host_variants_blocked(make_client: Callable[..., TestClient], headers: dict[str, str]) -> None:
    client = make_client(PUBLIC_WEBHOOK_HOST=TUNNEL)
    assert client.get("/stats", headers={**AUTH, **headers}).status_code == 403


@pytest.mark.parametrize("configured", [f"https://{TUNNEL}/", f"{TUNNEL.upper()}:443", f"{TUNNEL}."])
def test_tunnel_host_config_is_normalized(make_client: Callable[..., TestClient], configured: str) -> None:
    client = make_client(PUBLIC_WEBHOOK_HOST=configured)
    assert client.get("/stats", headers={**AUTH, "Host": TUNNEL}).status_code == 403
    assert client.get("/stats", headers=AUTH).status_code == 200


@pytest.mark.parametrize("path", ["/ws/events", "/ws/notifications"])
def test_tunnel_host_ws_refused(make_client: Callable[..., TestClient], path: str) -> None:
    client = make_client(PUBLIC_WEBHOOK_HOST=TUNNEL)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{path}?token={TOKEN}", headers={"Host": TUNNEL}):
            pass


def test_webhook_without_retell_key_always_401(client: TestClient) -> None:
    body = json.dumps({"event": "call_ended"})
    sig = symmetric["sign"](body, "anything")
    assert client.post("/retell/webhook", content=body, headers={"X-Retell-Signature": sig}).status_code == 401


def test_forged_webhook_is_401(make_client: Callable[..., TestClient]) -> None:
    client = make_client(RETELL_API_KEY=RETELL_KEY)
    body = json.dumps({"event": "call_ended", "call": {"call_id": "c1"}})
    forged = symmetric["sign"](body, "not-the-key")
    stale = symmetric["sign"](body, RETELL_KEY, int((time.time() - 3600) * 1000))
    for headers in ({}, {"X-Retell-Signature": "v=1,d=abc"}, {"X-Retell-Signature": forged},
                    {"X-Retell-Signature": stale}):
        assert client.post("/retell/webhook", content=body, headers=headers).status_code == 401


def test_signed_webhook_is_accepted(make_client: Callable[..., TestClient]) -> None:
    client = make_client(RETELL_API_KEY=RETELL_KEY)
    body = json.dumps({"event": "call_ended"})
    sig = symmetric["sign"](body, RETELL_KEY)
    resp = client.post("/retell/webhook", content=body, headers={"X-Retell-Signature": sig})
    assert resp.status_code == 200
    # Tampered body with the same signature fails.
    assert client.post("/retell/webhook", content=body + " ", headers={"X-Retell-Signature": sig}).status_code == 401


def test_oversized_webhook_is_413_before_signature_check(make_client: Callable[..., TestClient]) -> None:
    client = make_client(RETELL_API_KEY=RETELL_KEY)
    big = b"x" * (MAX_WEBHOOK_BYTES + 1)
    assert client.post("/retell/webhook", content=big).status_code == 413

    def chunked() -> Iterator[bytes]:  # no Content-Length: the cap must hold while streaming
        for _ in range(MAX_WEBHOOK_BYTES // 65536 + 2):
            yield b"x" * 65536

    assert client.post("/retell/webhook", content=chunked()).status_code == 413


def test_demo_trigger_fall_404_when_disabled(client: TestClient) -> None:
    assert client.post("/demo/trigger_fall", headers=AUTH, json={}).status_code == 404


def test_demo_trigger_fall_needs_token(make_client: Callable[..., TestClient]) -> None:
    client = make_client(DEMO_TOOLS_ENABLED="1")
    assert client.post("/demo/trigger_fall", json={}).status_code == 401


def test_demo_trigger_fall_enabled(make_client: Callable[..., TestClient]) -> None:
    client = make_client(DEMO_TOOLS_ENABLED="1")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        resp = client.post("/demo/trigger_fall", headers=AUTH, json={"responds_at_s": 30})
        assert resp.status_code == 200
        assert resp.json()["responds_at_s"] == 30
        for _ in range(500):
            msg = ws.receive_json()
            if msg.get("type") == "fall":
                break
        else:
            pytest.fail("no fall event after trigger")
    assert msg["stage"] == "confirmed"
    assert msg["session_id"] == resp.json()["session_id"]


def test_refuses_to_start_without_token(make_client: Callable[..., TestClient]) -> None:
    with pytest.raises(RuntimeError, match="MIRA_API_TOKEN"):
        make_client(MIRA_API_TOKEN="")


@pytest.mark.parametrize("token, reason", [
    (PLACEHOLDER_TOKEN, "placeholder"),
    ("short-token-123", "shorter than 24"),
])
def test_refuses_to_start_with_placeholder_or_short_token(make_client: Callable[..., TestClient], token: str,
                                                          reason: str) -> None:
    with pytest.raises(RuntimeError, match=reason):
        make_client(MIRA_API_TOKEN=token)


def test_tests_never_read_the_developers_dotenv() -> None:
    from server.tests import dbutil

    assert os.environ.get("MIRA_SKIP_DOTENV") == "1"
    assert dbutil.RO_PASSWORD == "mira-local-ro"  # fixed, never taken from the environment


def test_dashboard_static_path_is_device_dashboard() -> None:
    from server.config import REPO_ROOT
    from server.main import STATIC_PAGES

    assert STATIC_PAGES["dashboard"] == REPO_ROOT / "device" / "dashboard"
    assert STATIC_PAGES["dashboard"].is_dir()  # Victoria's folder exists in the repo
    assert all(path != REPO_ROOT / "device" for path in STATIC_PAGES.values())  # iOS sources never served


def test_dashboard_served_and_ios_sources_not(make_client: Callable[..., TestClient],
                                              monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from server import main

    (tmp_path / "device" / "dashboard").mkdir(parents=True)
    (tmp_path / "device" / "dashboard" / "index.html").write_text("<h1>dashboard</h1>")
    (tmp_path / "device" / "ios").mkdir()
    (tmp_path / "device" / "ios" / "App.swift").write_text("// source")
    monkeypatch.setattr(main, "STATIC_PAGES", {"dashboard": tmp_path / "device" / "dashboard",
                                               "device": tmp_path / "device" / "kiosk"})
    client = make_client()
    resp = client.get("/dashboard/")
    assert resp.status_code == 200 and "dashboard" in resp.text
    assert client.get("/device/ios/App.swift").status_code == 404


def test_cors_allows_separately_hosted_dashboard_but_token_still_required(client: TestClient) -> None:
    origin = "http://localhost:5173"  # e.g. a Vite dev server
    pre = client.options("/patients", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                               "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] in ("*", origin)
    assert "authorization" in pre.headers["access-control-allow-headers"].lower()
    resp = client.get("/stats", headers={"Origin": origin})
    assert resp.status_code == 401
    assert client.get("/stats", headers={"Origin": origin, **AUTH}).status_code == 200
