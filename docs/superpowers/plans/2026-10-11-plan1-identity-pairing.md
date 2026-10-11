# Plan 1 — Patient identity & pairing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the iPhone a real patient identity: a therapist code pairs the phone, mints a patient-scoped token, syncs the onboarding profile, and lets every later plan scope REST and WebSocket traffic to that patient.

**Architecture:** A third token scope (`patient`) joins `full` and `device` in `server/security.py` via a `Principal` value. Patient tokens are random, stored hashed in a new `patient_devices` table, minted by `POST /pair` from a `pairing_codes` row. New `/me*` routes return everything Home/Care need in one call. Existing routes keep their behaviour; three routers (`sessions`, `patients`, `prescriptions`) additionally accept patient tokens with ownership checks. WebSockets accept patient tokens and filter to the patient's own session. iOS onboarding calls `/pair` and stores the token in the existing Keychain store.

**Tech Stack:** FastAPI 0.143, psycopg 3, TimescaleDB (local Docker on :55432 for tests), pytest (asyncio auto), SwiftUI / Swift 5.9 package `MiraNetworking` tested with `swift test`, Xcode 26.6.

Spec: `docs/superpowers/specs/2026-10-11-wire-ios-backend-design.md` §3, §4, §8, §9.

## Global Constraints

- Branch `wire-tri`. Commit after every task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never remove or change the behaviour of an existing route, event, token or test expectation; only add. The admin token (`MIRA_API_TOKEN`) and device token keep working exactly as today.
- All INSERT/UPDATE/DELETE SQL lives in `server/db/writes.py` (or `db/seed.py`, `db/migrations/*.sql`); all SELECTs in `server/db/queries.py`. Writes never raise on DB failure (use `_safe`); queries raise `DBError`.
- Migrations are idempotent (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
- Secrets never logged: patient tokens are logged only as their last 4 chars; the SHA-256 hash is what is stored.
- Python: `from __future__ import annotations`, type hints, `log = get_logger("<name>")`, 120-col lines, docstrings in the repo's terse style. Swift: match the existing files' style (`WireJSON` snake_case coding, `Codable, Sendable` structs).
- Tests: DB tests use `server/tests/dbutil.py` (`fresh_database`, `prepare`) and skip when Docker Postgres is down. Run the whole suite only at the end of the plan (`make test`, `swift test` in `device/ios`, `xcodebuild`), per the project convention; run the single new test file while developing a task.
- Run Python from the venv: `.venv/bin/python -m pytest server/tests/<file> -q`.

---

## File structure

| File | Responsibility |
|---|---|
| `server/db/migrations/001_patient_identity.sql` (new) | pairing_codes, patient_devices, new patient/therapist columns, schema_migrations |
| `server/db/migrate.py` (new) | apply every `migrations/*.sql` in order, idempotently; CLI + `apply_migrations(dsn)` |
| `server/db/schema.sql` | same DDL appended for fresh databases |
| `server/db/setup.py`, `server/tests/dbutil.py`, `Makefile`, `tools/deploy_vultr.sh`, `server/main.py` | run migrations (setup step 1, tests, `make db-migrate`, deploy, `DB_AUTO_MIGRATE`) |
| `server/exercises.py` (new) | exercise catalog: id → display name, metric label, unit |
| `server/engine_bridge.py` | `LiveState.exercise_name` from the catalog (same strings) |
| `server/config.py` | `DB_AUTO_MIGRATE`, `PAIRING_DEMO_CODE_REUSABLE`, `PAIRING_DEMO_CODE` |
| `server/security.py` | `Principal`, `resolve_token`, `require_principal`, `require_token` (unchanged semantics), `ws_scope` → `Principal` |
| `server/db/queries.py` | `pairing_code`, `patient_device`, `patient_me_row`, `get_patient` (+clinic, phone) |
| `server/db/writes.py` | `insert_patient_device`, `touch_patient_device`, `revoke_patient_devices`, `mark_pairing_code_used`, `upsert_pairing_code`, `update_patient_profile`, `purge_patient_data` |
| `server/db/seed.py` | demo code `K7P4M2` → Maya, clinic, next visit |
| `server/api/pair.py` (new) | `POST /pair`, `POST /patients/{id}/pairing-code`, `DELETE /patients/{id}/devices` |
| `server/api/me.py` (new) | `build_me`, `GET/PUT/DELETE /me`, `GET /me/export` |
| `server/api/sessions.py`, `patients.py`, `prescriptions.py` | accept patient principals with ownership checks |
| `server/main.py` | router registration, patient WebSocket handling |
| `server/samples/rest_post_pair.json`, `rest_get_me.json` (+ iOS `Tests/Fixtures/` copies) | contract samples |
| `server/tests/test_migrate.py`, `test_exercises.py`, `test_security_principal.py`, `test_pairing.py`, `test_me.py`, `test_patient_scope.py`, `test_ws_patient.py` (new) | tests |
| `device/ios/Mira/Net/Models.swift`, `APIClient.swift` | pairing/me models and calls |
| `device/ios/Mira/App/AppState.swift` | `patientID`, `me`, `pair()`, `refreshMe()` |
| `device/ios/Mira/Features/Onboarding/*.swift`, `Settings/SettingsView.swift` | pairing UI, LCD status, demo-server preset |
| `device/ios/Tests/PairingTests.swift` (new), `ContractFixtureTests.swift` | Swift tests |
| `README.md`, `device/ios/NETWORKING.md`, `device/ios/ONBOARDING.md`, `.env.example` | docs |

---

### Task 1: Migration runner and `001_patient_identity.sql`

**Files:**
- Create: `server/db/migrations/001_patient_identity.sql`
- Create: `server/db/migrate.py`
- Modify: `server/db/schema.sql` (append), `server/db/setup.py:111-118`, `server/tests/dbutil.py:60-68`, `Makefile`, `tools/deploy_vultr.sh`, `server/config.py`, `server/main.py:103-107`, `.env.example`
- Test: `server/tests/test_migrate.py`

**Interfaces:**
- Produces: `server.db.migrate.apply_migrations(dsn: str) -> list[str]` (names applied, in order), `server.db.migrate.MIGRATIONS_DIR`, `cfg.DB_AUTO_MIGRATE: bool = True`.
- Tables: `pairing_codes(code, patient_id, therapist_id, created_at, expires_at, used_at, reusable)`, `patient_devices(token_hash, patient_id, device_name, created_at, last_seen, revoked_at)`, `schema_migrations(name, applied_at)`; new columns `patients.emergency_contact_relationship text`, `patients.alert_therapist boolean default true`, `patients.next_visit_at timestamptz`, `patients.share_by_default boolean default false`, `patients.device_prefs jsonb default '{}'`, `therapists.clinic text`.

- [ ] **Step 1: Write the failing test**

`server/tests/test_migrate.py`:
```python
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
        assert {"token_hash", "patient_id", "last_seen", "revoked_at"} <= _columns(dsn, "patient_devices")
        assert {"emergency_contact_relationship", "alert_therapist", "next_visit_at", "share_by_default",
                "device_prefs"} <= _columns(dsn, "patients")
        assert "clinic" in _columns(dsn, "therapists")
        with psycopg.connect(dsn) as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()]
        assert names == first


def test_schema_sql_already_contains_the_migration_ddl() -> None:
    """A fresh schema.sql database needs no migration to have the new tables (the file is kept in sync)."""
    with fresh_database() as dsn:
        apply_file(migrate.DB_DIR / "schema.sql", dsn)
        assert "reusable" in _columns(dsn, "pairing_codes")
        assert "device_prefs" in _columns(dsn, "patients")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_migrate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.db.migrate'`.

- [ ] **Step 3: Write the migration**

`server/db/migrations/001_patient_identity.sql`:
```sql
-- Plan 1: patient identity. Every statement is idempotent (applied by server/db/migrate.py on every start).
CREATE TABLE IF NOT EXISTS schema_migrations (
  name        text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pairing_codes (
  code          text PRIMARY KEY,                       -- 6 chars A-Z0-9 (no 0/O/1/I), upper case
  patient_id    text NOT NULL REFERENCES patients(id),
  therapist_id  text REFERENCES therapists(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz,                            -- NULL = never (demo code)
  used_at       timestamptz,
  reusable      boolean NOT NULL DEFAULT false          -- the demo code can pair many phones
);

CREATE TABLE IF NOT EXISTS patient_devices (
  token_hash    text PRIMARY KEY,                       -- sha256 hex of the bearer token (never the token)
  patient_id    text NOT NULL REFERENCES patients(id),
  device_name   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen     timestamptz,
  revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS patient_devices_patient_idx ON patient_devices (patient_id);

ALTER TABLE patients ADD COLUMN IF NOT EXISTS emergency_contact_relationship text;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS alert_therapist boolean NOT NULL DEFAULT true;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS next_visit_at timestamptz;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS share_by_default boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS device_prefs jsonb NOT NULL DEFAULT '{}';
ALTER TABLE therapists ADD COLUMN IF NOT EXISTS clinic text;

INSERT INTO schema_migrations (name) VALUES ('001_patient_identity.sql') ON CONFLICT (name) DO NOTHING;
```

Append the same DDL (everything except the final `INSERT INTO schema_migrations`, which `migrate.py` records) to the end of `server/db/schema.sql` under a comment `-- Plan 1 (2026-10-11): patient identity. Mirrors db/migrations/001_patient_identity.sql.` For the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` lines, instead add the columns directly in the `CREATE TABLE patients` / `CREATE TABLE therapists` blocks in schema.sql (`emergency_contact_relationship text`, `alert_therapist boolean NOT NULL DEFAULT true`, `next_visit_at timestamptz`, `share_by_default boolean NOT NULL DEFAULT false`, `device_prefs jsonb NOT NULL DEFAULT '{}'`; `clinic text` on therapists) and append only the three `CREATE TABLE IF NOT EXISTS` + index statements.

- [ ] **Step 4: Write the runner**

`server/db/migrate.py`:
```python
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
```

- [ ] **Step 5: Wire it into setup, tests, Makefile, deploy, server start**

`server/db/setup.py` — in `schema()` (lines 111-118) run migrations after the schema, whether or not it was skipped:
```python
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
```
and add `from server.db import migrate` to its imports.

`server/tests/dbutil.py` `prepare()` — after `apply_file(db_dir / "schema.sql", dsn)` add `migrate.apply_migrations(dsn)` (import `from server.db import migrate`).

`Makefile` — add to `.PHONY` and:
```make
# Apply idempotent migrations (server/db/migrations) to any DSN. The server also does this at start (DB_AUTO_MIGRATE=1).
db-migrate:
	@test -n "$(DSN)" || (echo 'usage: make db-migrate DSN="postgres://..."'; exit 2)
	@$(PY) -m server.db.migrate --dsn "$(DSN)"
```

`tools/deploy_vultr.sh` — inside the remote heredoc, before `systemctl restart mira`:
```bash
# Idempotent schema migrations (server/db/migrations) against the box's TIGER_DSN (read from its .env).
(cd $APP && sudo -u mira $APP/.venv/bin/python -m server.db.migrate) || echo "WARNING: migrations failed; the server will retry at start"
```

`server/config.py` — add after `DB_DISABLED`:
```python
    DB_AUTO_MIGRATE: bool = True  # apply server/db/migrations/*.sql (idempotent) at server start
```

`server/main.py` lifespan — after `await db.open()` (line 105):
```python
        if cfg.DB_AUTO_MIGRATE and db.available:
            try:
                applied = await asyncio.to_thread(migrate.apply_migrations, cfg.TIGER_DSN)
                log.info("migrations checked", extra={"count": len(applied)})
            except Exception as exc:  # noqa: BLE001 - a schema hiccup must never block the live demo
                log.warning("migrations failed; continuing", extra={"error": str(exc)})
```
with `from server.db import migrate, queries` replacing the existing `from server.db import queries`.

`.env.example` — add under the database section:
```
# Apply server/db/migrations/*.sql at start (idempotent). 0 to manage the schema by hand.
DB_AUTO_MIGRATE=1
```

- [ ] **Step 6: Run the test**

Run: `.venv/bin/python -m pytest server/tests/test_migrate.py server/tests/test_apply_sql.py -q`
Expected: PASS (2 new tests; apply_sql unchanged).

- [ ] **Step 7: Commit**

```bash
git add server/db/migrations server/db/migrate.py server/db/schema.sql server/db/setup.py server/tests/dbutil.py \
        server/tests/test_migrate.py Makefile tools/deploy_vultr.sh server/config.py server/main.py .env.example
git commit -m "Add idempotent DB migrations with the patient-identity tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Exercise catalog

**Files:**
- Create: `server/exercises.py`
- Modify: `server/engine_bridge.py:665,696`
- Test: `server/tests/test_exercises.py`

**Interfaces:**
- Produces: `server.exercises.display_name(exercise_id: str) -> str`, `server.exercises.describe(exercise_id: str) -> dict` (`{"exercise_id", "exercise_name", "metric_label", "unit", "target_key"}`), `server.exercises.EXERCISES`.

- [ ] **Step 1: Write the failing test**

`server/tests/test_exercises.py`:
```python
from __future__ import annotations

from server import exercises


def test_known_and_unknown_names() -> None:
    assert exercises.display_name("squat") == "Mini squats"  # what the LCD showed before the catalog existed
    assert exercises.display_name("glute_bridge") == "Glute bridge"
    assert exercises.display_name("single_leg_stance") == "Single-leg stance"
    assert exercises.display_name("heel_slide") == "Heel slide"  # unknown ids are humanised, never KeyError


def test_describe_shape() -> None:
    d = exercises.describe("squat")
    assert d == {"exercise_id": "squat", "exercise_name": "Mini squats", "metric_label": "Knee bend",
                 "unit": "°", "target_key": "knee_deg"}
    assert exercises.describe("mystery")["metric_label"] == "Progress"


def test_live_state_uses_the_catalog() -> None:
    from server.engine_bridge import LiveState

    live = LiveState()
    live.on_session_start("Maya", "glute_bridge")
    assert live.exercise_name == exercises.display_name("glute_bridge")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest server/tests/test_exercises.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.exercises'`.

- [ ] **Step 3: Implement**

`server/exercises.py`:
```python
"""The exercise catalog (MVP spec §9): one display name per exercise id, shared by the LCD caption, /me and the
phone's plan/progress screens so every surface says "Mini squats" the same way."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Exercise:
    exercise_id: str
    exercise_name: str
    metric_label: str   # the main metric, as the phone labels its big number
    unit: str
    target_key: str     # the prescriptions.targets key that holds the goal for the main metric


EXERCISES: dict[str, Exercise] = {e.exercise_id: e for e in (
    Exercise("squat", "Mini squats", "Knee bend", "°", "knee_deg"),
    Exercise("sit_to_stand", "Sit to stand", "Time to stand", "s", "time_s"),
    Exercise("single_leg_stance", "Single-leg stance", "Hold", "s", "hold_s"),
    Exercise("heel_raise", "Heel raises", "Heel height", "cm", "height_cm"),
    Exercise("glute_bridge", "Glute bridge", "Hip lift", "cm", "hold_s"),
    Exercise("bird_dog", "Bird-dog", "Trunk stability", "°", "hold_s"),
    Exercise("arm_raise", "Arm raise", "Shoulder angle", "°", "shoulder_deg"),
)}


def display_name(exercise_id: str) -> str:
    """Catalog name, or the id humanised ("heel_slide" → "Heel slide"). Never raises."""
    found = EXERCISES.get(exercise_id)
    return found.exercise_name if found else exercise_id.replace("_", " ").capitalize()


def describe(exercise_id: str) -> dict[str, str]:
    found = EXERCISES.get(exercise_id)
    if found is None:
        found = Exercise(exercise_id, display_name(exercise_id), "Progress", "", "")
    return asdict(found)
```

`server/engine_bridge.py` line 665: replace `EXERCISE_NAMES = {"squat": "Mini squats"}` with
```python
from server import exercises  # noqa: E402  (place with the other server imports at the top of the file instead)

EXERCISE_NAMES = {e.exercise_id: e.exercise_name for e in exercises.EXERCISES.values()}  # kept for callers
```
(put the import at the top with the other `from server...` imports) and line 696 with
`self.exercise_name = exercises.display_name(exercise_id)`.

Run `grep -rn EXERCISE_NAMES server device/kiosk` first; if anything else references it, keep the alias above so it still works.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_exercises.py server/tests/test_live_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/exercises.py server/engine_bridge.py server/tests/test_exercises.py
git commit -m "Add the exercise catalog; LiveState names exercises from it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `Principal` and patient tokens in `security.py`

**Files:**
- Modify: `server/security.py`, `server/config.py`, `server/db/queries.py`, `server/db/writes.py`, `server/main.py:295-316`
- Test: `server/tests/test_security_principal.py`

**Interfaces:**
- Produces (security):
  - `@dataclass(frozen=True) class Principal: scope: Literal["full","device","patient"]; patient_id: str | None = None` with `def owns(self, patient_id: str) -> bool` (full → True; patient → equal; device → False) and `def require(self, patient_id: str) -> None` (raises `HTTPException(403, "forbidden")` when not owner).
  - `PATIENT_TOKEN_PREFIX = "pt_"`, `def new_patient_token() -> str`, `def token_hash(token: str) -> str` (sha256 hex), `def mask(token: str) -> str` (`"…" + last 4`).
  - `async def resolve_token(token: str) -> Principal | None` — static tokens first; `pt_` tokens → DB lookup (60 s positive cache keyed by hash; raises `DBError` when the DB is down).
  - `async def require_principal(request) -> Principal` — full or patient, else 401 (device tokens are 401 on REST).
  - `async def require_token(request) -> None` — **unchanged contract**: full only, else 401.
  - `async def ws_scope(ws) -> Principal | None` (was a string; `main.py` is the only caller).
  - `def invalidate_token_cache() -> None` (tests, revocation).
- Produces (queries): `async def patient_device(db, token_hash: str) -> dict | None` (`{patient_id, revoked_at, last_seen}`).
- Produces (writes): `async def touch_patient_device(db, token_hash: str) -> bool`.
- Produces (config): `PAIRING_DEMO_CODE: str = "K7P4M2"`, `PAIRING_DEMO_CODE_REUSABLE: bool = True`.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_security_principal.py`:
```python
"""Principal resolution: static tokens, patient tokens (hashed, cached, revocable), REST and WS behaviour."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import security
from server.tests.conftest import AUTH, DEVICE_TOKEN, TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn


def test_principal_ownership() -> None:
    assert security.Principal("full").owns("maya") and security.Principal("full").owns("anyone")
    assert security.Principal("patient", "maya").owns("maya")
    assert not security.Principal("patient", "maya").owns("bob")
    assert not security.Principal("device").owns("maya")


def test_token_helpers() -> None:
    tok = security.new_patient_token()
    assert tok.startswith(security.PATIENT_TOKEN_PREFIX) and len(tok) >= 40
    assert len(security.token_hash(tok)) == 64 and security.token_hash(tok) != tok
    assert security.mask(tok) == "…" + tok[-4:]


@pytest.fixture(scope="module")
def dsn():
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn), MIRA_DEVICE_TOKEN=DEVICE_TOKEN)


def _insert_device(dsn: str, token: str, patient_id: str = "maya") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO patient_devices (token_hash, patient_id, device_name) VALUES (%s, %s, 'test')",
                     (security.token_hash(token), patient_id))


def test_static_tokens_resolve(client: TestClient) -> None:
    import asyncio

    assert asyncio.run(security.resolve_token(TOKEN)) == security.Principal("full")
    assert asyncio.run(security.resolve_token(DEVICE_TOKEN)) == security.Principal("device")
    assert asyncio.run(security.resolve_token("nope")) is None
    assert asyncio.run(security.resolve_token("pt_unknown")) is None


def test_patient_token_rest_and_revocation(client: TestClient, dsn: str) -> None:
    token = security.new_patient_token()
    _insert_device(dsn, token)
    headers = {"Authorization": f"Bearer {token}"}
    # /stats is full-only (require_token): a patient token is 401 there, as before.
    assert client.get("/stats", headers=headers).status_code == 401
    # /health stays public; a route that takes require_principal accepts the patient (added in Task 5: /me).
    assert client.get("/health").status_code == 200
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE patient_devices SET revoked_at = now() WHERE token_hash = %s",
                     (security.token_hash(token),))
    security.invalidate_token_cache()
    import asyncio

    assert asyncio.run(security.resolve_token(token)) is None


def test_device_token_is_401_on_rest(client: TestClient) -> None:
    assert client.get("/stats", headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}).status_code == 401


def test_ws_scope_returns_principals(client: TestClient, dsn: str) -> None:
    token = security.new_patient_token()
    _insert_device(dsn, token)
    # A patient socket is accepted on /ws/live (Task 7 filters its traffic); unknown tokens still close 1008.
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] in ("live", "ping") or "phase" in msg
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/live?token=pt_bogus"):
            pass
    assert exc.value.code == 1008
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest server/tests/test_security_principal.py -q`
Expected: FAIL — `AttributeError: module 'server.security' has no attribute 'Principal'`.

- [ ] **Step 3: Config**

`server/config.py`, after `DEMO_TOOLS_ENABLED`:
```python
    PAIRING_DEMO_CODE: str = "K7P4M2"  # therapist code the seed binds to Maya (the iOS design preview uses it)
    PAIRING_DEMO_CODE_REUSABLE: bool = True  # the demo code may pair many phones; real codes are single-use
```

- [ ] **Step 4: Query and write**

`server/db/queries.py`, new section after `profile_row`:
```python
# ---- patient identity (Plan 1) ------------------------------------------------------------------------------

async def patient_device(db: Database, token_hash: str, timeout_s: float = 1.5) -> dict[str, Any] | None:
    """The phone that owns a patient token (by hash), including revoked_at so the caller can refuse it."""
    return await db.fetchrow("SELECT token_hash, patient_id, device_name, last_seen, revoked_at FROM patient_devices "
                             "WHERE token_hash = %s", (token_hash,), timeout_s=timeout_s)
```

`server/db/writes.py`, new section before `# ---- helpers`:
```python
# ---- patient identity (Plan 1) ------------------------------------------------------------------------------

async def touch_patient_device(db: Database | None, token_hash: str) -> bool:
    sql = "UPDATE patient_devices SET last_seen = now() WHERE token_hash = %s AND revoked_at IS NULL"
    return await _safe(db, "touch_patient_device", lambda d: _ok(d.execute(sql, (token_hash,))), False)
```

- [ ] **Step 5: security.py**

Replace lines 22-52 of `server/security.py` with:
```python
import hashlib
import time
from dataclasses import dataclass

from server.db.pool import DBError

PATIENT_TOKEN_PREFIX = "pt_"
TOKEN_CACHE_S = 60.0
TOUCH_INTERVAL_S = 60.0

Scope = Literal["full", "device", "patient"]


@dataclass(frozen=True)
class Principal:
    """Who a token belongs to. `full` = MIRA_API_TOKEN (dashboard/ops), `device` = the LCD, `patient` = one phone."""

    scope: Scope
    patient_id: str | None = None

    def owns(self, patient_id: str) -> bool:
        return self.scope == "full" or (self.scope == "patient" and self.patient_id == patient_id)

    def require(self, patient_id: str) -> None:
        if not self.owns(patient_id):
            raise HTTPException(status_code=403, detail="forbidden")


def _token_matches(given: str, expected: str) -> bool:
    if not expected:
        return False
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def new_patient_token() -> str:
    return PATIENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask(token: str) -> str:
    """For logs: never the token, only its tail."""
    return "…" + token[-4:]


# token hash → (Principal, expires_at, last_touched). Positive results only; a revoked token is refused as soon as
# the entry expires (≤ 60 s) or invalidate_token_cache() runs (DELETE /me, device revocation).
_cache: dict[str, tuple[Principal, float, float]] = {}


def invalidate_token_cache() -> None:
    _cache.clear()


async def _patient_principal(token: str) -> Principal | None:
    from server.db import queries, writes  # local import: security is imported by the db layer's callers
    from server.state import get_state

    digest = token_hash(token)
    now = time.monotonic()
    cached = _cache.get(digest)
    if cached is not None and cached[1] > now:
        principal, expires, touched = cached
        if now - touched > TOUCH_INTERVAL_S:
            _cache[digest] = (principal, expires, now)
            writes.fire(writes.touch_patient_device(get_state().db, digest))
        return principal
    db = get_state().db
    if db is None:
        return None
    row = await queries.patient_device(db, digest)  # DBError propagates → 503, never a false 401
    if row is None or row["revoked_at"] is not None:
        return None
    principal = Principal("patient", row["patient_id"])
    _cache[digest] = (principal, now + TOKEN_CACHE_S, now)
    writes.fire(writes.touch_patient_device(db, digest))
    return principal


async def resolve_token(token: str) -> Principal | None:
    """Which principal a bearer/`?token=` value is, or None. Static tokens never touch the database."""
    if not token:
        return None
    if _token_matches(token, cfg.MIRA_API_TOKEN):
        return Principal("full")
    if _token_matches(token, cfg.MIRA_DEVICE_TOKEN):
        return Principal("device")
    if token.startswith(PATIENT_TOKEN_PREFIX):
        return await _patient_principal(token)
    return None


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency for routes patients may call: the full token or a patient token, else 401. The LCD's
    device token is never accepted on REST."""
    principal = await resolve_token(_bearer(request))
    if principal is None or principal.scope == "device":
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return principal


async def require_token(request: Request) -> None:
    """FastAPI dependency: `Authorization: Bearer <MIRA_API_TOKEN>` or 401 (unchanged: full scope only)."""
    if not _token_matches(_bearer(request), cfg.MIRA_API_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})


