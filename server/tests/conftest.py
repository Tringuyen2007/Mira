from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import reload_config
from server.logging_setup import shutdown_logging

TOKEN = "test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

BASELINE_ENV = {
    "MIRA_API_TOKEN": TOKEN,
    "MIRA_SOURCE": "mock",
    "ENGINE_MODE": "thread",
    "MOCK_PATIENT_ID": "maya",
    "MOCK_FALL_RESPONDS_AT": "",
    "DEMO_TOOLS_ENABLED": "0",
    "PUBLIC_WEBHOOK_HOST": "",
    "RETELL_API_KEY": "",
    "TIGER_DSN": "",
    "COPILOT_DSN": "",
    "GEMINI_API_KEY": "",
    "GEMINI_MODE": "fake",
    "GEMINI_DISABLED": "0",
    "ELEVEN_DISABLED": "0",
    "ELEVEN_API_KEY": "",
    "DEMO_EMERGENCY_PHONE": "+15550100111",
    "DEMO_THERAPIST_PHONE": "+15550100222",
}


@pytest.fixture(autouse=True)
def baseline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for key, value in BASELINE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    reload_config()
    yield
    shutdown_logging()


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    """Build the app with extra env overrides and run its lifespan."""
    clients: list[TestClient] = []

    def factory(**env: str) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        reload_config()
        from server.main import create_app

        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)
