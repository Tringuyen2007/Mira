"""SQL guard for the Copilot's run_sql tool (TRI §9.3). sqlglot is pinned (30.22.0); see test_sql_guard.py.

The read-only `copilot_ro` role is the real guarantee; this is the first wall, and its errors are written for the
model so it can fix its query.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_ROWS = 200

# Exactly the relations copilot_ro may SELECT (roles.sql).
ALLOWED_TABLES = frozenset({"progress_v", "sessions", "reps", "form_events", "memory_events", "patients",
                            "prescriptions"})
ALLOWED_SCHEMAS = frozenset({"", "public"})

BLOCKED_FUNCS = frozenset({"pg_sleep", "set_config", "dblink", "pg_read_file", "pg_read_binary_file", "lo_import",
                           "lo_export", "pg_terminate_backend", "pg_cancel_backend", "current_setting"})
# Whole families: server admin/files/locks (pg_*), large objects, remote queries, and the *_to_xml helpers that
# execute a query given as a string.
BLOCKED_PREFIXES = ("pg_", "lo_", "dblink", "query_to_", "cursor_to_", "table_to_", "schema_to_", "database_to_")
BLOCKED_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
                 exp.Copy, exp.Command)


class Rejected(Exception):
    """The query (or tool call) is not allowed. The message is shown to the model."""


def _func_name(f: exp.Func) -> str:
    return (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()


def _check_functions(tree: exp.Expression) -> None:
    for f in tree.find_all(exp.Func):
        name = _func_name(f)
        if name in BLOCKED_FUNCS or name.startswith(BLOCKED_PREFIXES):
            raise Rejected(f"function {name} not allowed")
        if isinstance(f.parent, exp.Dot):
            raise Rejected(f"schema-qualified function {f.parent.sql(dialect='postgres')} not allowed")


def _check_tables(tree: exp.Expression) -> None:
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):  # table function, e.g. generate_series(); checked as a func
            continue
        name, schema = table.name.lower(), table.db.lower()
        if not schema and name in ctes:
            continue
        if schema not in ALLOWED_SCHEMAS or name not in ALLOWED_TABLES:
            shown = f"{schema}.{name}" if schema else name
            raise Rejected(f"table {shown} is not available; you can read only: {', '.join(sorted(ALLOWED_TABLES))}")


def _cap_limit(tree: exp.Select, max_rows: int) -> exp.Select:
    limit = tree.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and not value.is_string and value.name.isdigit() and int(value.name) <= max_rows:
        return tree
    return tree.limit(max_rows)


def check_sql(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return a safe single SELECT (with LIMIT ≤ max_rows) as Postgres SQL, or raise Rejected."""
    try:
        stmts = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise Rejected(f"could not parse the query: {str(exc).splitlines()[0][:200]}") from None
    if len(stmts) != 1:
        raise Rejected("exactly one statement allowed")
    tree = stmts[0]
    if not isinstance(tree, exp.Query):  # SELECT / UNION / WITH … SELECT
        raise Rejected("only SELECT queries are allowed")
    if any(isinstance(n, BLOCKED_NODES) for n in tree.walk()):
        raise Rejected("write/DDL not allowed (read-only access)")
    if any(isinstance(n, exp.Into) for n in tree.walk()):
        raise Rejected("SELECT … INTO not allowed (read-only access)")
    if any(isinstance(n, exp.Lock) for n in tree.walk()):
        raise Rejected("row locks (FOR UPDATE / FOR SHARE) not allowed")
    _check_functions(tree)
    _check_tables(tree)
    if isinstance(tree, exp.Select):
        tree = _cap_limit(tree, max_rows)
    else:
        tree = exp.select("*").from_(tree.subquery("q")).limit(max_rows)
    return tree.sql(dialect="postgres")