async def ws_token_ok(ws: WebSocket) -> bool:
    """Check `?token=` before `ws.accept()` (browsers can't set headers on WebSockets). Full token only."""
    return _token_matches(ws.query_params.get("token", ""), cfg.MIRA_API_TOKEN)


async def ws_scope(ws: WebSocket) -> Principal | None:
    """The principal behind `?token=`: full, the LCD device token, a patient's phone, or None. Device scope is
    only honoured on the channels that opt in (see server.main). A database failure while checking a patient
    token returns None (the phone reconnects with backoff)."""
    try:
        return await resolve_token(ws.query_params.get("token", ""))
    except DBError as exc:
        log.warning("ws token check failed (database)", extra={"error": str(exc)})
        return None
```
Keep the `Request`/`WebSocket`/`HTTPException` imports; remove the old `Scope = Literal["full", "device"]`.

Note the former `_token_matches` returned False when `cfg.MIRA_API_TOKEN` was empty; the new version returns False when `expected` is empty, which covers both tokens.

- [ ] **Step 6: Adapt `main.py._serve_ws` to the Principal (minimal; Task 7 adds the patient filter)**

Replace `_serve_ws` (lines 295-315):
```python
async def _serve_ws(ws: WebSocket, channel: str, on_message: Any = None, *, allow_device: bool = False) -> None:
    principal = await ws_scope(ws)
    if principal is None or (principal.scope == "device" and not allow_device):
        await ws.close(code=1008)
        return
    await ws.accept()
    hub = get_state().hub
    if principal.scope == "full":
        await hub.connect(channel, ws, on_message)
        return
    if principal.scope == "patient":
        await _serve_patient_ws(ws, channel, principal)
        return
    if channel != "events":
        await hub.connect(channel, ws)
        return
    devices = get_state().devices
    if devices is not None:
        devices.connected()
    try:
        await hub.connect(channel, ws, _device_event_handler(), accept=_device_accepts)
    finally:
        if devices is not None:
            devices.disconnected()


