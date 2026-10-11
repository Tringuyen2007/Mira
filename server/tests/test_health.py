"""GET /health: liveness plus the notifier's call mode, so a deploy can refuse a box that fell back to log-only."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient


def test_health_reports_log_only_when_calls_are_disabled(make_client: Callable[..., TestClient]) -> None:
    body = make_client().get("/health").json()
    assert body["status"] == "ok"
    assert body["calls"] == "log_only"


def test_health_reports_live_when_calls_are_on_and_retell_is_configured(make_client: Callable[..., TestClient]) -> None:
    client = make_client(CALLS_DISABLED="0", RETELL_API_KEY="key_test_not_real_0123456789",
                         RETELL_FROM_NUMBER="+15550100999", RETELL_AGENT_ID="agent_test")
    assert client.get("/health").json()["calls"] == "live"


def test_health_reports_log_only_when_calls_are_on_but_retell_is_incomplete(
        make_client: Callable[..., TestClient]) -> None:
    client = make_client(CALLS_DISABLED="0", RETELL_API_KEY="", RETELL_FROM_NUMBER="", RETELL_AGENT_ID="")
    assert client.get("/health").json()["calls"] == "log_only"
