from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.devices import DEVICE_EVENT_TYPES
from server.main import sanitize_device_health
from server.tests.conftest import AUTH, DEVICE_TOKEN, TOKEN


def read_until(ws: Any, pred: Callable[[dict[str, Any]], bool], limit: int = 400) -> dict[str, Any]:
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("message not seen")


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(MIRA_DEVICE_TOKEN=DEVICE_TOKEN)


@pytest.mark.parametrize("path", ["/ws/live", "/ws/events"])
def test_device_token_opens_kiosk_channels(client: TestClient, path: str) -> None:
    with client.websocket_connect(f"{path}?token={DEVICE_TOKEN}") as ws:
        assert isinstance(ws.receive_json(), dict)


@pytest.mark.parametrize("path", ["/ws/pose", "/ws/notifications"])
def test_device_token_is_refused_elsewhere(client: TestClient, path: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"{path}?token={DEVICE_TOKEN}") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_device_token_gets_401_on_rest(client: TestClient) -> None:
    for path in ("/stats", "/health/data", "/alerts", "/patients"):
        assert client.get(path, headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}).status_code == 401


def test_device_token_unset_means_no_device_scope(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/live?token={DEVICE_TOKEN}") as ws:
            ws.receive_json()


def test_device_cannot_send_commands(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as full, \
            client.websocket_connect(f"/ws/events?token={DEVICE_TOKEN}") as device:
        # Same socket, in order: if the command were accepted it would be injected before the health message.
        device.send_json({"type": "command", "name": "stop", "payload": {}})
        device.send_json({"type": "device_health", "app": "watch", "screen": "1024x600", "evil": "x"})
        seen: list[str] = []
        health = read_until(full, lambda m: seen.append(m.get("type")) or m.get("type") == "device_health")
        assert "command" not in seen and "stopped" not in seen
        assert health["app"] == "lcd" and "evil" not in health
        status = read_until(full, lambda m: m.get("type") == "device_status")
        assert status["online"] is True


def test_device_receives_only_allow_listed_events(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/events?token={DEVICE_TOKEN}") as device, \
            client.websocket_connect(f"/ws/events?token={TOKEN}") as full:
        full.send_json({"type": "watch_button", "name": "x"})  # echoed to full clients only
        read_until(full, lambda m: m["type"] == "watch_button")  # proves it was published
        seen: list[str] = []
        read_until(device, lambda m: seen.append(m["type"]) or m["type"] == "hr")  # mock hr ~1/s
        assert seen and all(kind in DEVICE_EVENT_TYPES for kind in seen), seen
        assert "watch_button" not in seen


def test_health_data_reports_lcd(client: TestClient) -> None:
    body = client.get("/health/data", headers=AUTH).json()
    assert body["lcd"]["online"] is False


def test_sanitize_device_health_keeps_allow_listed_keys() -> None:
    out = sanitize_device_health({"type": "device_health", "app": "watch", "battery_pct": 50, "x": 1})
    assert out["type"] == "device_health" and out["app"] == "lcd" and out["battery_pct"] == 50
    assert "x" not in out and isinstance(out["t"], float)


def test_sanitize_device_health_truncates_long_strings() -> None:
    out = sanitize_device_health({"type": "device_health", "screen": "s" * 500, "version": "v" * 65})
    assert out["screen"] == "s" * 64 and out["version"] == "v" * 64


def test_sanitize_device_health_drops_nested_values() -> None:
    out = sanitize_device_health({"type": "device_health", "screen": {"a": "b" * 10_000}, "version": [1, 2],
                                  "battery_pct": None})
    assert "screen" not in out and "version" not in out and "battery_pct" not in out


def test_sanitize_device_health_clamps_battery() -> None:
    assert sanitize_device_health({"type": "device_health", "battery_pct": 150})["battery_pct"] == 100
    assert sanitize_device_health({"type": "device_health", "battery_pct": -5})["battery_pct"] == 0


def test_sanitize_device_health_drops_bool_battery() -> None:
    assert "battery_pct" not in sanitize_device_health({"type": "device_health", "battery_pct": True})
