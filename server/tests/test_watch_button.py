"""watch_button {name: ready|stop} on /ws/events maps to a `command start`/`stop` forwarded to the engine
(the same path as a client `command`), while the Guardian hold still blocks a `ready` during an open
incident. Unknown names are ignored; the raw watch_button event is still injected/broadcast as today."""

from __future__ import annotations
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import TOKEN
from server.tests.test_ws_safety import read_until


def test_watch_button_ready_sends_start_command(make_client: Callable[..., TestClient],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock")
    state = get_state()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(state.bridge, "send_to_engine", sent.append)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "ready"})
        time.sleep(0.2)
    assert [m["name"] for m in sent] == ["start"]
    assert sent[0]["payload"] == {"source": "watch"}


def test_watch_button_stop_sends_stop_command(make_client: Callable[..., TestClient],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock")
    state = get_state()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(state.bridge, "send_to_engine", sent.append)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "stop"})
        time.sleep(0.2)
    assert [m["name"] for m in sent] == ["stop"]
    assert sent[0]["payload"] == {"source": "watch"}


def test_watch_button_unknown_name_is_ignored(make_client: Callable[..., TestClient],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock")
    state = get_state()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(state.bridge, "send_to_engine", sent.append)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "pause"})
        time.sleep(0.2)
    assert sent == []


def test_watch_button_is_still_broadcast(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "ready"})
        read_until(ws, lambda m: m.get("type") == "watch_button" and m.get("name") == "ready")


def test_watch_button_ready_does_not_resume_a_guardian_held_session(
        make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    state = get_state()
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "command", "name": "pain", "payload": {"level": 8}})
        read_until(ws, lambda m: m.get("type") == "alert" and m.get("step") == "detected")
        assert [inc.kind for inc in state.guardian.incidents.values()] == ["pain"]

        sent: list[dict[str, Any]] = []
        monkeypatch.setattr(state.bridge, "send_to_engine", sent.append)
        ws.send_json({"type": "watch_button", "name": "ready"})
        time.sleep(0.2)
    assert sent == [], "watch Ready must never resume a session the Safety Guardian is holding open"
