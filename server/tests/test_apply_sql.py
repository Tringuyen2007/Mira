from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from server.db.apply_sql import apply_file, statements
from server.tests.dbutil import RO_PASSWORD, fresh_database

DB_DIR = Path(__file__).resolve().parents[1] / "db"


def test_aggregates_sql_statement_count() -> None:
    stmts = statements((DB_DIR / "aggregates.sql").read_text())
    # daily_progress, refresh policy, progress_v, archive table, archive primary key
    assert len(stmts) == 5
    assert stmts[0].startswith("CREATE MATERIALIZED VIEW daily_progress")
    assert "timescaledb.materialized_only = false" in stmts[0]
    assert stmts[2].startswith("CREATE VIEW progress_v")


def test_semicolons_in_quotes_and_comments_do_not_split() -> None:
    text = """
    -- a comment; with a semicolon
    SELECT 'a;b' AS x;  /* block; comment */
    SELECT "weird;name" FROM t;
    SELECT 'it''s; fine';
    DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;
    DO $body$ BEGIN RAISE NOTICE 'x;y'; END $body$;
    -- trailing comment only
    """
    stmts = statements(text)
    assert len(stmts) == 5
    assert stmts[0].endswith("SELECT 'a;b' AS x")
    assert stmts[2] == "SELECT 'it''s; fine'"
    assert stmts[3] == "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$"
    assert stmts[4] == "DO $body$ BEGIN RAISE NOTICE 'x;y'; END $body$"


def test_comment_only_file_has_no_statements() -> None:
    assert statements("-- nothing here;\n\n-- still nothing\n") == []


def test_placeholder_substitution_is_quoted() -> None:
    from server.db.apply_sql import substitute

    out = substitute("ALTER ROLE r PASSWORD {{pw}}", {"pw": "o'brien"})
    assert out == "ALTER ROLE r PASSWORD 'o''brien'"
    with pytest.raises(KeyError):
        substitute("SELECT {{missing}}", {})


def test_schema_aggregates_roles_apply_end_to_end() -> None:
    with fresh_database() as dsn:
        for name in ("schema.sql", "aggregates.sql"):
            apply_file(DB_DIR / name, dsn)
        # copilot_ro is cluster-wide: keep the shared test password so concurrent test databases keep working
        apply_file(DB_DIR / "roles.sql", dsn, {"copilot_ro_password": RO_PASSWORD})
        apply_file(DB_DIR / "roles.sql", dsn, {"copilot_ro_password": RO_PASSWORD})  # re-runnable
        with psycopg.connect(dsn) as conn:
            rows = conn.execute("SELECT view_name, materialized_only "
                                "FROM timescaledb_information.continuous_aggregates").fetchall()
            assert rows == [("daily_progress", False)]
            hypertables = {r[0] for r in conn.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables").fetchall()}
            assert {"pose_frames", "form_events", "alerts", "heart_rate", "wrist_motion"} <= hypertables
            assert conn.execute("SELECT to_regclass('notifications')").fetchone()[0] is not None
            assert conn.execute("SELECT to_regclass('daily_progress_archive')").fetchone()[0] is not None
            # the Data Guard reads the materialization hypertable as copilot_ro (granted by roles.sql's DO block)
            assert conn.execute(
                "SELECT has_table_privilege('copilot_ro', format('%I.%I', materialization_hypertable_schema, "
                "materialization_hypertable_name), 'SELECT') FROM timescaledb_information.continuous_aggregates"
            ).fetchone()[0] is True
