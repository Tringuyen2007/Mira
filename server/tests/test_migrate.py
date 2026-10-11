"""Migrations apply to a fresh schema, are idempotent, and setup/dbutil run them."""

from __future__ import annotations

import psycopg

from server.db import migrate
from server.db.apply_sql import apply_file
from server.tests.dbutil import fresh_database


def _columns(dsn: str, table: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                            (table,)).fetchall()
    return {r[0] for r in rows}


def test_migrations_apply_and_are_idempotent() -> None:
    with fresh_database() as dsn:
        apply_file(migrate.DB_DIR / "schema.sql", dsn)
        first = migrate.apply_migrations(dsn)
        assert first and first[0] == "001_patient_identity.sql"
        second = migrate.apply_migrations(dsn)  # every statement is IF NOT EXISTS
        assert second == first
        assert {"code", "patient_id", "expires_at", "used_at", "reusable"} <= _columns(dsn, "pairing_codes")
        assert {"token_hash", "patient_id", "last_seen", "revoked_at", "via_reusable"} <= _columns(dsn,
                                                                                               "patient_devices")
        assert {"emergency_contact_relationship", "alert_therapist", "next_visit_at", "share_by_default",
                "device_prefs"} <= _columns(dsn, "patients")
        assert "clinic" in _columns(dsn, "therapists")
        assert "005_watch.sql" in first
        assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(dsn, "patients")
        assert {"patient_id", "accel"} <= _columns(dsn, "wrist_motion")
        assert "watch_clock_offset_ms" in _columns(dsn, "sessions")
        with psycopg.connect(dsn) as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()]
        assert names == first


def test_schema_sql_already_contains_the_migration_ddl() -> None:
    """A fresh schema.sql database needs no migration to have the new tables (the file is kept in sync)."""
    with fresh_database() as dsn:
        apply_file(migrate.DB_DIR / "schema.sql", dsn)
        assert "reusable" in _columns(dsn, "pairing_codes")
        assert "device_prefs" in _columns(dsn, "patients")
        assert "via_reusable" in _columns(dsn, "patient_devices")
        assert "tremor_monitoring" in _columns(dsn, "patients")
        assert "set_index" in _columns(dsn, "hr_recovery")