async def _serve_patient_ws(ws: WebSocket, channel: str, principal: Principal) -> None:
    """Placeholder until Task 7: a patient socket behaves like a full one. Task 7 adds the own-session filter and
    the restricted client handler."""
    hub = get_state().hub
    await hub.connect(channel, ws, _client_event_handler() if channel == "events" else None)
```
Import `Principal` from `server.security`.

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest server/tests/test_security_principal.py server/tests/test_security.py server/tests/test_device_token.py server/tests/test_ws_device.py -q`
Expected: PASS (existing security/device tests unchanged).

- [ ] **Step 8: Commit**

```bash
git add server/security.py server/config.py server/db/queries.py server/db/writes.py server/main.py server/tests/test_security_principal.py
git commit -m "Add Principal scopes: patient tokens resolved from patient_devices

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Pairing — `POST /pair`, codes, seed

**Files:**
- Create: `server/api/pair.py`, `server/api/identity_models.py` (shared Pydantic models: `ContactIn`, `ProfileIn`, `ProfilePatch`, `MeUpdate` — so `pair.py` and `me.py` never import each other)
- Modify: `server/db/queries.py`, `server/db/writes.py`, `server/db/seed.py`, `server/main.py` (router), `server/tests/test_security.py:20` (PUBLIC_ROUTES)
- Test: `server/tests/test_pairing.py`

**Interfaces:**
- Consumes: `security.new_patient_token/token_hash/mask/require_token`, `exercises.describe`, Task 1 tables.
- Produces (queries): `pairing_code(db, code) -> dict | None`, `patient_me_row(db, patient_id) -> dict | None` (patient columns + `therapist_name, therapist_clinic, therapist_phone`).
- Produces (writes): `insert_patient_device(db, token_hash, patient_id, device_name) -> bool`, `mark_pairing_code_used(db, code) -> bool`, `upsert_pairing_code(db, code, patient_id, therapist_id, expires_at, reusable) -> bool`, `revoke_patient_devices(db, patient_id) -> int | None`, `update_patient_profile(db, patient_id, **fields) -> bool` (fields: `name, height_cm, mass_kg, affected_side, condition, emergency_contact_name, emergency_contact_phone, emergency_contact_relationship, alert_therapist, share_by_default, device_prefs`; `None` leaves unchanged).
- Produces (api): `POST /pair` (public, 5/min/IP) → `{patient_id, token, me}`; `POST /patients/{id}/pairing-code` (full) → `{code, expires_at}`; `DELETE /patients/{id}/devices` (full) → `{revoked: n}`. `api.pair.PairIn`, `ProfileIn`, `ContactIn` Pydantic models reused by `/me` (Task 5).
- `me` is built by `server.api.me.build_me(db, patient_id)` — Task 5 creates it; in this task create `server/api/me.py` with only `build_me` (Task 5 adds the routes).

- [ ] **Step 1: Write the failing tests**

`server/tests/test_pairing.py`:
```python
"""POST /pair: therapist code → patient token; demo code reusable; errors; admin code minting and revocation."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

PROFILE = {"name": "Maya Ellis", "height_cm": 163, "mass_kg": 70, "affected_side": "right",
           "condition": "Knee replacement"}
CONTACT = {"name": "Eric", "phone": "+15550100111", "relationship": "son"}


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


def _pair(client: TestClient, code: str = "K7P4M2", **extra: object) -> object:
    body = {"code": code, "device_name": "iPhone", "profile": PROFILE, "emergency_contact": CONTACT,
            "alert_therapist": True, **extra}
    return client.post("/pair", json=body)


def test_pair_with_demo_code_returns_token_and_me(client: TestClient, dsn: str) -> None:
    r = _pair(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patient_id"] == "maya" and body["token"].startswith("pt_")
    me = body["me"]
    assert me["patient"]["first_name"] == "Maya" and me["patient"]["emergency_contact"]["name"] == "Eric"
    assert me["patient"]["emergency_contact"]["relationship"] == "son"
    assert me["therapist"]["name"] == "Dr. Okafor" and me["therapist"]["clinic"] == "Lakeside Rehab"
    assert {p["exercise_id"] for p in me["prescriptions"]} == {"squat", "glute_bridge", "single_leg_stance"}
    assert next(p for p in me["prescriptions"] if p["exercise_id"] == "squat")["exercise_name"] == "Mini squats"
    assert "online" in me["lcd"] and me["calibration"]["calibrated"] is False
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT patient_id, device_name FROM patient_devices WHERE token_hash = %s",
                           (security.token_hash(body["token"]),)).fetchone()
        assert row == ("maya", "iPhone")
        assert conn.execute("SELECT count(*) FROM patient_devices WHERE patient_id = 'maya'").fetchone()[0] >= 1
        # the demo code stays usable
        assert conn.execute("SELECT used_at FROM pairing_codes WHERE code = 'K7P4M2'").fetchone()[0] is None
    # the token works on a patient route (Task 5 adds /me; here: the token resolves)
    import asyncio

    assert asyncio.run(security.resolve_token(body["token"])) == security.Principal("patient", "maya")


def test_pair_is_case_insensitive_and_second_phone_gets_its_own_token(client: TestClient) -> None:
    a = _pair(client, code="k7p4m2").json()["token"]
    b = _pair(client).json()["token"]
    assert a != b


def test_bad_codes(client: TestClient, dsn: str) -> None:
    assert _pair(client, code="ZZZZZZ").status_code == 404
    assert _pair(client, code="ABC").status_code == 422
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO pairing_codes (code, patient_id, therapist_id, expires_at) "
                     "VALUES ('EXPIRD', 'maya', 'okafor', now() - interval '1 minute') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO pairing_codes (code, patient_id, therapist_id, used_at) "
                     "VALUES ('USEDUP', 'maya', 'okafor', now()) ON CONFLICT DO NOTHING")
    assert _pair(client, code="EXPIRD").status_code == 410
    assert _pair(client, code="USEDUP").status_code == 410


def test_pair_needs_no_token_but_is_rate_limited(client: TestClient) -> None:
    from server.api import pair as pair_api

    pair_api._attempts.clear()
    for _ in range(pair_api.MAX_ATTEMPTS_PER_MIN):
        assert _pair(client, code="ZZZZZZ").status_code == 404
    assert _pair(client, code="ZZZZZZ").status_code == 429
    pair_api._attempts.clear()


def test_admin_mints_single_use_code_and_revokes_devices(client: TestClient, dsn: str) -> None:
    r = client.post("/patients/maya/pairing-code", headers=AUTH)
    assert r.status_code == 200
    code = r.json()["code"]
    assert len(code) == 6 and r.json()["expires_at"]
    assert client.post("/patients/nobody/pairing-code", headers=AUTH).status_code == 404
    assert client.post("/patients/maya/pairing-code").status_code == 401
    first = _pair(client, code=code)
    assert first.status_code == 200
    assert _pair(client, code=code).status_code == 410  # single use
    token = first.json()["token"]
    r = client.delete("/patients/maya/devices", headers=AUTH)
    assert r.status_code == 200 and r.json()["revoked"] >= 1
    import asyncio

    assert asyncio.run(security.resolve_token(token)) is None


def test_pair_without_database_is_503(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert _pair(client).status_code == 503
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest server/tests/test_pairing.py -q`
Expected: FAIL — 404 on `/pair` (route missing) / import errors.

- [ ] **Step 3: Queries**

Add to `server/db/queries.py` patient-identity section:
```python
async def pairing_code(db: Database, code: str) -> dict[str, Any] | None:
    return await db.fetchrow("SELECT code, patient_id, therapist_id, expires_at, used_at, reusable FROM pairing_codes "
                             "WHERE code = %s", (code,))


ME_SQL = """
    SELECT p.id, p.name, p.age, p.height_cm, p.mass_kg, p.affected_side, p.condition, p.hr_limit,
           p.emergency_contact_name, p.emergency_contact_phone, p.emergency_contact_relationship,
           p.alert_therapist, p.share_by_default, p.device_prefs, p.next_visit_at, p.simulated,
           t.id AS therapist_id, t.name AS therapist_name, t.clinic AS therapist_clinic, t.phone AS therapist_phone
    FROM patients p LEFT JOIN therapists t ON t.id = p.therapist_id
    WHERE p.id = %s"""


async def patient_me_row(db: Database, patient_id: str) -> dict[str, Any] | None:
    """Everything GET /me shows about the patient and their therapist, in one round trip."""
    return await db.fetchrow(ME_SQL, (patient_id,))
```
Also extend `get_patient` (line 156) to `SELECT p.*, t.name AS therapist_name, t.clinic AS therapist_clinic, t.phone AS therapist_phone` (additive; the dashboard ignores extra keys).

- [ ] **Step 4: Writes**

Add to `server/db/writes.py` patient-identity section:
```python
async def insert_patient_device(db: Database | None, token_hash: str, patient_id: str,
                                device_name: str | None) -> bool:
    sql = "INSERT INTO patient_devices (token_hash, patient_id, device_name) VALUES (%s, %s, %s)"
    return await _safe(db, "insert_patient_device", lambda d: _ok(d.execute(sql, (token_hash, patient_id,
                                                                                 device_name))), False,
                       patient_id=patient_id)


async def revoke_patient_devices(db: Database | None, patient_id: str) -> int | None:
    sql = "UPDATE patient_devices SET revoked_at = now() WHERE patient_id = %s AND revoked_at IS NULL"
    return await _safe(db, "revoke_patient_devices", lambda d: d.execute(sql, (patient_id,)), None,
                       patient_id=patient_id)


async def upsert_pairing_code(db: Database | None, code: str, patient_id: str, therapist_id: str | None,
                              expires_at: Timestamp = None, reusable: bool = False) -> bool:
    sql = ("INSERT INTO pairing_codes (code, patient_id, therapist_id, expires_at, reusable) "
           "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET patient_id = EXCLUDED.patient_id, "
           "therapist_id = EXCLUDED.therapist_id, expires_at = EXCLUDED.expires_at, reusable = EXCLUDED.reusable, "
           "used_at = NULL")
    return await _safe(db, "upsert_pairing_code", lambda d: _done(d.execute(
        sql, (code, patient_id, therapist_id, ts(expires_at) if expires_at is not None else None, reusable))),
        False, patient_id=patient_id)


async def mark_pairing_code_used(db: Database | None, code: str) -> bool:
    sql = "UPDATE pairing_codes SET used_at = now() WHERE code = %s AND NOT reusable"
    return await _safe(db, "mark_pairing_code_used", lambda d: _done(d.execute(sql, (code,))), False)


PROFILE_FIELDS = ("name", "height_cm", "mass_kg", "affected_side", "condition", "emergency_contact_name",
                  "emergency_contact_phone", "emergency_contact_relationship", "alert_therapist",
                  "share_by_default", "device_prefs")


async def update_patient_profile(db: Database | None, patient_id: str, **fields: Any) -> bool:
    """Partial update of the patient's own fields (PROFILE_FIELDS); None leaves a column unchanged. Raises
    InvalidInput for unknown fields."""
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise InvalidInput(f"unknown profile fields: {sorted(unknown)}")
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return True
    assignments = ", ".join(f"{k} = %({k})s" for k in changes)
    params: dict[str, Any] = {k: (_jsonb(v) if k == "device_prefs" else v) for k, v in changes.items()}
    params["pid"] = patient_id
    sql = f"UPDATE patients SET {assignments} WHERE id = %(pid)s"
    return await _safe(db, "update_patient_profile", lambda d: _ok(d.execute(sql, params)), False,
                       patient_id=patient_id)
```
(`fields` keys are validated against the fixed tuple before being interpolated, so the SQL stays fixed-shape.)

- [ ] **Step 5: Seed**

`server/db/seed.py`: 
```python
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

CLINIC = "Lakeside Rehab"


def next_tuesday_1030(now: datetime | None = None) -> datetime:
    """The patient's next visit for the demo: next Tuesday 10:30 in the server's local zone."""
    tz = ZoneInfo(cfg.TZ_NAME)
    now = now or datetime.now(tz)
    days = (1 - now.weekday()) % 7 or 7  # Monday=0 → Tuesday=1
    day = (now + timedelta(days=days)).date()
    return datetime.combine(day, dtime(10, 30), tz)


def upsert_therapist(conn):  # extend the existing INSERT with clinic
    conn.execute("INSERT INTO therapists (id, name, phone, clinic) VALUES (%s, %s, %s, %s) "
                 "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone, clinic = EXCLUDED.clinic",
                 (THERAPIST_ID, "Dr. Okafor", cfg.DEMO_THERAPIST_PHONE, CLINIC))


def upsert_pairing_code(conn, code: str, patient_id: str, reusable: bool) -> None:
    conn.execute("INSERT INTO pairing_codes (code, patient_id, therapist_id, reusable) VALUES (%s, %s, %s, %s) "
                 "ON CONFLICT (code) DO UPDATE SET patient_id = EXCLUDED.patient_id, reusable = EXCLUDED.reusable, "
                 "used_at = NULL", (code.upper(), patient_id, THERAPIST_ID, reusable))
```
and in `seed()` after `ensure_prescriptions`:
```python
    upsert_pairing_code(conn, cfg.PAIRING_DEMO_CODE, MAYA_ID, reusable=cfg.PAIRING_DEMO_CODE_REUSABLE)
    conn.execute("UPDATE patients SET next_visit_at = COALESCE(next_visit_at, %s) WHERE id = %s",
                 (next_tuesday_1030(), MAYA_ID))
```
Update the `print` in `main` and the setup step note to mention the pairing code.

- [ ] **Step 6: `build_me` and the pair router**

`server/api/me.py` (routes added in Task 5):
```python
"""The patient's own view: profile, care team, plan, device status (Plan 1 §4.2). Routes in Task 5."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from server import exercises
from server.api.calibration import placeholder_status
from server.db import queries
from server.db.pool import Database
from server.state import get_state

