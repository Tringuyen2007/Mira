"""MediaMTX asks /media/auth before a WHIP publish or WHEP read; only the right token on a mira/<sid> path passes."""
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from server.tests.conftest import TOKEN

VISION_TOKEN = "vision-token-0123456789abcdef"


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(MIRA_VISION_TOKEN=VISION_TOKEN)


def auth(client: TestClient, **body: str) -> int:
    return client.post("/media/auth", json={"path": "mira/abc-123", **body}).status_code


def test_publish_needs_the_vision_token(client: TestClient) -> None:
    assert auth(client, action="publish", token=VISION_TOKEN) == 200
    assert auth(client, action="publish", password=VISION_TOKEN) == 200
    assert auth(client, action="publish", token=TOKEN) == 401  # the dashboard token can't publish
    assert auth(client, action="publish", token="") == 401
    assert auth(client, action="publish", token="wrong") == 401


def test_read_needs_the_dashboard_token(client: TestClient) -> None:
    assert auth(client, action="read", token=TOKEN) == 200
    assert auth(client, action="read", password=TOKEN) == 200
    assert auth(client, action="read", token=VISION_TOKEN) == 401  # the camera laptop can't watch
    assert auth(client, action="read", token="") == 401


def test_only_session_paths_and_no_api(client: TestClient) -> None:
    assert client.post("/media/auth", json={"action": "publish", "path": "other/x", "token": VISION_TOKEN}).status_code == 401
    assert client.post("/media/auth", json={"action": "read", "path": "", "token": TOKEN}).status_code == 401
    assert client.post("/media/auth", json={"action": "api", "path": "", "token": TOKEN}).status_code == 401


def test_publish_refused_when_vision_token_unset(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_VISION_TOKEN="")
    assert auth(client, action="publish", token="") == 401
