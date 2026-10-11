"""Apply server/db/migrations/*.sql in name order. Every file is idempotent, so running this on every start is
safe; schema_migrations only records what ran. Used by setup (step 1), the tests, `make db-migrate`, the deploy
script and the server itself when DB_AUTO_MIGRATE=1.

usage: python -m server.db.migrate [--dsn DSN]      (default: TIGER_DSN from the environment / .env)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from server.db.apply_sql import apply_file

DB_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = DB_DIR / "migrations"


def migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def apply_migrations(dsn: str) -> list[str]:
    """Run every migration file (each statement autocommitted) and record its name. Returns the names in order."""
    applied: list[str] = []
    for path in migration_files():
        apply_file(path, dsn)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                         (path.name,))
        applied.append(path.name)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="apply Mira database migrations")
    parser.add_argument("--dsn", default=None, help="defaults to TIGER_DSN")
    args = parser.parse_args(argv)
    dsn = args.dsn
    if not dsn:
        from server.config import cfg  # loads .env; imported late so --dsn works without one

        dsn = cfg.TIGER_DSN
    if not dsn:
        print("no DSN: pass --dsn or set TIGER_DSN", file=sys.stderr)
        return 2
    names = apply_migrations(dsn)
    print(f"applied {len(names)} migration(s): {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