router = APIRouter()


def _first_name(name: str | None) -> str:
    return (name or "").split(" ")[0] or "there"


def prescription_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"exercise_id": row["exercise_id"], "exercise_name": exercises.display_name(row["exercise_id"]),
            "sets": row["sets"], "reps": row["reps"], "targets": row["targets"] or {}, "limits": row["limits"] or {},
            "version": row["version"], **{k: v for k, v in exercises.describe(row["exercise_id"]).items()
                                          if k in ("metric_label", "unit", "target_key")}}


async def build_me(db: Database, patient_id: str) -> dict[str, Any]:
    row, rx = await asyncio.gather(queries.patient_me_row(db, patient_id), queries.prescriptions(db, patient_id))
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    state = get_state()
    lcd = state.devices.status() if state.devices is not None else {"online": False, "last_seen": None}
    contact = ({"name": row["emergency_contact_name"], "phone": row["emergency_contact_phone"],
                "relationship": row["emergency_contact_relationship"]} if row["emergency_contact_name"] else None)
    return {
        "patient": {
            "id": row["id"], "name": row["name"], "first_name": _first_name(row["name"]), "age": row["age"],
            "height_cm": row["height_cm"], "mass_kg": row["mass_kg"], "affected_side": row["affected_side"],
            "condition": row["condition"], "hr_limit": row["hr_limit"], "emergency_contact": contact,
            "alert_therapist": row["alert_therapist"], "share_by_default": row["share_by_default"],
            "device_prefs": row["device_prefs"] or {}, "next_visit_at": row["next_visit_at"],
        },
        "therapist": ({"id": row["therapist_id"], "name": row["therapist_name"], "clinic": row["therapist_clinic"],
                       "phone": row["therapist_phone"]} if row["therapist_id"] else None),
        "prescriptions": [prescription_view(r) for r in rx],
        "lcd": {"online": bool(lcd.get("online")), "last_seen": lcd.get("last_seen")},
        "calibration": placeholder_status(),
    }
```
Check `server/api/calibration.py`: if its placeholder dict is built inline in the route, extract it into a module-level `def placeholder_status() -> dict[str, Any]` and have the route return it (behaviour unchanged).

`server/api/identity_models.py`:
```python
"""Request models shared by /pair and /me (kept apart so the two routers don't import each other)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=7, max_length=20)
    relationship: str | None = Field(default=None, max_length=40)


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    height_cm: float = Field(gt=50, lt=250)
    mass_kg: float = Field(gt=20, lt=400)
    affected_side: Literal["left", "right", "none"] | None = None
    condition: str | None = Field(default=None, max_length=120)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    height_cm: float | None = Field(default=None, gt=50, lt=250)
    mass_kg: float | None = Field(default=None, gt=20, lt=400)
    affected_side: Literal["left", "right", "none"] | None = None
    condition: str | None = Field(default=None, max_length=120)


class MeUpdate(BaseModel):
    profile: ProfilePatch | None = None
    emergency_contact: ContactIn | None = None
    alert_therapist: bool | None = None
    share_by_default: bool | None = None
    device_prefs: dict[str, Any] | None = None

    @field_validator("device_prefs")
    @classmethod
    def _small(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and (len(v) > 20 or any(not isinstance(k, str) or len(k) > 40 for k in v)):
            raise ValueError("device_prefs: at most 20 short keys")
        return v
```

`server/api/pair.py`:
```python
"""Pairing a phone to a patient with a therapist code (Plan 1 §4.2). Public route, rate-limited per IP."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from server import security
from server.api.db_dep import require_db
from server.api.identity_models import ContactIn, ProfileIn
from server.api.me import build_me
from server.db import queries, writes
from server.db.pool import Database
from server.logging_setup import get_logger

log = get_logger("pair")
router = APIRouter()

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
CODE_TTL = timedelta(hours=24)
MAX_ATTEMPTS_PER_MIN = 5
_attempts: dict[str, list[float]] = defaultdict(list)


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    device_name: str | None = Field(default=None, max_length=60)
    profile: ProfileIn
    emergency_contact: ContactIn | None = None
    alert_therapist: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.strip().upper()
        if not CODE_RE.match(v):
            raise ValueError("code must be 6 letters or digits")
        return v


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _attempts[ip] if now - t < 60.0]
    _attempts[ip] = recent
    if len(recent) >= MAX_ATTEMPTS_PER_MIN:
        return True
    recent.append(now)
    return False


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


@router.post("/pair")
async def pair(body: PairIn, request: Request, db: Database = Depends(require_db)) -> dict[str, Any]:
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="too many pairing attempts; try again in a minute")
    row = await queries.pairing_code(db, body.code)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown code")
    if row["expires_at"] is not None and row["expires_at"] < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="code expired")
    if row["used_at"] is not None and not row["reusable"]:
        raise HTTPException(status_code=410, detail="code already used")
    patient_id = row["patient_id"]
    contact = body.emergency_contact
    ok = await writes.update_patient_profile(
        db, patient_id, name=body.profile.name, height_cm=body.profile.height_cm, mass_kg=body.profile.mass_kg,
        affected_side=body.profile.affected_side, condition=body.profile.condition,
        emergency_contact_name=contact.name if contact else None,
        emergency_contact_phone=contact.phone if contact else None,
        emergency_contact_relationship=contact.relationship if contact else None,
        alert_therapist=body.alert_therapist)
    if not ok:
        raise HTTPException(status_code=503, detail="database unavailable: could not save the profile")
    token = security.new_patient_token()
    if not await writes.insert_patient_device(db, security.token_hash(token), patient_id, body.device_name):
        raise HTTPException(status_code=503, detail="database unavailable: could not pair the phone")
    await writes.mark_pairing_code_used(db, body.code)
    log.info("phone paired", extra={"patient_id": patient_id, "device": body.device_name,
                                    "token": security.mask(token)})
    return {"patient_id": patient_id, "token": token, "me": await build_me(db, patient_id)}


@router.post("/patients/{patient_id}/pairing-code", dependencies=[Depends(security.require_token)])
async def mint_pairing_code(patient_id: str, db: Database = Depends(require_db)) -> dict[str, Any]:
    """Therapist/dashboard: a fresh single-use code for this patient, valid 24 h."""
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    code, expires_at = new_code(), datetime.now(UTC) + CODE_TTL
    if not await writes.upsert_pairing_code(db, code, patient_id, patient.get("therapist_id"), expires_at, False):
        raise HTTPException(status_code=503, detail="database unavailable: could not create a code")
    return {"code": code, "expires_at": expires_at}


