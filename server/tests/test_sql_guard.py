"""SQL guard (TRI §9.3, §11): the first wall in front of the copilot_ro pool."""

from __future__ import annotations

import pytest
import sqlglot

from server.agents.sql_guard import ALLOWED_TABLES, MAX_ROWS, Rejected, check_sql


def _limit(sql: str) -> int:
    tree = sqlglot.parse_one(sql, read="postgres")
    return int(tree.args["limit"].expression.name)


@pytest.mark.parametrize("sql", [
    "SELECT patient_id, day, p95_knee FROM progress_v WHERE patient_id = 'maya' ORDER BY day",
    "select count(*) from sessions",
    "WITH w AS (SELECT patient_id, avg(affected_load_pct) a FROM progress_v GROUP BY 1) SELECT * FROM w",
    "SELECT id FROM patients UNION SELECT patient_id FROM sessions",
    "SELECT s.id, r.rep FROM sessions s JOIN reps r ON r.session_id = s.id",
    "SELECT * FROM public.progress_v",
    "SELECT regr_slope(p95_knee, extract(epoch FROM day)) FROM progress_v GROUP BY patient_id",
    "SELECT d FROM generate_series(1, 3) AS d",
    "SELECT rule_id, count(*) FILTER (WHERE severity = 'warning') FROM form_events GROUP BY 1",
])
def test_accepts_read_queries(sql: str) -> None:
    out = check_sql(sql)
    assert "LIMIT" in out.upper()


def test_adds_limit_to_select() -> None:
    assert _limit(check_sql("SELECT * FROM progress_v")) == MAX_ROWS


def test_keeps_smaller_limit_and_caps_larger_one() -> None:
    assert _limit(check_sql("SELECT * FROM progress_v LIMIT 5")) == 5
    assert _limit(check_sql("SELECT * FROM progress_v LIMIT 5000")) == MAX_ROWS
    assert _limit(check_sql("SELECT * FROM progress_v LIMIT ALL")) == MAX_ROWS


def test_union_is_wrapped_with_a_limit() -> None:
    out = check_sql("SELECT id FROM patients UNION SELECT patient_id FROM sessions")
    assert out.upper().startswith("SELECT * FROM (") and out.endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.parametrize("sql", [
    "INSERT INTO sessions (id) VALUES ('x')",
    "UPDATE prescriptions SET sets = 9",
    "DELETE FROM sessions",
    "DROP TABLE sessions",
    "CREATE TABLE x (a int)",
    "TRUNCATE sessions",
    "ALTER TABLE sessions ADD COLUMN x int",
    "GRANT SELECT ON alerts TO copilot_ro",
    "COPY sessions TO STDOUT",
    "EXPLAIN SELECT 1",
    "SET statement_timeout = 0",
    "VACUUM",
])
def test_rejects_writes_ddl_and_commands(sql: str) -> None:
    with pytest.raises(Rejected):
        check_sql(sql)


def test_rejects_statement_chains() -> None:
    with pytest.raises(Rejected, match="exactly one statement"):
        check_sql("SELECT 1 FROM sessions; DELETE FROM sessions")
    with pytest.raises(Rejected, match="exactly one statement"):
        check_sql("SELECT 1; SELECT 2")


def test_rejects_data_modifying_cte() -> None:
    with pytest.raises(Rejected, match="write/DDL"):
        check_sql("WITH a AS (DELETE FROM sessions RETURNING *) SELECT * FROM a")


@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10)",
    "SELECT PG_SLEEP(10) FROM sessions",
    "SELECT (SELECT pg_sleep(1))",
    "SELECT pg_catalog.pg_sleep(1)",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('server_version')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT dblink('host=x', 'select 1')",
    "SELECT query_to_xml('delete from sessions', true, true, '')",
    "SELECT pg_terminate_backend(1)",
])
def test_rejects_dangerous_functions(sql: str) -> None:
    with pytest.raises(Rejected, match="not allowed"):
        check_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM alerts",
    "SELECT * FROM agent_actions",
    "SELECT * FROM ai_calls",
    "SELECT * FROM pose_frames",
    "SELECT * FROM plan_proposals",
    "SELECT * FROM progress_v p JOIN therapists t ON true",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other.sessions",
])
def test_rejects_tables_outside_the_allow_list(sql: str) -> None:
    with pytest.raises(Rejected) as info:
        check_sql(sql)
    assert "progress_v" in str(info.value)  # the error lists what IS allowed, so the model can fix its query


def test_cte_names_are_not_mistaken_for_tables() -> None:
    check_sql("WITH alerts AS (SELECT * FROM sessions) SELECT * FROM alerts")


