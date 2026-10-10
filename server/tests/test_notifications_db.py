"""Notifications backed by a fresh local TimescaleDB: DB ids, REST history/unread and the since_id replay all
survive a server restart; a row the database rejects is still delivered live."""

from __future__ import annotations

import functools
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.config import reload_config
from server.state import get_state
from server.tests.conftest import AUTH, TOKEN
from server.tests.dbutil import fresh_database, prepare


@pytest.fixture(scope="module")
def notify_dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def _start(monkeypatch: pytest.MonkeyPatch, dsn: str) -> TestClient:
    monkeypatch.setenv("TIGER_DSN", dsn)
    reload_config()
    from server.main import create_app

    client = TestClient(create_app())
    client.__enter__()
    return client


def _create(client: TestClient, **overrides: Any) -> int | None:
    kwargs: dict[str, Any] = dict(kind="milestone", severity="low", therapist_id="okafor", patient_id="maya",
                                  title="t", body=None, link=None, source_table=None, source_id=None,
                                  dedupe_key=None)
    kwargs.update(overrides)
    service = get_state().notifications
    return client.portal.call(functools.partial(service.create, **kwargs))  # type: ignore[union-attr]


def test_history_unread_and_replay_survive_a_restart(monkeypatch: pytest.MonkeyPatch, notify_dsn: str) -> None:
    first = _start(monkeypatch, notify_dsn)
    try:
        ids = [_create(first, dedupe_key=f"restart:{i}") for i in range(4)]
        urgent = _create(first, kind="alert", severity="urgent", dedupe_key="restart:urgent")
        assert all(isinstance(i, int) and i > 0 for i in [*ids, urgent])
        assert first.post(f"/notifications/{ids[0]}/read", headers=AUTH).status_code == 200
    finally:
        first.__exit__(None, None, None)

    second = _start(monkeypatch, notify_dsn)  # a new process: empty memory, same database
    try:
        rows = second.get("/notifications?limit=50", headers=AUTH).json()["notifications"]
        assert [r["id"] for r in rows][:5] == [urgent, *reversed(ids)]  # DB ids, newest first
        assert isinstance(rows[0]["time"], float)
        by_id = {r["id"]: r for r in rows}
        assert by_id[ids[0]]["read_at"] is not None and by_id[ids[1]]["read_at"] is None
        count = second.get("/notifications/count", headers=AUTH).json()
        assert count == {"unread": 4, "urgent_unacked": 1}

        # dedupe still holds across the restart (the database's ON CONFLICT, not process memory)
        assert _create(second, dedupe_key="restart:1") is None

        # security smoke row: a reconnecting dashboard gets exactly the rows after its last id, in order
        with second.websocket_connect(f"/ws/notifications?token={TOKEN}&since_id={ids[1]}") as ws:
            replayed = [ws.receive_json()["id"] for _ in range(3)]
            live = _create(second, dedupe_key="restart:live")
            assert ws.receive_json()["id"] == live
        assert replayed == [ids[2], ids[3], urgent]

        polled = second.get(f"/notifications?since_id={ids[2]}&limit=2", headers=AUTH).json()["notifications"]
        assert [r["id"] for r in polled] == [ids[3], urgent]  # oldest-first paging for the polling fallback

        acked = second.post(f"/notifications/{urgent}/ack", json={"therapist_id": "okafor"}, headers=AUTH)
        assert acked.status_code == 200 and acked.json()["acked_by"] == "okafor"
        assert second.get("/notifications/count", headers=AUTH).json()["urgent_unacked"] == 0
        marked = second.post("/notifications/read-all", headers=AUTH).json()["marked_read"]
        assert marked >= 3
        assert second.get("/notifications/count", headers=AUTH).json()["unread"] == 0
        assert second.post("/notifications/999999/read", headers=AUTH).status_code == 404
    finally:
        second.__exit__(None, None, None)


def test_a_row_the_database_rejects_is_still_delivered_live(monkeypatch: pytest.MonkeyPatch,
                                                            notify_dsn: str) -> None:
    client = _start(monkeypatch, notify_dsn)
    try:
        with client.websocket_connect(f"/ws/notifications?token={TOKEN}&therapist_id=ghost") as ws:
            # therapist_id violates the therapists FK: the INSERT fails, the urgent row must not vanish
            nid = _create(client, kind="alert", severity="urgent", therapist_id="ghost", dedupe_key="fk:1")
            msg = ws.receive_json()
        assert nid is not None and nid < 0 and msg["id"] == nid
        count = client.get("/notifications/count?therapist_id=ghost", headers=AUTH).json()
        assert count == {"unread": 1, "urgent_unacked": 1}
        rows = client.get("/notifications?therapist_id=ghost", headers=AUTH).json()["notifications"]
        assert [r["id"] for r in rows] == [nid]
        assert client.post(f"/notifications/{nid}/ack", json={"therapist_id": "x"}, headers=AUTH).status_code == 200
    finally:
        client.__exit__(None, None, None)
