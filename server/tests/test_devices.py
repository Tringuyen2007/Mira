from __future__ import annotations

from typing import Any

from server.devices import DeviceRegistry


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


def make(now: list[float]) -> tuple[DeviceRegistry, FakeHub]:
    hub = FakeHub()
    return DeviceRegistry(hub, stale_s=15.0, clock=lambda: now[0]), hub  # type: ignore[arg-type]


def test_online_after_connect_and_health_then_offline_on_disconnect() -> None:
    now = [100.0]
    reg, hub = make(now)
    reg.connected()
    assert not reg.online, "a socket alone is not enough: the page must report health"
    reg.on_health({"type": "device_health", "app": "lcd", "t": 100.0, "screen": "1024x600"})
    assert reg.online
    assert hub.events[-1]["type"] == "device_status" and hub.events[-1]["online"] is True
    assert hub.events[-1]["device"] == "lcd" and hub.events[-1]["last_health"]["screen"] == "1024x600"
    reg.disconnected()
    assert not reg.online and hub.events[-1]["online"] is False


def test_goes_offline_when_health_is_stale_and_emits_once() -> None:
    now = [100.0]
    reg, hub = make(now)
    reg.connected()
    reg.on_health({"type": "device_health", "app": "lcd"})
    now[0] = 116.0
    reg.check()
    reg.check()
    assert not reg.online
    assert [e["online"] for e in hub.events] == [True, False]


def test_status_reports_last_seen() -> None:
    now = [50.0]
    reg, _ = make(now)
    assert reg.status() == {"online": False, "connections": 0, "last_seen": None, "last_health": None}
    reg.connected()
    reg.on_health({"type": "device_health", "app": "lcd"})
    assert reg.status()["last_seen"] == 50.0 and reg.status()["connections"] == 1