def test_rejects_select_into_and_row_locks() -> None:
    with pytest.raises(Rejected, match="INTO"):
        check_sql("SELECT * INTO copy_of_sessions FROM sessions")
    for lock in ("FOR UPDATE", "FOR SHARE", "FOR NO KEY UPDATE"):
        with pytest.raises(Rejected, match="lock"):
            check_sql(f"SELECT * FROM sessions {lock}")


def test_rejects_garbage_and_empty() -> None:
    for sql in ("", "   ", "SELEC * FRM x", "SELECT * FROM progress_v WHERE"):
        with pytest.raises(Rejected):
            check_sql(sql)


def test_allow_list_matches_copilot_ro_grants() -> None:
    assert ALLOWED_TABLES == {"progress_v", "sessions", "reps", "form_events", "memory_events", "patients",
                              "prescriptions", "hr_recovery", "rotation_sets", "tremor_daily"}


# ---- review regressions (F4 review): bypasses found adversarially --------------------------------------------

@pytest.mark.parametrize("sql", [
    # a CTE defined in a subquery doesn't hide the real table outside it
    "SELECT * FROM alerts, (WITH alerts AS (SELECT 1) SELECT * FROM alerts) s",
    # a non-recursive CTE sees only the CTEs before it: here `alerts` is the real table
    "WITH x AS (SELECT * FROM alerts), alerts AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM sessions WHERE EXISTS (WITH a AS (SELECT 1) SELECT 1 FROM agent_actions)",
    "SELECT * FROM mira.public.sessions",           # catalog-qualified
    'SELECT * FROM "Sessions"',                      # quoted names are exact in Postgres
])
def test_rejects_table_allow_list_bypasses(sql: str) -> None:
    with pytest.raises(Rejected, match="is not available"):
        check_sql(sql)


@pytest.mark.parametrize("sql", [
    "WITH alerts AS (SELECT 1 AS a) SELECT a FROM (SELECT a FROM alerts) s",  # the CTE is visible in the body
    "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT x FROM b UNION SELECT x FROM a",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT n FROM r",
    'SELECT * FROM "sessions"',
])
def test_cte_scoping_still_accepts_valid_queries(sql: str) -> None:
    check_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT ts_stat('SELECT to_tsvector(note) FROM sessions')",        # runs a query given as text
    "SELECT ts_rewrite('a'::tsquery, 'SELECT 1')",
    "SELECT drop_chunks('pose_frames', older_than => now())",          # TimescaleDB admin API
    "SELECT decompress_chunk('x')",
    "SELECT add_retention_policy('pose_frames', INTERVAL '1 day')",
    "SELECT alter_job(1000, scheduled => false)",
    "SELECT delete_job(1000)",
    "SELECT nextval('plan_proposals_id_seq')",
    "SELECT _timescaledb_functions.x()",
    "SELECT * FROM sessions s, LATERAL (SELECT setval('x', 1)) q",
])
def test_rejects_query_text_and_admin_functions(sql: str) -> None:
    with pytest.raises(Rejected, match="not allowed"):
        check_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT emergency_contact_phone FROM patients",
    "SELECT regexp_replace(emergency_contact_phone, '\\D', '', 'g') AS x FROM patients",  # beats a value scrub
    "SELECT name FROM patients WHERE emergency_contact_name IS NOT NULL",
    "SELECT * FROM patients",
    "SELECT p.* FROM patients p",
    "SELECT * FROM sessions s JOIN patients p ON p.id = s.patient_id",
    "SELECT p::text FROM patients p",
    "SELECT row_to_json(p) FROM patients p",
    "SELECT to_jsonb(patients) FROM patients",
])
def test_rejects_contact_details_and_whole_patient_rows(sql: str) -> None:
    with pytest.raises(Rejected, match="contact|patients"):
        check_sql(sql)


def test_named_patient_columns_are_fine() -> None:
    check_sql("SELECT s.*, p.name FROM sessions s JOIN patients p ON p.id = s.patient_id")
    check_sql("SELECT id, name, affected_side FROM patients")
    check_sql("SELECT count(*) FROM patients")


def test_comments_are_dropped_from_the_query_that_runs() -> None:
    out = check_sql("SELECT id FROM sessions /* ; DROP TABLE sessions */ -- */ , alerts /*")
    assert "/*" not in out and "--" not in out and "DROP" not in out and "alerts" not in out


def test_union_with_order_by_and_big_limit_is_capped() -> None:
    out = check_sql("SELECT id FROM sessions UNION SELECT id FROM sessions ORDER BY 1 LIMIT 5000")
    assert out.endswith(f"LIMIT {MAX_ROWS}")
    assert _limit(check_sql("SELECT * FROM sessions FETCH FIRST 1000 ROWS ONLY")) == MAX_ROWS
    assert _limit(check_sql("SELECT * FROM sessions LIMIT 1 + 1000")) == MAX_ROWS
