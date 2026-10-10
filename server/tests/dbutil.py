"""Throwaway databases on the local Docker TimescaleDB (never Tiger Cloud)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from server.db import seed
from server.db.apply_sql import apply_file

ADMIN_DSN = os.environ.get("MIRA_TEST_ADMIN_DSN", "postgresql://postgres:mira@localhost:55432/postgres")
# copilot_ro is a cluster-wide role, so tests reuse the local password when one is configured.
RO_PASSWORD = os.environ.get("COPILOT_RO_PASSWORD") or "mira-test-ro"


def _with_db(dsn: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{password}@{host}"
    return urlunsplit((parts.scheme, netloc, f"/{dbname}", parts.query, parts.fragment))


def admin_available() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def ro_dsn(dsn: str) -> str:
    return _with_db(dsn, urlsplit(dsn).path.lstrip("/"), "copilot_ro", RO_PASSWORD)


@contextmanager
def fresh_database() -> Iterator[str]:
    """Create a uniquely named database, yield its DSN, drop it afterwards. Skips if Docker DB is down."""
    if not admin_available():
        pytest.skip("local TimescaleDB (MIRA_TEST_ADMIN_DSN) is unreachable")
    name = f"mira_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _with_db(ADMIN_DSN, name)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def prepare(dsn: str) -> None:
    """schema → aggregates → roles → seed, as setup steps 1-4 do."""
    db_dir = Path(__file__).resolve().parents[1] / "db"
    apply_file(db_dir / "schema.sql", dsn)
    apply_file(db_dir / "aggregates.sql", dsn)
    apply_file(db_dir / "roles.sql", dsn, {"copilot_ro_password": RO_PASSWORD})
    with psycopg.connect(dsn, autocommit=True) as conn:
        seed.seed(conn)
