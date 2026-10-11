"""Run a .sql file one statement at a time on an autocommit connection (TRI.md §5.15 step 2).

Postgres runs a multi-statement string as one implicit transaction, so `CREATE MATERIALIZED VIEW ... WITH
(timescaledb.continuous)` fails if the whole file is sent in one execute(). This splits on ';' outside quotes,
comments and dollar-quoted bodies (roles.sql uses a DO block) and sends each statement separately.

`{{name}}` placeholders are replaced with quoted literals (used for the copilot_ro password, which is never
stored in git).

usage: python -m server.db.apply_sql server/db/aggregates.sql "$TIGER_DSN"
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


def statements(text: str) -> list[str]:
    """Split SQL text on ';' outside quotes, `--`/`/* */` comments and `$tag$` bodies; drop empty ones."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            buf.append(text[i:end])
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(text[i:end])
            i = end
        elif ch in ("'", '"'):
            end = text.find(ch, i + 1)
            end = n if end == -1 else end + 1
            buf.append(text[i:end])  # a doubled quote ('') simply closes and reopens
            i = end
        elif ch == "$" and (m := _DOLLAR_TAG.match(text, i)) and not (i and (text[i - 1].isalnum()
                                                                              or text[i - 1] == "_")):
            tag = m.group(0)
            end = text.find(tag, m.end())
            end = n if end == -1 else end + len(tag)
            buf.append(text[i:end])
            i = end
        elif ch == ";":
            out.append("".join(buf).strip())
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    out.append("".join(buf).strip())
    return [s for s in (_strip_leading_comments(stmt) for stmt in out) if s]


_LEADING_COMMENT = re.compile(r"\s*(--[^\n]*(\n|$)|/\*.*?\*/)", re.S)


def _strip_leading_comments(stmt: str) -> str:
    while m := _LEADING_COMMENT.match(stmt):
        stmt = stmt[m.end():]
    return stmt.strip()


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def substitute(text: str, params: Mapping[str, str]) -> str:
    """Replace `{{name}}` with a quoted SQL literal. Raises KeyError for a missing parameter."""
    return _PLACEHOLDER.sub(lambda m: _quote_literal(params[m.group(1)]), text)


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def copilot_ro_password() -> str | None:
    """COPILOT_RO_PASSWORD, else the password inside COPILOT_DSN."""
    explicit = os.environ.get("COPILOT_RO_PASSWORD")
    if explicit:
        return explicit
    dsn = os.environ.get("COPILOT_DSN")
    if dsn:
        try:
            return urlsplit(dsn).password
        except ValueError:
            return None
    return None


def env_params() -> dict[str, str]:
    pw = copilot_ro_password()
    return {"copilot_ro_password": pw} if pw else {}


def apply_file(path: str | Path, dsn: str, params: Mapping[str, str] | None = None) -> int:
    """Apply every statement of `path` on one autocommit connection. Returns the statement count."""
    text = Path(path).read_text()
    needed = placeholders(text)
    values = dict(params if params is not None else env_params())
    missing = needed - values.keys()
    if missing:
        raise SystemExit(f"{Path(path).name} needs {sorted(missing)}; set COPILOT_RO_PASSWORD "
                         "(or a password in COPILOT_DSN)")
    stmts = statements(text)
    with psycopg.connect(dsn, autocommit=True) as conn:  # autocommit is required
        for stmt in stmts:
            conn.execute(substitute(stmt, values).encode())  # one statement per round trip
    return len(stmts)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m server.db.apply_sql <file.sql> <dsn>", file=sys.stderr)
        return 2
    import server.config  # noqa: F401 - loads .env so COPILOT_RO_PASSWORD / COPILOT_DSN are visible

    count = apply_file(argv[1], argv[2])
    print(f"applied {count} statements from {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
