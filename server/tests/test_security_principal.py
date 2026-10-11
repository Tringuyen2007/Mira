"""Principal resolution: static tokens, patient tokens (hashed, cached, revocable), REST and WS behaviour."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import security
from server.tests.conftest import AUTH, DEVICE_TOKEN, TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn


def test_principal_ownership() -> None:
    assert security.Principal("full").owns("maya") and security.Principal("full").owns("anyone")
    assert security.Principal("patient", "maya").owns("maya")
    assert not security.Principal("patient", "maya").owns("bob")
    assert not security.Principal("device").owns("maya")


def test_token_helpers() -> None:
    tok = security.new_patient_token()
    assert tok.startswith(security.PATIENT_TOKEN_PREFIX) and len(tok) >= 40
    assert len(security.token_hash(tok)) == 64 and security.token_hash(tok) != tok
    assert security.mask(tok) == "…" + tok[-4:]


@pytest.fixture(scope="module")
def dsn():
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), MIRA_DEVICE_TOKEN=DEVICE_TOKEN)


def _insert_device(dsn: str, token: str, patient_id: str = "maya") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO patient_devices (token_hash, patient_id, device_name) VALUES (%s, %s, 'test')",
                     (security.token_hash(token), patient_id))


def test_static_tokens_resolve(client: TestClient) -> None:
    import asyncio

    assert asyncio.run(security.resolve_token(TOKEN)) == security.Principal("full")
    assert asyncio.run(security.resolve_token(DEVICE_TOKEN)) == security.Principal("device")
    assert asyncio.run(security.resolve_token("nope")) is None
    assert asyncio.run(security.resolve_token("pt_unknown")) is None


def test_patient_token_rest_and_revocation(client: TestClient, dsn: str) -> None:
    token = security.new_patient_token()
    _insert_device(dsn, token)
    headers = {"Authorization": f"Bearer {token}"}
    # /stats is full-only (require_token): a patient token is 401 there, as before.
    assert client.get("/stats", headers=headers).status_code == 401
    # /health stays public; a route that takes require_principal accepts the patient (added in Task 5: /me).
    assert client.get("/health").status_code == 200
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE patient_devices SET revoked_at = now() WHERE token_hash = %s",
                     (security.token_hash(token),))
    security.invalidate_token_cache()
    import asyncio

    assert asyncio.run(security.resolve_token(token)) is None


def test_device_token_is_401_on_rest(client: TestClient) -> None:
    assert client.get("/stats", headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}).status_code == 401


def test_ws_scope_returns_principals(client: TestClient, dsn: str) -> None:
    token = security.new_patient_token()
    _insert_device(dsn, token)
    # A patient socket is accepted on /ws/live (Task 7 filters its traffic); unknown tokens still close 1008.
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        msg = ws.receive_json()
        assert "phase" in msg or msg.get("type") in ("live", "ping")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/live?token=pt_bogus"):
            pass
    assert exc.value.code == 1008
