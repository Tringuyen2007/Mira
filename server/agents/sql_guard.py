"""SQL guard for the Copilot's run_sql tool (TRI §9.3). sqlglot is pinned (30.22.0); see test_sql_guard.py.

The read-only `copilot_ro` role is the real guarantee; this is the first wall, and its errors are written for the
model so it can fix its query. The query that runs is regenerated from the parsed tree (comments dropped).
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_ROWS = 200

# Exactly the relations copilot_ro may SELECT (roles.sql). patients is readable only column by column: the
# emergency contact columns are not granted.
ALLOWED_TABLES = frozenset({"progress_v", "sessions", "reps", "form_events", "memory_events", "patients",
                            "prescriptions", "hr_recovery", "rotation_sets", "tremor_daily"})
ALLOWED_SCHEMAS = frozenset({"", "public"})
PATIENT_COLUMNS = ("id", "name", "age", "height_cm", "mass_kg", "affected_side", "condition", "therapist_id",
                   "hr_limit", "simulated", "created_at")
_PRIVATE_COLUMN_RE = re.compile(r"phone|contact", re.IGNORECASE)

BLOCKED_FUNCS = frozenset({"pg_sleep", "set_config", "dblink", "pg_read_file", "pg_read_binary_file", "lo_import",
                           "lo_export", "pg_terminate_backend", "pg_cancel_backend", "current_setting",
                           # run a query given as text
                           "ts_stat", "ts_rewrite",
                           # sequences / transaction ids (writes or state changes)
                           "nextval", "setval", "currval", "lastval"})
# Whole families: server admin/files/locks (pg_*), large objects, remote queries, the *_to_xml helpers that execute
# a query given as a string, and TimescaleDB's admin API (policies, jobs, chunks, hypertables).
BLOCKED_PREFIXES = ("pg_", "lo_", "dblink", "query_to_", "cursor_to_", "table_to_", "schema_to_", "database_to_",
                    "_timescaledb", "timescaledb_", "txid_", "add_", "remove_", "drop_", "alter_", "delete_", "run_",
                    "create_", "attach_", "detach_", "move_", "reorder_", "compress", "decompress", "recompress",
                    "refresh_", "set_", "enable_", "disable_", "convert_to_")
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


def _visible_ctes(table: exp.Table) -> set[str]:
    """CTE names a table reference can resolve to, with Postgres scoping: the query body sees all of its WITH;
    a CTE body sees only the CTEs before it (all of them, itself included, under WITH RECURSIVE)."""
    names: set[str] = set()
    child: exp.Expression = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With) and isinstance(child, exp.CTE):
            ctes = list(node.expressions)
            visible = ctes if node.args.get("recursive") else ctes[:ctes.index(child)]
            names |= {c.alias_or_name.lower() for c in visible}
        elif isinstance(node, exp.Query) and child is not node.args.get("with_"):
            with_ = node.args.get("with_")
            if isinstance(with_, exp.With):
                names |= {c.alias_or_name.lower() for c in with_.expressions}
        child, node = node, node.parent
    return names


def _ident(node: exp.Expression | None) -> str:
    """Postgres folds unquoted names to lower case; quoted ones are exact."""
    if not isinstance(node, exp.Identifier):
        return ""
    return node.name if node.quoted else node.name.lower()


def _check_tables(tree: exp.Expression) -> None:
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):  # table function, e.g. generate_series(); checked as a func
            continue
        name, schema = _ident(table.this), _ident(table.args.get("db"))
        if not schema and not table.catalog and name in _visible_ctes(table):
            continue
        if table.catalog or schema not in ALLOWED_SCHEMAS or name not in ALLOWED_TABLES:
            shown = ".".join(p for p in (table.catalog, schema, name) if p)
            raise Rejected(f"table {shown} is not available; you can read only: {', '.join(sorted(ALLOWED_TABLES))}")


def _check_private_columns(tree: exp.Expression) -> None:
    """Contact details never reach the model: no contact columns, no `*` or whole-row use of patients."""
    for ident in tree.find_all(exp.Identifier):
        if _PRIVATE_COLUMN_RE.search(ident.name):
            raise Rejected("patient contact details are not available to the Copilot")
    patient_aliases = {t.alias_or_name.lower() for t in tree.find_all(exp.Table)
                       if isinstance(t.this, exp.Identifier) and t.name.lower() == "patients"}
    if not patient_aliases:
        return
    hint = f"name the patients columns you need ({', '.join(PATIENT_COLUMNS)})"
    for select in tree.find_all(exp.Select):
        sources = {t.alias_or_name.lower() for t in select.find_all(exp.Table)
                   if t.parent_select is select and t.name.lower() == "patients"}
        for proj in select.expressions:
            star = isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star))
            if star and sources and (isinstance(proj, exp.Star) or proj.table.lower() in sources):
                raise Rejected(f"SELECT * on patients is not allowed; {hint}")
    for col in tree.find_all(exp.Column):
        if not col.table and col.name.lower() in patient_aliases and col.name.lower() not in PATIENT_COLUMNS:
            raise Rejected(f"whole-row use of patients is not allowed; {hint}")


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
    _check_private_columns(tree)
    if isinstance(tree, exp.Select):
        tree = _cap_limit(tree, max_rows)
    else:
        tree = exp.select("*").from_(tree.subquery("q")).limit(max_rows)
    return tree.sql(dialect="postgres", comments=False)