@router.delete("/patients/{patient_id}/devices", dependencies=[Depends(security.require_token)])
async def revoke_devices(patient_id: str, db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    revoked = await writes.revoke_patient_devices(db, patient_id)
    if revoked is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not revoke")
    security.invalidate_token_cache()
    return {"patient_id": patient_id, "revoked": revoked}
```

`server/main.py` `create_app`: `from server.api import pair as pair_api` and `app.include_router(pair_api.router)` (its two admin routes carry their own `require_token`).

`server/tests/test_security.py:20`: `PUBLIC_ROUTES = {"/health", "/retell/webhook", "/pair"}`.

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest server/tests/test_pairing.py server/tests/test_security.py server/tests/test_db_unit.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add server/api/pair.py server/api/me.py server/api/identity_models.py server/api/calibration.py server/db/queries.py server/db/writes.py \
        server/db/seed.py server/main.py server/tests/test_pairing.py server/tests/test_security.py
git commit -m "POST /pair: therapist code → patient token; demo code K7P4M2 seeded

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `/me` routes

**Files:**
- Modify: `server/api/me.py`, `server/db/writes.py`, `server/db/queries.py`, `server/main.py`, `server/tests/test_security.py:49-54`
- Test: `server/tests/test_me.py`

**Interfaces:**
- Consumes: `security.require_principal`, `Principal.require`, `build_me`, `api.identity_models.MeUpdate`.
- Produces: `GET /me`, `PUT /me` (`MeUpdate{profile?: ProfilePatch, emergency_contact?: ContactIn, alert_therapist?, share_by_default?, device_prefs?: dict (≤20 keys)}`), `DELETE /me` → `{deleted: true, sessions_deleted: n}`, `GET /me/export` → JSON document. `writes.purge_patient_data(db, patient_id) -> dict | None`, `queries.patient_export(db, patient_id) -> dict`.
- Changes `test_every_rest_route_requires_token_except_public` to accept `require_principal` as well.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_me.py`:
```python
"""GET/PUT/DELETE /me and /me/export with a patient token; a full token may act as the patient via ?patient_id."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_get_me(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/me", headers=phone)
    assert r.status_code == 200
    me = r.json()
    assert me["patient"]["id"] == "maya" and me["therapist"]["name"] == "Dr. Okafor"
    assert me["patient"]["next_visit_at"]
    assert client.get("/me").status_code == 401
    # The admin token can read a patient's view by id (dashboard "see what the patient sees").
    assert client.get("/me?patient_id=maya", headers=AUTH).json()["patient"]["id"] == "maya"
    assert client.get("/me", headers=AUTH).status_code == 400  # admin must say which patient


def test_put_me_partial_update(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    r = client.put("/me", headers=phone, json={"emergency_contact": {"name": "Ana", "phone": "+15550100999",
                                                                     "relationship": "daughter"},
                                               "share_by_default": True,
                                               "device_prefs": {"captions": False, "volume": 0.4}})
    assert r.status_code == 200
    me = r.json()
    assert me["patient"]["emergency_contact"]["name"] == "Ana" and me["patient"]["share_by_default"] is True
    assert me["patient"]["device_prefs"] == {"captions": False, "volume": 0.4}
    assert me["patient"]["height_cm"] == 163  # untouched
    r = client.put("/me", headers=phone, json={"profile": {"mass_kg": 68}})
    assert r.status_code == 200 and r.json()["patient"]["mass_kg"] == 68
    assert client.put("/me", headers=phone, json={"device_prefs": {str(i): i for i in range(30)}}).status_code == 422


def test_export_and_delete(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    from server.state import get_state

    sid = get_state().sessions.current.session_id  # the live mock session is Maya's and not simulated
    export = client.get("/me/export", headers=phone)
    assert export.status_code == 200
    doc = export.json()
    assert doc["patient"]["id"] == "maya" and any(s["id"] == sid for s in doc["sessions"])
    assert "pose_frames_count" in doc and "reps" in doc and "alerts" in doc

    with psycopg.connect(dsn) as conn:
        simulated_before = conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND simulated").fetchone()[0]
    r = client.delete("/me", headers=phone)
    assert r.status_code == 200 and r.json()["deleted"] is True
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND NOT simulated").fetchone()[0] == 0
        # labeled demo history is not the patient's own data and survives
        assert conn.execute("SELECT count(*) FROM sessions WHERE patient_id='maya' AND simulated").fetchone()[0] == simulated_before
        row = conn.execute("SELECT emergency_contact_name, height_cm FROM patients WHERE id='maya'").fetchone()
        assert row == (None, None)
        assert conn.execute("SELECT count(*) FROM patient_devices WHERE patient_id='maya' AND revoked_at IS NULL").fetchone()[0] == 0
    # the token is gone
    security.invalidate_token_cache()
    assert client.get("/me", headers=phone).status_code == 401
    # the live session was Maya's: it has been stopped
    assert get_state().sessions.current is None or get_state().sessions.current.session_id != sid
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest server/tests/test_me.py -q`
Expected: FAIL — `/me` 404.

- [ ] **Step 3: Queries and writes**

`server/db/queries.py`:
```python
async def patient_export(db: Database, patient_id: str) -> dict[str, Any]:
    """Everything DELETE /me would remove, as JSON-able rows (no pose frames, only their count)."""
    sessions = await db.fetch("SELECT * FROM sessions WHERE patient_id = %s AND NOT simulated ORDER BY started_at",
                              (patient_id,), timeout_s=20)
    ids = [s["id"] for s in sessions]
    reps = await db.fetch("SELECT * FROM reps WHERE session_id = ANY(%s) ORDER BY session_id, rep", (ids,),
                          timeout_s=20) if ids else []
    flags = await db.fetch("SELECT time, session_id, rep, rule_id, severity, duration_s FROM form_events "
                           "WHERE session_id = ANY(%s) ORDER BY time", (ids,), timeout_s=20) if ids else []
    alerts = await db.fetch("SELECT time, alert_id, session_id, kind, step, channel, payload FROM alerts "
                            "WHERE patient_id = %s ORDER BY time", (patient_id,), timeout_s=20)
    memory = await db.fetch("SELECT time, kind, message, evidence FROM memory_events WHERE patient_id = %s "
                            "ORDER BY time", (patient_id,), timeout_s=10)
    frames = await db.fetchrow("SELECT count(*) AS n FROM pose_frames WHERE session_id = ANY(%s)", (ids,),
                               timeout_s=30) if ids else {"n": 0}
    return {"sessions": sessions, "reps": reps, "form_events": flags, "alerts": alerts, "memory_events": memory,
            "pose_frames_count": int(frames["n"]) if frames else 0}
```

`server/db/writes.py`:
```python
async def purge_patient_data(db: Database | None, patient_id: str) -> dict[str, int] | None:
    """DELETE /me: the patient's own (non-simulated) sessions and everything hanging off them, their alerts,
    memory events, notifications, agent actions and phones; profile fields are blanked. Labeled simulated demo
    history is kept: it is not the patient's data. The patient row and prescriptions stay (the therapist's plan).
    One transaction; None on database failure."""
    if db is None:
        return None

    async def run(d: Database) -> dict[str, int]:
        async with d.connection() as conn, conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = 120000")
            cur = await conn.execute("SELECT id FROM sessions WHERE patient_id = %s AND NOT simulated", (patient_id,))
            ids = [r["id"] for r in await cur.fetchall()]
            counts: dict[str, int] = {"sessions": len(ids)}
            if ids:
                for table in ("pose_frames", "reps", "form_events", "wrist_motion", "heart_rate"):
                    cur = await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY(%s)", (ids,))  # noqa: S608
                    counts[table] = cur.rowcount
            for table in ("alerts", "memory_events", "notifications", "agent_actions", "plan_proposals"):
                cur = await conn.execute(f"DELETE FROM {table} WHERE patient_id = %s", (patient_id,))  # noqa: S608
                counts[table] = cur.rowcount
            if ids:
                await conn.execute("DELETE FROM sessions WHERE id = ANY(%s)", (ids,))
            cur = await conn.execute("UPDATE patient_devices SET revoked_at = now() WHERE patient_id = %s "
                                     "AND revoked_at IS NULL", (patient_id,))
            counts["devices_revoked"] = cur.rowcount
            await conn.execute("UPDATE patients SET height_cm = NULL, mass_kg = NULL, emergency_contact_name = NULL, "
                               "emergency_contact_phone = NULL, emergency_contact_relationship = NULL, "
                               "device_prefs = '{}' WHERE id = %s", (patient_id,))
            return counts

    return await _safe(db, "purge_patient_data", run, None, patient_id=patient_id)
```
(The table names come from fixed tuples in this function, never from input.) Check the `notifications` table has a `patient_id` column (it does: `NOTIFICATION_INSERT_SQL`) and `agent_actions`/`plan_proposals` too (schema lines 118, 151).

- [ ] **Step 4: Routes**

Append to `server/api/me.py`:
```python
from fastapi import Depends, Query

from server import security
from server.api.db_dep import require_db
from server.api.identity_models import MeUpdate
from server.db import writes


def _patient_for(principal: security.Principal, patient_id: str | None) -> str:
    """Patients act as themselves; the admin token must name a patient with ?patient_id=."""
    if principal.scope == "patient":
        assert principal.patient_id is not None
        return principal.patient_id
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required with the API token")
    return patient_id


@router.get("/me")
async def get_me(principal: security.Principal = Depends(security.require_principal),
                 patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    return await build_me(db, _patient_for(principal, patient_id))


@router.put("/me")
async def put_me(body: MeUpdate, principal: security.Principal = Depends(security.require_principal),
                 patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = _patient_for(principal, patient_id)
    p, c = body.profile, body.emergency_contact
    ok = await writes.update_patient_profile(
        db, pid, name=p.name if p else None, height_cm=p.height_cm if p else None, mass_kg=p.mass_kg if p else None,
        affected_side=p.affected_side if p else None, condition=p.condition if p else None,
        emergency_contact_name=c.name if c else None, emergency_contact_phone=c.phone if c else None,
        emergency_contact_relationship=c.relationship if c else None, alert_therapist=body.alert_therapist,
        share_by_default=body.share_by_default, device_prefs=body.device_prefs)
    if not ok:
        raise HTTPException(status_code=503, detail="database unavailable: could not save")
    return await build_me(db, pid)


@router.get("/me/export")
async def export_me(principal: security.Principal = Depends(security.require_principal),
                    patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = _patient_for(principal, patient_id)
    me = await build_me(db, pid)
    return {"exported_at": datetime.now(UTC), **me, **await queries.patient_export(db, pid)}


@router.delete("/me")
async def delete_me(principal: security.Principal = Depends(security.require_principal),
                    patient_id: str | None = Query(default=None), db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = _patient_for(principal, patient_id)
    if await queries.patient_me_row(db, pid) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    state = get_state()
    current = state.sessions.current
    if current is not None and current.patient_id == pid:
        await state.sessions.stop(current.session_id)
    counts = await writes.purge_patient_data(db, pid)
    if counts is None:
        raise HTTPException(status_code=503, detail="database unavailable: could not delete")
    security.invalidate_token_cache()
    return {"deleted": True, "sessions_deleted": counts.get("sessions", 0), "counts": counts}
```
Add `from datetime import UTC, datetime` to the module imports. Register in `main.py`: `app.include_router(me_api.router)` (routes carry their own `require_principal`).

Caveat for the mock: when `DELETE /me` stops Maya's live mock session and `MIRA_SOURCE=mock`, nothing restarts it in this plan — that is Plan 2's auto-restart. Note it in the route docstring.

- [ ] **Step 5: Relax the all-routes test**

`server/tests/test_security.py` `test_every_rest_route_requires_token_except_public`:
```python
from server.security import MAX_WEBHOOK_BYTES, require_principal, require_token
...
        calls = {dep.call for dep in route.dependant.dependencies}
        assert calls & {require_token, require_principal}, f"{route.path} is missing require_token/require_principal"
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest server/tests/test_me.py server/tests/test_pairing.py server/tests/test_security.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/api/me.py server/db/queries.py server/db/writes.py server/main.py server/tests/test_me.py server/tests/test_security.py
git commit -m "Add /me: the patient's profile, care team, plan; PUT, export and delete

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Patient scope on sessions, patients and prescriptions

**Files:**
- Modify: `server/api/sessions.py`, `server/api/patients.py`, `server/api/prescriptions.py`, `server/main.py:338-339`
- Test: `server/tests/test_patient_scope.py`

**Interfaces:**
- Consumes: `security.require_principal`, `Principal.require/owns`, `queries.prescriptions`.
- Behaviour: routers `sessions`, `patients`, `prescriptions` are registered with `Depends(require_principal)`. Inside: full-only routes (`GET /patients`, `PUT /prescriptions/{id}`) call `_full_only(principal)` (403 for patients). Owner routes call `principal.require(patient_id)`. `POST /sessions` with a patient principal forces `patient_id` to the principal's and validates `exercise_id` against active prescriptions (422 otherwise; skipped when there is no DB). Other routers (`calibration`, `alerts`, `notifications`, `insight`, `copilot`, `proposals`, `health/stats`, `demo`) stay full-only.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_patient_scope.py`:
```python
"""Patient tokens on existing routes: own data OK, others 403, full-only routes 403, admin unchanged."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('bob', 'Bob Stone', 'okafor') "
                         "ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                         "VALUES ('11111111-1111-1111-1111-111111111111', 'bob', 'squat', now() - interval '1 hour', now())")
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_patient_reads_own_data_only(client: TestClient, phone: dict[str, str]) -> None:
    assert client.get("/patients/maya", headers=phone).status_code == 200
    assert client.get("/patients/maya/progress", headers=phone).status_code == 200
    assert client.get("/prescriptions/maya", headers=phone).status_code == 200
    assert client.get("/patients/bob", headers=phone).status_code == 403
    assert client.get("/patients/bob/progress", headers=phone).status_code == 403
    assert client.get("/prescriptions/bob", headers=phone).status_code == 403
    assert client.get("/sessions/11111111-1111-1111-1111-111111111111", headers=phone).status_code == 403
    assert client.get("/patients", headers=phone).status_code == 403  # caseload is the therapist's
    assert client.put("/prescriptions/maya", headers=phone,
                      json={"exercise_id": "squat", "reps": 12, "created_by": "maya"}).status_code == 403
    assert client.get("/alerts", headers=phone).status_code == 401  # full-only router, unchanged


def test_patient_starts_stops_reads_own_session(client: TestClient, phone: dict[str, str]) -> None:
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=phone)
    assert r.status_code == 200 and r.json()["patient_id"] == "maya"  # forced to the caller
    sid = r.json()["session_id"]
    assert client.post("/sessions", json={"exercise_id": "lunge"}, headers=phone).status_code == 422
    assert client.get(f"/sessions/{sid}", headers=phone).status_code == 200
    assert client.get(f"/sessions/{sid}/frames?downsample=10", headers=phone).status_code == 200
    assert client.post(f"/sessions/{sid}/share", headers=phone).json()["shared"] is True
    assert client.post(f"/sessions/{sid}/stop", headers=phone).json()["stopped"] is True
    assert client.post("/sessions/11111111-1111-1111-1111-111111111111/stop", headers=phone).status_code == 403
    assert client.post(f"/sessions/{uuid.uuid4()}/stop", headers=phone).status_code == 404


def test_admin_unchanged(client: TestClient) -> None:
    assert client.get("/patients", headers=AUTH).status_code == 200
    assert client.get("/patients/bob", headers=AUTH).status_code == 200
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "anything"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["patient_id"] == "bob"  # admin is not validated against the plan
    client.post(f"/sessions/{r.json()['session_id']}/stop", headers=AUTH)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest server/tests/test_patient_scope.py -q`
Expected: FAIL — 401s where 200/403 expected.

- [ ] **Step 3: Implement**

`server/main.py:338-339`:
```python
    for module in (sessions_api, patients, prescriptions):  # patients may call these with their own token
        app.include_router(module.router, dependencies=[Depends(require_principal)])
    for module in (calibration, alerts, notifications_api, insight_api):
        app.include_router(module.router, dependencies=[Depends(require_token)])
```
Import `require_principal`.

Add to `server/security.py`:
```python
def full_only(principal: Principal) -> None:
    if principal.scope != "full":
        raise HTTPException(status_code=403, detail="forbidden")
```

`server/api/patients.py`:
```python
from server import security
...
@router.get("/patients")
async def list_patients(principal: security.Principal = Depends(security.require_principal),
                        db: Database = Depends(require_db)) -> dict[str, Any]:
    security.full_only(principal)
    return {"patients": await queries.list_patients(db)}


@router.get("/patients/{patient_id}")
async def get_patient(patient_id: str, principal: security.Principal = Depends(security.require_principal),
                      db: Database = Depends(require_db)) -> dict[str, Any]:
    principal.require(patient_id)
    ...  # body unchanged


@router.get("/patients/{patient_id}/progress")
async def get_progress(patient_id: str, exercise_id: str | None = None,
                       days: int = Query(default=90, ge=1, le=365),
                       principal: security.Principal = Depends(security.require_principal),
                       db: Database = Depends(require_db)) -> dict[str, Any]:
    principal.require(patient_id)
    ...  # body unchanged
```

`server/api/prescriptions.py`: `GET /prescriptions/{patient_id}` adds `principal: security.Principal = Depends(security.require_principal)` + `principal.require(patient_id)`; `PUT /prescriptions/{patient_id}` adds the principal + `security.full_only(principal)`.

`server/api/sessions.py`:
```python
from server import security
...
async def _owned_session(db: Database, sid: str, principal: security.Principal) -> dict[str, Any]:
    session = await queries.get_session(db, sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    principal.require(session["patient_id"])
    return session


@router.post("/sessions")
async def start_session(body: SessionIn | None = None,
                        principal: security.Principal = Depends(security.require_principal)) -> dict[str, Any]:
    body = body or SessionIn()
    state = get_state()
    if principal.scope == "patient":
        assert principal.patient_id is not None
        body.patient_id = principal.patient_id
        if state.db is not None:  # a phone may only start exercises on its plan
            active = {rx["exercise_id"] for rx in await queries.prescriptions(state.db, body.patient_id)}
            if body.exercise_id not in active:
                raise HTTPException(status_code=422, detail=f"{body.exercise_id!r} is not in your plan")
    registry = state.sessions
    if registry.current is not None:
        await registry.stop(registry.current.session_id)
    session = await registry.start(body.patient_id, body.exercise_id)
    return {...}  # unchanged


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: uuid.UUID,
                       principal: security.Principal = Depends(security.require_principal)) -> dict[str, Any]:
    sid = str(session_id)
    state = get_state()
    live = state.sessions.get(sid)
    if live is not None:
        principal.require(live.patient_id)
        await state.sessions.stop(sid)
        return {"session_id": sid, "stopped": True}
    db = require_db()
    await _owned_session(db, sid, principal)
    if not await writes.end_session(db, sid, None):
        raise HTTPException(status_code=503, detail="database unavailable: could not stop the session")
    return {"session_id": sid, "stopped": True}
```
`GET /sessions/{id}`: replace the `get_session` + 404 lines with `session = await _owned_session(db, sid, principal)` (add the principal dependency). `GET /sessions/{id}/frames` and `POST /sessions/{id}/share`: add the principal dependency and call `await _owned_session(db, sid, principal)` first (`share` replaces its own 404 check with it).

Note: `queries.get_session` of a live-but-not-yet-persisted session can be None for a patient on `/frames`; that's the existing behaviour (404) and stays.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest server/tests/test_patient_scope.py server/tests/test_api_db.py server/tests/test_security.py -q`
Expected: PASS (existing admin tests untouched).

- [ ] **Step 5: Commit**

```bash
git add server/api/sessions.py server/api/patients.py server/api/prescriptions.py server/security.py server/main.py server/tests/test_patient_scope.py
git commit -m "Let patient tokens use their own sessions, progress and plan

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Patient WebSockets — own session only

**Files:**
- Modify: `server/main.py` (`_serve_patient_ws`, new handler), `server/devices.py` (nothing) 
- Test: `server/tests/test_ws_patient.py`

**Interfaces:**
- Produces: `PATIENT_CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button"})`; `_patient_accepts(patient_id) -> Callable[[dict], bool]` (True iff `sessions.current.patient_id == patient_id`); `_patient_event_handler(patient_id)` (rate-limited; drops `device_health`; drops everything while the current session isn't the patient's).

- [ ] **Step 1: Write the failing tests**

`server/tests/test_ws_patient.py`:
```python
"""A patient's sockets only carry their own session; they may send commands, not device_health."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from server import security
from server.state import get_state
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        import psycopg

        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('bob', 'Bob Stone', 'okafor') "
                         "ON CONFLICT DO NOTHING")
        yield url


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    return r.json()["token"]


def _drain(ws, seconds: float = 1.5) -> list[dict]:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            out.append(ws.receive_json())
        except Exception:  # noqa: BLE001
            break
    return out


def test_patient_receives_own_session_streams(client: TestClient, token: str) -> None:
    assert get_state().sessions.current.patient_id == "maya"
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        msg = ws.receive_json()
        assert "phase" in msg
    with client.websocket_connect(f"/ws/pose?token={token}") as ws:
        assert "joints" in ws.receive_json()


def test_patient_is_silenced_during_another_patients_session(client: TestClient, token: str) -> None:
    r = client.post("/sessions", json={"patient_id": "bob", "exercise_id": "squat"}, headers=AUTH)
    assert r.status_code == 200
    try:
        with client.websocket_connect(f"/ws/live?token={token}") as ws:
            msgs = _drain(ws, 1.0)
            assert all(m.get("type") == "ping" for m in msgs), msgs  # nothing of Bob's leaks
            # a command from Maya's phone during Bob's session is ignored
            ws.send_json({"type": "command", "name": "pause"})
    finally:
        client.post("/sessions", json={"patient_id": "maya", "exercise_id": "squat"}, headers=AUTH)


def test_patient_commands_and_forbidden_messages(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from server import main

    seen: list[dict] = []
    monkeypatch.setattr(main, "_on_client_event", lambda msg: seen.append(msg))
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        ws.send_json({"type": "command", "name": "pause"})
        ws.send_json({"type": "hr", "t": 1.0, "bpm": 90})
        ws.send_json({"type": "device_health", "app": "lcd"})
        ws.send_json({"type": "fall", "stage": "confirmed"})
        _drain(ws, 0.5)
    assert [m["type"] for m in seen] == ["command", "hr"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest server/tests/test_ws_patient.py -q`
Expected: FAIL — Bob's session leaks (placeholder from Task 3) and `device_health` gets through.

- [ ] **Step 3: Implement**

In `server/main.py` add next to `CLIENT_EVENT_TYPES`:
```python
# A patient's phone may steer the session and relay its watch; it is never the LCD.
PATIENT_CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button"})
```
Replace the Task 3 placeholder `_serve_patient_ws`:
```python
def _patient_accepts(patient_id: str) -> Callable[[dict[str, Any]], bool]:
    """Per-client hub filter: a phone sees frames and events only while the live session is its patient's."""
    def accept(_msg: dict[str, Any]) -> bool:
        current = get_state().sessions.current
        return current is not None and current.patient_id == patient_id

    return accept


def _patient_event_handler(patient_id: str) -> Callable[[dict[str, Any]], None]:
    """Like _client_event_handler, minus device_health, and only while the session is the patient's own."""
    limiter = RateLimiter(CLIENT_MSGS_PER_S)
    owns = _patient_accepts(patient_id)

    def handle(msg: dict[str, Any]) -> None:
        if not limiter.allow() or msg.get("type") not in PATIENT_CLIENT_EVENT_TYPES or not owns(msg):
            return
        _on_client_event(msg)

    return handle


async def _serve_patient_ws(ws: WebSocket, channel: str, principal: Principal) -> None:
    assert principal.patient_id is not None
    hub = get_state().hub
    handler = _patient_event_handler(principal.patient_id) if channel == "events" else None
    await hub.connect(channel, ws, handler, accept=_patient_accepts(principal.patient_id))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest server/tests/test_ws_patient.py server/tests/test_ws_device.py server/tests/test_hub.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/main.py server/tests/test_ws_patient.py
git commit -m "Patient sockets: own session only, commands allowed, no device_health

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Contract samples for `/pair` and `/me`

**Files:**
- Create: `server/samples/rest_post_pair.json`, `server/samples/rest_get_me.json`
- Modify: `server/tests/test_samples.py`, `server/samples/README.md`
- Copy to: `device/ios/Tests/Fixtures/rest_post_pair.json`, `device/ios/Tests/Fixtures/rest_get_me.json`

- [ ] **Step 1: Read `server/tests/test_samples.py`** to see how samples are checked (there is a registry of file → required keys). Add entries for the two new files following the same pattern, e.g.:
```python
    "rest_get_me.json": {"patient", "therapist", "prescriptions", "lcd", "calibration"},
    "rest_post_pair.json": {"patient_id", "token", "me"},
```

- [ ] **Step 2: Generate the samples from a running test server** — run this script once (it uses the DB test helpers):
```bash
.venv/bin/python - <<'EOF'
import json, os
os.environ["MIRA_SKIP_DOTENV"] = "1"
from server.tests.conftest import BASELINE_ENV
for k, v in BASELINE_ENV.items(): os.environ[k] = v
from server.config import reload_config
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from fastapi.testclient import TestClient
with fresh_database() as dsn:
    prepare(dsn)
    os.environ.update(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn)); reload_config()
    from server.main import create_app
    with TestClient(create_app()) as c:
        body = {"code": "K7P4M2", "device_name": "Maya's iPhone",
                "profile": {"name": "Maya Ellis", "height_cm": 163, "mass_kg": 70, "affected_side": "right", "condition": "Knee replacement"},
                "emergency_contact": {"name": "Eric", "phone": "+15550100111", "relationship": "son"}, "alert_therapist": True}
        pair = c.post("/pair", json=body).json()
        pair["token"] = "pt_EXAMPLE-TOKEN-REDACTED-xxxxxxxxxxxxxxxxxxxxxxxx"
        json.dump(pair, open("server/samples/rest_post_pair.json", "w"), indent=2, default=str); print(file=open("server/samples/rest_post_pair.json","a"))
        me = c.get("/me?patient_id=maya", headers={"Authorization": f"Bearer {os.environ['MIRA_API_TOKEN']}"}).json()
        json.dump(me, open("server/samples/rest_get_me.json", "w"), indent=2, default=str); print(file=open("server/samples/rest_get_me.json","a"))
