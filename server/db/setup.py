"""Database setup in the exact §5.11 order, against any DSN (local Docker or Tiger Cloud).

usage: python -m server.db.setup --dsn "$DSN" [--fresh] [--wait] [--seed 42] [--days 75] [--patients 15]

 1. schema.sql              (skipped if pose_frames exists)
 2. aggregates.sql          (autocommit, statement by statement; skipped if daily_progress exists, then
                             materialized_only = false is enforced)
 3. roles.sql               copilot_ro (password from COPILOT_RO_PASSWORD or COPILOT_DSN)
 4. seed                    Dr. Okafor, Maya, prescriptions (idempotent)
 5. simulate_history        skipped if simulated sessions already exist
 6. CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)  only right after a new backfill
 7. Data Guard check 3      must report 0 mismatches over the whole history
 8. compress finished chunks with fingerprints → ratio
 9. add_columnstore_policy  (if_not_exists)
10. list jobs               exactly one refresh policy + one columnstore policy (besides TimescaleDB internals)

--fresh drops and recreates the target database first; it only works for localhost DSNs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from server.agents.data_guard import DataGuard
from server.db import migrate
from server.db import seed as seed_mod
from server.db import writes
from server.db.apply_sql import apply_file, env_params
from server.db.compress_history import compress_history
from server.db.pool import Database
from server.sim import simulate_history

DB_DIR = Path(__file__).resolve().parent
MIN_TIMESCALE = (2, 18)
# User jobs we expect (TimescaleDB's own maintenance jobs have job_id < 1000).
EXPECTED_JOBS = {("policy_refresh_continuous_aggregate", "daily_progress"), ("policy_compression", "pose_frames")}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SetupError(RuntimeError):
    pass


def _where(dsn: str) -> str:
    parts = urlsplit(dsn)
    return f"{parts.hostname}:{parts.port or 5432}/{parts.path.lstrip('/')}"


def _step(n: int, title: str, fn: Callable[[], str]) -> None:
    started = time.perf_counter()
    print(f"[{n:>2}/10] {title} ...", flush=True)
    note = fn()
    print(f"        {note} ({time.perf_counter() - started:.1f}s)", flush=True)


def wait_for(dsn: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    admin = _admin_dsn(dsn)
    while True:
        try:
            with psycopg.connect(admin, connect_timeout=3):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise SetupError(f"database at {_where(dsn)} did not accept connections in {timeout_s:.0f}s")
            time.sleep(1)


def _admin_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def recreate_database(dsn: str) -> None:
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/")
    if parts.hostname not in LOCAL_HOSTS:
        raise SetupError("--fresh only works on a local database (refusing to drop a remote one)")
    if not name or name == "postgres":
        raise SetupError("--fresh needs a dedicated database name in the DSN, e.g. .../mira")
    with psycopg.connect(_admin_dsn(dsn), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _exists(conn: psycopg.Connection[Any], relation: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (relation,)).fetchone()[0]


def setup(dsn: str, *, fresh: bool = False, seed: int = 42, days: int = 75, patients: int = 15,
          ro_password: str | None = None, expected_path: Path | None = None) -> dict[str, Any]:
    print(f"Mira database setup → {_where(dsn)}")
    if fresh:
        recreate_database(dsn)
        print("        recreated the database (--fresh)")
    params = {"copilot_ro_password": ro_password} if ro_password else env_params()
    if "copilot_ro_password" not in params:
        raise SetupError("set COPILOT_RO_PASSWORD (or put the copilot_ro password in COPILOT_DSN)")
    report: dict[str, Any] = {}

    with psycopg.connect(dsn, autocommit=True) as conn:
        def schema() -> str:
            if _exists(conn, "pose_frames"):
                migrated = migrate.apply_migrations(dsn)
                return f"already applied, skipped; {len(migrated)} migration(s) checked"
            count = apply_file(DB_DIR / "schema.sql", dsn)
            migrate.apply_migrations(dsn)
            version = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchone()[0]
            if tuple(int(p) for p in version.split(".")[:2]) < MIN_TIMESCALE:
                raise SetupError(f"TimescaleDB {version} < 2.18: use the legacy compression names (§5.15 5b)")
            return f"{count} statements, timescaledb {version}"

        def aggregates() -> str:
            if _exists(conn, "daily_progress"):
                conn.execute("ALTER MATERIALIZED VIEW daily_progress SET (timescaledb.materialized_only = false)")
                return "already applied; materialized_only = false enforced"
            return f"{apply_file(DB_DIR / 'aggregates.sql', dsn)} statements"

        def roles() -> str:
            return f"{apply_file(DB_DIR / 'roles.sql', dsn, params)} statements (copilot_ro)"

        def seed_step() -> str:
            seed_mod.seed(conn, history_days=days)
            return "therapist okafor, patient maya, prescriptions"

        _step(1, "schema.sql", schema)
        _step(2, "aggregates.sql (autocommit)", aggregates)
        _step(3, "roles.sql", roles)
        _step(4, "seed", seed_step)

    loaded: dict[str, Any] = {}

    def sim() -> str:
        loaded.update(simulate_history.simulate(dsn, seed, days, patients, expected_path))
        if loaded.get("skipped"):
            return "simulated history already loaded, skipped"
        loaded.pop("expected", None)
        report["sim"] = dict(loaded)
        return (f"{loaded['frames']:,} frames ({loaded['frames_with_landmarks']:,} with landmarks), "
                f"{loaded['sessions']} sessions, {loaded['reps']:,} reps in {loaded['wall_s']}s")

    def refresh() -> str:
        if loaded.get("skipped"):
            return "skipped: no new backfill (the policy keeps the last 30 days fresh)"
        with psycopg.connect(dsn, autocommit=True) as conn:  # cannot run inside a transaction block
            conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
        return "full refresh done (every raw frame still exists)"

    _step(5, f"simulate_history --seed {seed} --days {days} --patients {patients}", sim)
    _step(6, "refresh_continuous_aggregate (full)", refresh)

    def check3() -> str:
        result = asyncio.run(_check3(dsn, ro_dsn(dsn, params["copilot_ro_password"]), days + 7))
        report["check3"] = result
        d = result["details"]
        if result["status"] != "ok":
            raise SetupError(f"Data Guard check 3 failed: {d}")
        return f"{d['days_compared']} days / {d['rows_compared']} rows compared, 0 mismatches"

    def compress() -> str:
        out = asyncio.run(compress_history(dsn, verbose=False))
        report["compression"] = out
        if out["failed"]:
            raise SetupError(f"{out['failed']} chunks failed the fingerprint check")
        return (f"{out['chunks']} chunks compressed, fingerprints match; "
                f"{(out['before_bytes'] or 0) / 1e6:.1f} MB → {(out['after_bytes'] or 0) / 1e6:.1f} MB, "
                f"ratio {out['ratio']}")

    def policy() -> str:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("CALL add_columnstore_policy('pose_frames', after => INTERVAL '1 day', "
                         "if_not_exists => true)")
        return "columnstore policy after 1 day"

    def jobs() -> str:
        with psycopg.connect(dsn, autocommit=True) as conn:
            rows = conn.execute("SELECT job_id, proc_name, hypertable_name, schedule_interval "
                                "FROM timescaledb_information.jobs ORDER BY job_id").fetchall()
        user_jobs = [r for r in rows if r[0] >= 1000]
        found = {(r[1], r[2]) for r in user_jobs}
        report["jobs"] = [f"{r[0]} {r[1]} {r[2]} every {r[3]}" for r in rows]
        if found != EXPECTED_JOBS or len(user_jobs) != len(EXPECTED_JOBS):
            raise SetupError(f"unexpected background jobs: {report['jobs']} (remove with SELECT delete_job(id))")
        return "; ".join(report["jobs"])

    _step(7, "Data Guard check 3 (raw vs summary)", check3)
    _step(8, "compress history with fingerprints", compress)
    _step(9, "add_columnstore_policy", policy)
    _step(10, "background jobs", jobs)
    print("setup complete")
    return report


def ro_dsn(dsn: str, password: str) -> str:
    """The same database as copilot_ro (the Data Guard reads only through that role)."""
    parts = urlsplit(dsn)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, f"copilot_ro:{quote(password, safe='')}@{host}", parts.path, parts.query,
                       parts.fragment))


async def _check3(dsn: str, ro: str, days: int) -> dict[str, Any]:
    db = Database(dsn, ro, min_size=1, max_size=2)
    await db.open()
    try:
        guard = DataGuard(db)
        result = await guard.check_raw_vs_summary(days)
        await writes.insert_data_check(db, result["check_name"], result["status"], result["details"])
        return result
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mira database setup (§5.11 steps 1-10)")
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--fresh", action="store_true", help="drop and recreate the (local) database first")
    parser.add_argument("--wait", action="store_true", help="wait for the server to accept connections")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=75)
    parser.add_argument("--patients", type=int, default=len(simulate_history.PATIENTS))
    args = parser.parse_args(argv)
    full = args.days == 75 and args.patients == len(simulate_history.PATIENTS)
    try:
        if args.wait:
            wait_for(args.dsn)
        setup(args.dsn, fresh=args.fresh, seed=args.seed, days=args.days, patients=args.patients,
              expected_path=simulate_history.DEFAULT_EXPECTED if full else None)
    except SetupError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
