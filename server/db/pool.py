"""Connection pools: the app pool (TIGER_DSN, read/write) and the Copilot pool (COPILOT_DSN, copilot_ro only)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from server.logging_setup import get_logger

log = get_logger("db")

Params = Sequence[Any] | Mapping[str, Any] | None

CONNECT_KWARGS: dict[str, Any] = {
    "connect_timeout": 5,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}
CHECKOUT_TIMEOUT_S = 3.0
# After a connectivity failure, calls fail immediately for this long instead of each waiting for a checkout
# timeout (the pools keep reconnecting in the background).
FAIL_FAST_S = 5.0
DEFAULT_QUERY_TIMEOUT_S = 10.0
# psycopg_pool doubles its reconnect delay without a cap and gives up after `reconnect_timeout` (default 300 s,
# by which point the delay is ~2 min). A short series keeps every delay <= ~15 s; after it gives up, the keeper
# task below starts a new series every KEEPER_INTERVAL_S while the database is unavailable, forever.
RECONNECT_TIMEOUT_S = 30.0
KEEPER_INTERVAL_S = 5.0


class DBError(Exception):
    """Any database failure (unreachable, timeout, SQL error). The message is safe to log (no DSN)."""


async def _configure_ro(conn: psycopg.AsyncConnection[Any]) -> None:
    await conn.set_read_only(True)


class Database:
    """Async pools for the app and the read-only Copilot role.

    Every method raises DBError on failure; `available` turns False after a connectivity failure and True
    again after the next successful call. Fire-and-forget writes go through server.db.writes, which catches.
    """

    def __init__(self, dsn: str, ro_dsn: str | None = None, *, min_size: int = 1, max_size: int = 6,
                 reconnect_timeout_s: float = RECONNECT_TIMEOUT_S,
                 keeper_interval_s: float = KEEPER_INTERVAL_S) -> None:
        self._pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=False, name="app",
            kwargs={"autocommit": True, "row_factory": dict_row, "application_name": "mira-server",
                    **CONNECT_KWARGS},
            check=AsyncConnectionPool.check_connection, reconnect_timeout=reconnect_timeout_s)
        self._ro_pool: AsyncConnectionPool[Any] | None = None
        if ro_dsn:
            self._ro_pool = AsyncConnectionPool(
                ro_dsn, min_size=1, max_size=3, open=False, name="copilot_ro",
                kwargs={"row_factory": dict_row, "application_name": "mira-copilot", **CONNECT_KWARGS},
                configure=_configure_ro, check=AsyncConnectionPool.check_connection,
                reconnect_timeout=reconnect_timeout_s)
        self._ok = False
        self._ro_ok = True  # only a copilot_ro connectivity failure turns this off (it never affects `available`)
        self._opened = False
        self._failed_at = 0.0
        self._keeper_interval = keeper_interval_s
        self._keeper: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self._opened and self._ok

    @property
    def has_ro(self) -> bool:
        return self._ro_pool is not None

    async def open(self, wait_s: float = 2.0) -> None:
        """Open the pools without waiting for them to fill (open(wait=True) closes a pool it can't fill), then
        probe once. If the database is unreachable the server still starts; the pools keep reconnecting."""
        self._opened = True
        for pool in (self._pool, self._ro_pool):
            if pool is not None:
                await pool.open(wait=False)
        try:
            await self.fetch("SELECT 1 AS ok", timeout_s=wait_s)
        except DBError as exc:
            self._ok, self._failed_at = False, time.monotonic()
            log.warning("database unreachable at startup; continuing without it", extra={"error": str(exc)})
        log.info("database opened", extra={"available": self.available, "copilot_ro": self.has_ro})
        self._keeper = asyncio.create_task(self._keep_connected(), name="db-keeper")

    async def close(self) -> None:
        if self._keeper is not None:
            self._keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keeper
            self._keeper = None
        for pool in (self._pool, self._ro_pool):
            if pool is not None:
                await pool.close()
        self._opened = False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """An autocommit app connection; use `async with conn.transaction():` for multi-statement writes."""
        if not self._ok and time.monotonic() - self._failed_at < FAIL_FAST_S:
            raise DBError("database unreachable (reconnecting in the background)")
        try:
            async with self._pool.connection(timeout=CHECKOUT_TIMEOUT_S) as conn:
                yield conn
            self._ok = True
        except (psycopg.Error, PoolTimeout, PoolClosed, OSError) as exc:
            raise self._wrap(exc) from exc

    async def fetch(self, sql: Any, params: Params = None, *,
                    timeout_s: float | None = DEFAULT_QUERY_TIMEOUT_S) -> list[dict[str, Any]]:
        return await self._timed(self._fetch(sql, params), timeout_s)

    async def fetchrow(self, sql: Any, params: Params = None, *,
                       timeout_s: float | None = DEFAULT_QUERY_TIMEOUT_S) -> dict[str, Any] | None:
        rows = await self.fetch(sql, params, timeout_s=timeout_s)
        return rows[0] if rows else None

    async def execute(self, sql: Any, params: Params = None, *,
                      timeout_s: float | None = DEFAULT_QUERY_TIMEOUT_S) -> int:
        """Run one statement; returns the affected row count."""
        return await self._timed(self._execute(sql, params), timeout_s)

    async def ro_fetch(self, sql: str, params: Params = None, timeout_s: float = 3.0) -> list[dict[str, Any]]:
        """Run a query as copilot_ro in a READ ONLY transaction with a statement timeout."""
        if self._ro_pool is None:
            raise DBError("COPILOT_DSN is not configured (copilot_ro pool unavailable)")
        return await self._timed(self._ro(sql, params, timeout_s), timeout_s + 1.0)

    # ---- internals --------------------------------------------------------------------------------

    async def _keep_connected(self) -> None:
        """While a pool is down, probe it every few seconds. Each probe asks the pool for a connection, which
        starts a fresh (short, capped) reconnect series even after psycopg_pool gave up on the previous one."""
        while True:
            await asyncio.sleep(self._keeper_interval)
            if not self._ok:
                try:
                    async with self._pool.connection(timeout=CHECKOUT_TIMEOUT_S) as conn:
                        await conn.execute("SELECT 1")
                except (psycopg.Error, PoolTimeout, OSError) as exc:
                    self._failed_at = time.monotonic()
                    log.debug("database still unreachable", extra={"error": _first_line(exc)})
                except PoolClosed:
                    return
                else:
                    self._ok = True
                    log.info("database reachable again")
            if self._ro_pool is not None and not self._ro_ok:
                try:
                    async with self._ro_pool.connection(timeout=CHECKOUT_TIMEOUT_S) as conn:
                        await conn.execute("SELECT 1")
                        await conn.rollback()
                except (psycopg.Error, PoolTimeout, OSError):
                    pass
                except PoolClosed:
                    return
                else:
                    self._ro_ok = True

    async def _fetch(self, sql: Any, params: Params) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            try:
                cur = await conn.execute(sql, params)
                return await cur.fetchall() if cur.description else []
            except (psycopg.Error, OSError) as exc:
                raise self._wrap(exc) from exc

    async def _execute(self, sql: Any, params: Params) -> int:
        async with self.connection() as conn:
            try:
                cur = await conn.execute(sql, params)
                return cur.rowcount
            except (psycopg.Error, OSError) as exc:
                raise self._wrap(exc) from exc

    async def _ro(self, sql: str, params: Params, timeout_s: float) -> list[dict[str, Any]]:
        assert self._ro_pool is not None
        try:
            async with self._ro_pool.connection(timeout=CHECKOUT_TIMEOUT_S) as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                    cur = await conn.execute(sql.encode(), params)
                    rows = await cur.fetchall() if cur.description else []
            self._ro_ok = True
            return rows
        except (psycopg.Error, PoolTimeout, PoolClosed, OSError) as exc:
            raise self._wrap(exc, ro=True) from exc

    async def _timed(self, coro: Any, timeout_s: float | None) -> Any:
        try:
            if timeout_s is None:
                return await coro
            return await asyncio.wait_for(coro, timeout_s)
        except TimeoutError as exc:
            raise DBError(f"query timed out after {timeout_s}s") from exc

    def _wrap(self, exc: BaseException, *, ro: bool = False) -> DBError:
        """DBError with a short message. A connectivity failure marks the pool it came from as down: the app
        pool (fail fast + keeper) or, with ro=True, only the copilot_ro pool (never the app's `available`)."""
        if isinstance(exc, DBError):
            return exc
        if isinstance(exc, (psycopg.OperationalError, PoolTimeout, PoolClosed, OSError)) and not isinstance(
                exc, psycopg.errors.QueryCanceled):
            if ro:
                self._ro_ok = False
            else:
                self._ok = False
                self._failed_at = time.monotonic()
        return DBError(f"{type(exc).__name__}: {_first_line(exc)}")


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