EOF
cp server/samples/rest_post_pair.json server/samples/rest_get_me.json device/ios/Tests/Fixtures/
```
Check the files look like the other samples (2-space indent, trailing newline), and that `rest_get_me.json` has `"next_visit_at"` set and `"lcd": {"online": false, ...}`.

- [ ] **Step 3: README** — add two lines to `server/samples/README.md` describing `rest_post_pair` (token redacted) and `rest_get_me`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest server/tests/test_samples.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add server/samples device/ios/Tests/Fixtures/rest_post_pair.json device/ios/Tests/Fixtures/rest_get_me.json server/tests/test_samples.py
git commit -m "Samples: rest_post_pair and rest_get_me (and iOS fixture copies)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: iOS models, APIClient and AppState pairing

**Files:**
- Modify: `device/ios/Mira/Net/Models.swift` (append), `device/ios/Mira/Net/APIClient.swift`, `device/ios/Mira/App/AppState.swift`
- Test: `device/ios/Tests/PairingTests.swift` (new), `device/ios/Tests/ContractFixtureTests.swift`

**Interfaces:**
- Produces (Models): `PairProfile`, `PairContact`, `PairRequest`, `PairResponse`, `MePatient`, `MeTherapist`, `MePrescription`, `LcdStatus`, `MeResponse`, `MeProfilePatch`, `MeUpdate` (all `Codable, Sendable`, snake_case via `WireJSON`).
- Produces (APIClient): `func pair(_ body: PairRequest) async throws -> PairResponse` (unauthenticated), `func me() async throws -> MeResponse`, `func updateMe(_ body: MeUpdate) async throws -> MeResponse`, `func exportMe() async throws -> Data`, `func deleteMe() async throws`.
- Produces (AppState): `private(set) var patientID: String?` (UserDefaults `patientID.v1`), `private(set) var me: MeResponse?`, `private(set) var pairingError: String?`, `private(set) var isPairing`, `func pair(deviceName: String = "iPhone") async -> Bool`, `func refreshMe() async`, `var isLinked: Bool { patientID != nil && isPaired }`; `forgetToken()` also clears `patientID`/`me`; `init(defaults:tokenStore:urlSession:)` gains `urlSession: URLSession = .shared` used by `apiClient`.
- `APIError.http(code, message)` is parsed into a friendlier `pairingError` via `PairingError.message(for:)`: 404 → "That code isn't recognised. Check it with your therapist.", 410 → "That code has expired or was already used. Ask your therapist for a new one.", 429 → "Too many attempts. Wait a minute and try again.", 503/notConnected → "Mira's server is offline right now. You can save your code and try again later."

- [ ] **Step 1: Write the failing tests**

`device/ios/Tests/PairingTests.swift`:
```swift
import XCTest
@testable import MiraNetworking

