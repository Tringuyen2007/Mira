from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from server.config import PLACEHOLDER_TOKEN, Config
from server.tests.conftest import DEVICE_TOKEN, TOKEN


def test_device_token_is_optional() -> None:
    assert Config(MIRA_API_TOKEN=TOKEN).device_token_problem() is None


@pytest.mark.parametrize("value, problem", [
    ("short", "shorter than"),
    (PLACEHOLDER_TOKEN, "placeholder"),
    (TOKEN, "must differ"),
])
def test_bad_device_token_is_a_problem(value: str, problem: str) -> None:
    found = Config(MIRA_API_TOKEN=TOKEN, MIRA_DEVICE_TOKEN=value).device_token_problem()
    assert found is not None and problem in found


def test_server_refuses_to_start_with_bad_device_token(make_client: Callable[..., TestClient]) -> None:
    with pytest.raises(RuntimeError, match="MIRA_DEVICE_TOKEN"):
        make_client(MIRA_DEVICE_TOKEN="short")


def test_device_token_is_redacted() -> None:
    cfg = Config(MIRA_API_TOKEN=TOKEN, MIRA_DEVICE_TOKEN=DEVICE_TOKEN)
    assert cfg.redacted()["MIRA_DEVICE_TOKEN"] == "set"
    assert DEVICE_TOKEN in cfg.secret_values()
