"""Watch traffic on /ws/events: watch_button resume, watch_sync to ClockSync (not broadcast), wrist sanitising,
and the per-client filters for the new server events."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import main
from server.db.wrist import WristRecorder
from server.db.writer import wrist_row
from server.devices import DEVICE_EVENT_TYPES
from server.state import get_state
from server.tests.conftest import TOKEN
from server.tests.test_ws_safety import read_until
from server.watch_wire import sanitize_watch_sync, sanitize_wrist


# ---- sanitising -------------------------------------------------------------------------------------------------

def test_sanitize_wrist_keeps_known_finite_fields() -> None:
    now = time.time()
    msg = {"type": "wrist", "t": now, "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
           "rotation_deg": 41.54, "extra": "x"}
    assert sanitize_wrist(msg) == {"type": "wrist", "t": now, "quat": [0.99, 0.01, 0.05, 0.02],
                                   "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}


@pytest.mark.parametrize("msg", [
    {"type": "wrist"},
    {"type": "wrist", "rotation_deg": "41"},
    {"type": "wrist", "rotation_deg": 400.0},
    {"type": "wrist", "quat": [1, 0, 0], "accel": [0, 0, "x"]},
    {"type": "wrist", "rotation_deg": True},
    {"type": "wrist", "t": time.time()},  # a time alone carries nothing
])
def test_sanitize_wrist_drops_empty_or_bad(msg: dict[str, Any]) -> None:
    assert sanitize_wrist(msg) is None


def test_sanitize_wrist_drops_an_implausible_time() -> None:
    clean = sanitize_wrist({"type": "wrist", "t": time.time() * 1000, "rotation_deg": 10})
    assert clean == {"type": "wrist", "rotation_deg": 10.0}  # inject() stamps the server time instead


def test_sanitize_watch_sync() -> None:
    ok = {"type": "watch_sync", "t0": 1791662259.0, "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9]}
    assert sanitize_watch_sync(ok) == {"type": "watch_sync", "t0": 1791662259.0, "dt_ms": 20.0,
                                       "accel_mag": [0.1, 0.4, 0.9]}
    for bad in ({**ok, "t0": "x"}, {**ok, "dt_ms": 1}, {**ok, "dt_ms": 500}, {**ok, "accel_mag": []},
                {**ok, "accel_mag": [0.1] * 61}, {**ok, "accel_mag": [0.1, -1.0]}, {**ok, "accel_mag": [0.1, 99]},
                {**ok, "accel_mag": "0.1"}):
        assert sanitize_watch_sync(bad) is None


# ---- routing ------------------------------------------------------------------------------------------------------

def test_watch_sync_is_a_client_and_patient_type() -> None:
    assert "watch_sync" in main.CLIENT_EVENT_TYPES and "watch_sync" in main.PATIENT_CLIENT_EVENT_TYPES
    assert main.WATCH_BUTTON_COMMANDS == {"ready": "start", "stop": "stop", "resume": "start"}


def test_watch_button_resume_sends_start(make_client: Callable[..., TestClient],
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert [(m["name"], m["payload"]) for m in sent] == [("start", {"source": "watch"})]


def test_watch_button_resume_is_blocked_while_the_guardian_holds(make_client: Callable[..., TestClient],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "command", "name": "pain", "payload": {"level": 8}})
        read_until(ws, lambda m: m.get("type") == "alert" and m.get("step") == "detected")
        sent: list[dict[str, Any]] = []
        monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert sent == []


class FakeClockSync:
    def __init__(self) -> None:
        self.windows: list[tuple[dict[str, Any], str]] = []

    def on_window(self, msg: dict[str, Any], session_id: str) -> None:
        self.windows.append((msg, session_id))


def test_watch_sync_goes_to_clock_sync_and_is_not_broadcast(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock")
    state = get_state()
    fake = FakeClockSync()
    state.clock_sync = fake
    window = {"type": "watch_sync", "t0": time.time(), "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9, 1.3, 0.8, 0.2]}
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json(window)
        ws.send_json({"type": "watch_sync", "t0": "bad", "dt_ms": 20, "accel_mag": [0.1]})
        ws.send_json({"type": "watch_button", "name": "marker"})
        seen: list[str] = []
        read_until(ws, lambda m: seen.append(str(m.get("type"))) or m.get("type") == "watch_button")
    assert "watch_sync" not in seen
    assert len(fake.windows) == 1
    msg, sid = fake.windows[0]
    assert msg["accel_mag"] == window["accel_mag"] and sid == state.sessions.current.session_id


def test_wrist_is_sanitised_then_broadcast(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "wrist", "rotation_deg": "nope"})  # dropped
        ws.send_json({"type": "wrist", "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
                      "rotation_deg": 41.5, "junk": 1})
        got = read_until(ws, lambda m: m.get("type") == "wrist")
    assert got["rotation_deg"] == 41.5 and "junk" not in got and got["session_id"]


def test_filters_for_the_new_server_events() -> None:
    assert "breather" in DEVICE_EVENT_TYPES and "hr" in DEVICE_EVENT_TYPES
    assert main._device_accepts({"type": "breather"})
    for kind in ("rotation_rep", "rotation_set", "hr_recovery", "sync_progress", "limits_changed",
                 "watch_features_changed", "haptic"):
        assert not main._device_accepts({"type": kind})
    accept = main._patient_accepts("maya")
    assert accept({"type": "limits_changed", "patient_id": "maya"})
    assert not accept({"type": "limits_changed", "patient_id": "p02"})
    assert not accept({"type": "watch_features_changed", "patient_id": "p02"})


# ---- wrist persistence (unit) ------------------------------------------------------------------------------------

class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def submit(self, frame: dict[str, Any]) -> None:
        self.rows.append(frame)


async def test_wrist_recorder_throttles_per_patient_and_applies_the_offset() -> None:
    now = [100.0]
    writer = FakeWriter()
    owners = {"s1": "maya", "s2": "liam"}
    recorder = WristRecorder(writer, owners.get, clock=lambda: now[0])
    for i in range(5):  # five samples within 50 ms → one row
        await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1000.0 + i / 100, "rotation_deg": i})
    now[0] += 0.2  # (0.1 exactly would be 0.0999… in floating point)
    await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1000.2, "rotation_deg": 9})
    await recorder.on_event({"type": "wrist", "session_id": "s2", "t": 1000.2, "rotation_deg": 3})  # other patient
    await recorder.on_event({"type": "wrist", "session_id": "unknown", "t": 1000.2, "rotation_deg": 3})
    await recorder.on_event({"type": "hr", "session_id": "s1", "t": 1000.2, "bpm": 90})
    assert [(r["patient_id"], r["rotation_deg"]) for r in writer.rows] == [("maya", 0), ("maya", 9), ("liam", 3)]
    recorder.offset_s = lambda sid: 0.25 if sid == "s1" else None
    now[0] += 0.2
    await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1001.0, "rotation_deg": 1})
    assert writer.rows[-1]["t"] == pytest.approx(1000.75)  # watch clock minus offset = server clock


def test_wrist_row_maps_quat_wxyz_and_accel() -> None:
    row = wrist_row({"t": 1791662259.706, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f",
                     "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}, "maya")
    assert row[1:] == ("maya", "219b6491-aff5-461e-9e5e-39ef73bde60f", 0.99, 0.01, 0.05, 0.02, 41.5,
                       [0.02, -9.79, 0.15])
    bare = wrist_row({"t": 1.0, "session_id": "s", "rotation_deg": 5.0}, "maya")
    assert bare[3:7] == (None, None, None, None) and bare[8] is None