@MainActor
final class PairingTests: XCTestCase {
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "PairingTests-\(UUID().uuidString)")
        StubProtocol.requests = []
    }

    private func draftState(tokenStore: MemoryTokenStore = MemoryTokenStore()) -> AppState {
        let state = AppState(defaults: defaults, tokenStore: tokenStore, urlSession: StubProtocol.session())
        state.onboarding.patientName = "Maya Ellis"
        state.onboarding.heightCM = "163"
        state.onboarding.bodyMassKG = "70"
        state.onboarding.affectedSide = .right
        state.onboarding.condition = "Knee replacement"
        state.onboarding.emergencyContactName = "Eric"
        state.onboarding.emergencyContactPhone = "+15550100111"
        state.onboarding.therapistCode = "K7P4M2"
        state.preferences.contactRelationship = "son"
        _ = state.connect(address: "http://mira.test:8000")
        return state
    }

    private static let meJSON = #"""
    {"patient":{"id":"maya","name":"Maya Ellis","first_name":"Maya","age":67,"height_cm":163,"mass_kg":70,
     "affected_side":"right","condition":"Knee replacement","hr_limit":130,
     "emergency_contact":{"name":"Eric","phone":"+15550100111","relationship":"son"},
     "alert_therapist":true,"share_by_default":false,"device_prefs":{},"next_visit_at":"2026-10-13T15:30:00Z"},
     "therapist":{"id":"okafor","name":"Dr. Okafor","clinic":"Lakeside Rehab","phone":"+15550100222"},
     "prescriptions":[{"exercise_id":"squat","exercise_name":"Mini squats","sets":3,"reps":10,
       "targets":{"knee_deg":90},"limits":{},"version":1,"metric_label":"Knee bend","unit":"°","target_key":"knee_deg"}],
     "lcd":{"online":false,"last_seen":null},
     "calibration":{"status":"unknown","calibrated":false,"cameras":[],"errors":null,"note":"x"}}
    """#

    func testPairStoresTokenPatientAndMe() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/pair")
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            let body = try! JSONSerialization.jsonObject(with: request.httpBody ?? request.streamedBody()) as! [String: Any]
            XCTAssertEqual(body["code"] as? String, "K7P4M2")
            XCTAssertEqual((body["profile"] as? [String: Any])?["affected_side"] as? String, "right")
            XCTAssertEqual((body["emergency_contact"] as? [String: Any])?["relationship"] as? String, "son")
            return (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#)
        }
        let store = MemoryTokenStore()
        let state = draftState(tokenStore: store)
        let ok = await state.pair()
        XCTAssertTrue(ok)
        XCTAssertEqual(state.patientID, "maya")
        XCTAssertEqual(store.load(), "pt_abc123")
        XCTAssertEqual(state.me?.therapist?.clinic, "Lakeside Rehab")
        XCTAssertEqual(state.me?.prescriptions.first?.exerciseName, "Mini squats")
        XCTAssertTrue(state.isLinked)
        XCTAssertNil(state.pairingError)
        // persisted
        XCTAssertEqual(defaults.string(forKey: "patientID.v1"), "maya")
    }

    func testUnknownCodeShowsFriendlyErrorAndStaysUnpaired() async {
        StubProtocol.handler = { _ in (404, #"{"detail":"unknown code"}"#) }
        let state = draftState()
        let ok = await state.pair()
        XCTAssertFalse(ok)
        XCTAssertNil(state.patientID)
        XCTAssertFalse(state.isPaired)
        XCTAssertEqual(state.pairingError, "That code isn't recognised. Check it with your therapist.")
    }

    func testExpiredCodeMessage() async {
        StubProtocol.handler = { _ in (410, #"{"detail":"code expired"}"#) }
        let state = draftState()
        _ = await state.pair()
        XCTAssertEqual(state.pairingError, "That code has expired or was already used. Ask your therapist for a new one.")
    }

    func testForgetTokenClearsIdentity() async {
        StubProtocol.handler = { _ in (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#) }
        let state = draftState()
        _ = await state.pair()
        state.forgetToken()
        XCTAssertNil(state.patientID)
        XCTAssertNil(state.me)
        XCTAssertFalse(state.isLinked)
    }

    func testRefreshMeUsesPatientToken() async throws {
        StubProtocol.handler = { request in
            if request.url?.path == "/pair" { return (200, #"{"patient_id":"maya","token":"pt_abc123","me":\#(Self.meJSON)}"#) }
            XCTAssertEqual(request.url?.path, "/me")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer pt_abc123")
            return (200, Self.meJSON)
        }
        let state = draftState()
        _ = await state.pair()
        await state.refreshMe()
        XCTAssertEqual(state.me?.patient.firstName, "Maya")
    }
}

extension URLRequest {
    /// URLProtocol receives bodies as a stream; read it back for assertions.
    func streamedBody() -> Data {
        guard let stream = httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable { let n = stream.read(&buffer, maxLength: buffer.count); if n <= 0 { break }; data.append(buffer, count: n) }
        return data
    }
}
```

Add to `ContractFixtureTests.testRestResponses`:
```swift
        let paired = try decode(PairResponse.self, "rest_post_pair")
        XCTAssertEqual(paired.patientId, "maya")
        XCTAssertTrue(paired.token.hasPrefix("pt_"))
        let me = try decode(MeResponse.self, "rest_get_me")
        XCTAssertEqual(me.therapist?.name, "Dr. Okafor")
        XCTAssertEqual(me.prescriptions.first?.exerciseName.isEmpty, false)
        XCTAssertNotNil(me.lcd)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd device/ios && swift test --filter PairingTests 2>&1 | tail -20`
Expected: compile errors (`PairRequest` undefined, `AppState.pair` missing).

- [ ] **Step 3: Models**

Append to `device/ios/Mira/Net/Models.swift`:
```swift
// MARK: - Pairing and /me (Plan 1; server/samples/rest_post_pair.json, rest_get_me.json)

struct PairProfile: Codable, Sendable {
    let name: String
    let heightCm: Double
    let massKg: Double
    var affectedSide: String?   // "left" | "right" | "none"
    var condition: String?
}
struct PairContact: Codable, Sendable, Equatable {
    let name: String
    let phone: String
    var relationship: String?
}
struct PairRequest: Codable, Sendable {
    let code: String
    var deviceName: String?
    let profile: PairProfile
    var emergencyContact: PairContact?
    var alertTherapist: Bool = true
}
struct PairResponse: Codable, Sendable {
    let patientId: String
    let token: String
    let me: MeResponse
}

struct MePatient: Codable, Sendable {
    let id: String
    let name: String
    let firstName: String
    let age: Int?
    let heightCm: Double?, massKg: Double?
    let affectedSide: String?, condition: String?
    let hrLimit: Int?
    let emergencyContact: PairContact?
    let alertTherapist: Bool
    let shareByDefault: Bool
    let devicePrefs: JSONValue?
    let nextVisitAt: String?
}
struct MeTherapist: Codable, Sendable { let id: String; let name: String; let clinic: String?; let phone: String? }
struct MePrescription: Codable, Sendable, Identifiable {
    var id: String { exerciseId }
    let exerciseId: String
    let exerciseName: String
    let sets: Int?
    let reps: Int?
    let targets: JSONValue?
    let limits: JSONValue?
    let version: Int?
    let metricLabel: String?, unit: String?, targetKey: String?
}
struct LcdStatus: Codable, Sendable { let online: Bool; let lastSeen: Double? }
struct MeResponse: Codable, Sendable {
    let patient: MePatient
    let therapist: MeTherapist?
    let prescriptions: [MePrescription]
    let lcd: LcdStatus?
    let calibration: CalibrationStatus?
}

struct MeProfilePatch: Codable, Sendable {
    var name: String?
    var heightCm: Double?, massKg: Double?
    var affectedSide: String?, condition: String?
}
struct MeUpdate: Codable, Sendable {
    var profile: MeProfilePatch?
    var emergencyContact: PairContact?
    var alertTherapist: Bool?
    var shareByDefault: Bool?
    var devicePrefs: JSONValue?
}
```

- [ ] **Step 4: APIClient**

In `device/ios/Mira/Net/APIClient.swift`, inside `APIClient` after `notificationCount()`:
```swift
    // MARK: Pairing and /me (Plan 1)

    /// Public: a therapist code plus the onboarding profile → a patient token. Never retried.
    func pair(_ body: PairRequest) async throws -> PairResponse {
        let data = try await perform("POST", path: ["pair"], body: json(body), authenticated: false)
        return try WireJSON.decoder().decode(PairResponse.self, from: data)
    }
    func me() async throws -> MeResponse { try await request(path: ["me"]) }
    func updateMe(_ body: MeUpdate) async throws -> MeResponse { try await request("PUT", path: ["me"], body: json(body)) }
    func exportMe() async throws -> Data { try await perform("GET", path: ["me", "export"]) }
    func deleteMe() async throws { _ = try await perform("DELETE", path: ["me"]) }
```
Also add to `APIError`:
```swift
    case forbidden
```
with description `"This isn't your data to see."`, and in `perform` map `403` → `throw APIError.forbidden` right after the 401 check.

- [ ] **Step 5: AppState**

In `device/ios/Mira/App/AppState.swift`:
- Add stored properties after `tokenHint`:
```swift
    /// The server's patient id for this phone, set by a successful /pair. UserDefaults `patientID.v1`.
    private(set) var patientID: String? { didSet { defaults.set(patientID, forKey: "patientID.v1") } }
    private(set) var me: MeResponse?
    private(set) var pairingError: String?
    private(set) var isPairing = false
    @ObservationIgnored private let urlSession: URLSession

    var isLinked: Bool { patientID != nil && isPaired }
```
- `init(defaults: UserDefaults = .standard, tokenStore: TokenStore = KeychainTokenStore(), urlSession: URLSession = .shared)`; set `self.urlSession = urlSession` and `patientID = defaults.string(forKey: "patientID.v1")`. Note `patientID`'s `didSet` isn't called from `init`, which is what we want.
- `apiClient`: `APIClient(address: address, token: token, session: urlSession)`.
- New methods:
```swift
    /// Builds the /pair request from the onboarding draft and preferences.
    func pairRequest(deviceName: String) -> PairRequest? {
        let d = onboarding
        guard d.therapistCode.count == 6, let height = d.height, let mass = d.bodyMass,
              let name = d.patientName?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty else { return nil }
        let side: String? = switch d.affectedSide {
        case .left: "left"
        case .right: "right"
        case .notApplicable: "none"
        case .both, .notSelected: nil
        }
        let relationship = preferences.contactRelationship.trimmingCharacters(in: .whitespacesAndNewlines)
        let contact = d.contactIsValid ? PairContact(name: d.emergencyContactName, phone: d.emergencyContactPhone,
                                                     relationship: relationship.isEmpty ? nil : relationship) : nil
        return PairRequest(code: d.therapistCode, deviceName: deviceName,
                           profile: PairProfile(name: name, heightCm: height, massKg: mass, affectedSide: side,
                                                condition: d.condition.isEmpty ? nil : d.condition),
                           emergencyContact: contact, alertTherapist: preferences.alertTherapist)
    }

    /// POST /pair with the draft; on success the patient token replaces any saved token and sockets reconnect.
    @discardableResult
    func pair(deviceName: String = "iPhone") async -> Bool {
        guard !isPairing, let body = pairRequest(deviceName: deviceName) else {
            pairingError = "Enter your name, height, weight and the 6-character code first."
            return false
        }
        guard let address = try? ServerAddress(serverAddress) else {
            pairingError = APIError.invalidAddress.localizedDescription
            return false
        }
        isPairing = true
        pairingError = nil
        defer { isPairing = false }
        do {
            let response = try await APIClient(address: address, token: nil, session: urlSession).pair(body)
            try tokenStore.save(response.token)
            token = response.token
            tokenHint = PairingToken.masked(response.token)
            patientID = response.patientId
            me = response.me
            configurationVersion = UUID()
            configurationError = nil
            connectionEnabled = true
            defaults.set(true, forKey: "connectionEnabled")
            if foreground { liveSession.connect(to: address, token: token, reset: true) }
            return true
        } catch {
            pairingError = PairingError.message(for: error)
            return false
        }
    }

    func refreshMe() async {
        guard patientID != nil, let apiClient else { return }
        do { me = try await apiClient.me() } catch APIError.unauthorized {
            configurationError = APIError.unauthorized.localizedDescription
        } catch { /* keep the cached `me`; Home shows the offline banner from the socket state */ }
    }
```
- `forgetToken()`: add `patientID = nil; me = nil; pairingError = nil`.
- `setForeground(_:)`: when `active` and `patientID != nil`, `Task { await refreshMe() }`.
- Add at the bottom of the file:
```swift
enum PairingError {
    static func message(for error: Error) -> String {
        switch error {
        case APIError.http(404, _): return "That code isn't recognised. Check it with your therapist."
        case APIError.http(410, _): return "That code has expired or was already used. Ask your therapist for a new one."
        case APIError.http(429, _): return "Too many attempts. Wait a minute and try again."
        case APIError.http(422, _): return "Mira couldn't read your details. Check your name, height and weight."
        case APIError.http(503, _), APIError.notConnected: return "Mira's server is offline right now. You can save your code and try again later."
        case let api as APIError: return api.localizedDescription
        default: return "Mira's server is offline right now. You can save your code and try again later."
        }
    }
}
```

- [ ] **Step 6: Run the Swift tests**

Run: `cd device/ios && swift test 2>&1 | tail -30`
Expected: all green, including `PairingTests` (5) and the contract fixture test with the new samples.

- [ ] **Step 7: Commit**

```bash
git add device/ios/Mira/Net/Models.swift device/ios/Mira/Net/APIClient.swift device/ios/Mira/App/AppState.swift device/ios/Tests/PairingTests.swift device/ios/Tests/ContractFixtureTests.swift
git commit -m "iOS: pairing models, /pair and /me calls, AppState.pair()

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: iOS onboarding pairs; device step shows the LCD; demo-server preset

**Files:**
- Modify: `device/ios/Mira/Features/Onboarding/OnboardingView.swift`, `OnboardingDesignedSteps.swift`, `device/ios/Mira/Features/Settings/SettingsView.swift` (NetworkDiagnosticsView), `device/ios/Mira/App/AppState.swift` (default address)
- Verify: `xcodebuild`

**Interfaces:**
- Consumes: `state.pair()`, `state.me`, `state.isLinked`, `state.pairingError`, `state.isPairing`, `state.refreshMe()`.
- Produces: `AppState.demoServerAddress = "http://45.76.255.253:8000"`; default `serverAddress` is `demoServerAddress` when compiled with `-DMIRA_DEMO`, else `http://localhost:8000`.

- [ ] **Step 1: OnboardingView — Continue on the therapist step pairs**

In `OnboardingView`:
```swift
    private var canPairNow: Bool { state.onboarding.therapistCode.count == 6 && !state.isLinked }

    private func continueTapped() {
        if returningToReview { returningToReview = false; state.onboarding.step = .review; return }
        if state.onboarding.step == .therapist && canPairNow {
            Task {
                if await state.pair() { state.advanceOnboarding() }
                // on failure the step shows state.pairingError and the button reads "Set up later"
            }
            return
        }
        state.advanceOnboarding()
    }
```
Use `continueTapped` as the `MiraButton` action; `.disabled(!state.onboarding.canContinue || state.isPairing)`. `continueTitle` for `.therapist`: `state.isLinked ? "Continue" : canPairNow && state.pairingError == nil ? "Link therapist" : "Set up later"`; for `.device`: `"Continue"` when `state.isLinked`, else the existing text.

- [ ] **Step 2: OnboardingDesignedSteps — therapist and device content from `me`**

In `therapist`: show the plan card from `state.me` when linked (replace the `preview ?` ternaries with a `linked = preview || state.me != nil` flag and real values):
- title `linked ? "Your plan is ready" : "Your plan will appear here"`, subtitle `state.me?.therapist.map { "From \($0.name)" + ($0.clinic.map { ", \($0)" } ?? "") } ?? (preview ? "From Dr. Sam Lee, PT" : "Enter your code to link your therapist.")`
- `planRow("imgIconExercises", "\(me.prescriptions.count) exercises", "About \(estimatedMinutes) minutes a day")` where `estimatedMinutes = max(5, prescriptions.reduce(0) { $0 + ($1.sets ?? 1) * (($1.reps ?? 10) * 4 + 30) } / 60)`.
- Goal row from the first prescription whose `targets` has `knee_deg` (`"Bend your knee to \(deg)°"`), else "Targets from your care plan".
- Below the card: `if let error = state.pairingError { Text(error).font(MiraUI.font(15)).foregroundStyle(MiraUI.alert) }` and, when not linked and no error, the existing "You can save your code for later…" copy reworded to "Tap Link therapist to load your plan, or save the code for later."
- While `state.isPairing` show a `ProgressView("Linking…")`.

In `device`: 
- `let online = state.me?.lcd?.online ?? false`
- Card title `state.isLinked ? (online ? "Mira is online" : "Mira's screen is offline") : "Mira setup pending"`, subtitle `state.isLinked ? (online ? "Paired through \(clinic)" : "Plug Mira in; this updates on its own.") : "Link your therapist first"`.
- Check icon when `online`. A small "Refresh" button calling `Task { await state.refreshMe() }` when linked.
- Keep the "finish now, connect later" line when not online.

In `OnboardingReviewView`: therapist card rows use `state.me` when present: Therapist `me.therapist?.name ?? "Not linked yet"`, Plan `"\(me.prescriptions.count) exercises"`, Goal as above. Mira card Status `me.lcd?.online == true ? "✓ Online" : state.isLinked ? "Paired · screen offline" : "Setup pending"`. Footer text when linked: "Your details are saved with your care team. You can change them any time in Settings."

Keep every `preview` branch working: `preview` still shows the Figma sample copy when `state.me == nil`.

- [ ] **Step 3: Demo-server preset**

`AppState`:
```swift
    static let demoServerAddress = "http://45.76.255.253:8000"
    private static var defaultServerAddress: String {
        #if MIRA_DEMO
        return demoServerAddress
        #else
        return "http://localhost:8000"
        #endif
    }
```
and `serverAddress = defaults.string(forKey: "serverAddress") ?? Self.defaultServerAddress`.

`NetworkDiagnosticsView` (SettingsView.swift ~185-220): add a `Button("Use demo server (Vultr)") { address = AppState.demoServerAddress }` next to the address field, and a line showing `state.patientID.map { "Patient: \($0)" } ?? "Not paired to a patient"`.

- [ ] **Step 4: Build**

Run: `cd device/ios && xcodebuild -project Mira.xcodeproj -scheme Mira -destination 'platform=iOS Simulator,name=iPhone 17' -configuration Debug build 2>&1 | grep -E "error|warning: unre|BUILD" | head -30`
Expected: `** BUILD SUCCEEDED **`, no errors. Then `swift test 2>&1 | tail -5` still green.

- [ ] **Step 5: Manual check (simulator, optional but recommended)**

Start the local server with the Docker DB: from repo root `MIRA_SOURCE=mock make run` (uses `.env`; confirm `TIGER_DSN` points to the local Docker DB or `DB_DISABLED` is unset). In the simulator: fresh install → onboarding → code `K7P4M2` → "Link therapist" → therapist card shows Dr. Okafor / 3 exercises → device step shows LCD status → review → Home. Settings → Developer tools (needs `--network-debug`) shows `Patient: maya`.

- [ ] **Step 6: Commit**

```bash
git add device/ios/Mira
git commit -m "iOS onboarding links the therapist via /pair and shows the real plan and LCD status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Docs

**Files:**
- Modify: `README.md`, `device/ios/NETWORKING.md`, `device/ios/ONBOARDING.md`, `.env.example`

- [ ] **Step 1: README** — in the API section add a "Patient identity (phone)" subsection: the three token scopes table from the spec §3, `POST /pair` request/response, `GET/PUT/DELETE /me`, `/me/export`, `POST /patients/{id}/pairing-code`, `DELETE /patients/{id}/devices`, which existing routes accept patient tokens (sessions own/progress/prescriptions GET), WS patient filtering, and the migration runner (`make db-migrate`, `DB_AUTO_MIGRATE`). Mention the demo code `K7P4M2` and `PAIRING_DEMO_CODE_REUSABLE`.
- [ ] **Step 2: NETWORKING.md** — replace the "enter the MIRA_API_TOKEN in Developer tools" guidance with: pairing in onboarding is the normal path; the admin token stays for diagnostics. Note the 403 → `APIError.forbidden`.
- [ ] **Step 3: ONBOARDING.md** — update "Backend work still needed": the therapist-code exchange, typed prescription response, profile/contact sync and pairing now exist (point at `/pair`, `/me`); remaining: calibration (vision), HR limit comes from `me.patient.hr_limit`.
- [ ] **Step 4: .env.example** — add `PAIRING_DEMO_CODE=K7P4M2`, `PAIRING_DEMO_CODE_REUSABLE=1` with one-line comments.
- [ ] **Step 5: Commit**

```bash
git add README.md device/ios/NETWORKING.md device/ios/ONBOARDING.md .env.example
git commit -m "Docs: patient identity, pairing and migrations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Plan-level verification (run once, at the end)

- [ ] `make test` from the repo root → all green (DB tests run against Docker on :55432; none skipped unexpectedly — check the summary line for `skipped`).
- [ ] `cd device/ios && swift test` → green.
- [ ] `cd device/ios && xcodebuild -project Mira.xcodeproj -scheme Mira -destination 'platform=iOS Simulator,name=iPhone 17' build` → BUILD SUCCEEDED.
- [ ] `node --test device/kiosk/tests/*.test.mjs` → green (nothing in this plan touches the kiosk; confirms no regression from the `exercise_name` change).
- [ ] Smoke against the local server: `curl -s -X POST localhost:8000/pair -H 'content-type: application/json' -d '{"code":"K7P4M2","profile":{"name":"Maya Ellis","height_cm":163,"mass_kg":70}}' | head -c 300` → a `pt_` token.
- [ ] Do **not** deploy yet; deployment happens after Plan 2 (the mock auto-restart) so `DELETE /me` can't leave the LCD demo idle.

---

## Self-review notes

- Spec §4.1 ✔ (Task 1), §4.2 routes ✔ (Tasks 4-5; `POST /patients/{id}/pairing-code` and `DELETE /patients/{id}/devices` in Task 4), §4.3 ✔ (Tasks 3, 6, 7), §4.4 ✔ (Tasks 9-10), §8 error handling: 401 vs revoked — the app only clears identity on `forgetToken()`/`DELETE /me`; a transient 401 sets `configurationError` (Task 9 `refreshMe`). §9 tests ✔ (`test_security_principal`, `test_pairing`, `test_me`, `test_patient_scope`, `test_ws_patient`; the spec's `test_session_end`/`test_session_analyst` belong to Plan 2).
- `DELETE /me` keeps simulated history (added decision; recorded in the route docstring and the spec should note it — update spec §4.2 when this plan lands: "simulated demo sessions are kept").
- Type consistency: `Principal.require` (raises) vs `owns` (bool); `build_me` lives in `api/me.py` and is imported by `api/pair.py`; the request models live in `api/identity_models.py` so there is no import cycle.
