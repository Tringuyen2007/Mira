# Apple Watch — Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add everything the Apple Watch app needs on the server (branch `tri-watch`): watch tables, watch REST routes, new
`/ws/events` client/server messages, the breather (HR pause + auto-resume), heart-rate recovery, rotation exercises
counted from the watch, clock sync, and AI context — without a new server or a second process.

**Architecture:** The watch never talks to the server; the iPhone relays it over the existing `/ws/events` socket and
REST. New behaviour lives in small, single-purpose agents (`server/agents/recovery.py`, `rotation.py`, `clock_sync.py`)
subscribed to `EngineBridge` like the existing ones, plus additions to the Safety Guardian and HapticCoach. New tables
come from an idempotent migration (`007_watch.sql`) mirrored in `schema.sql`; every write goes through `server/db/writes.py`,
every read through `server/db/queries.py`.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 (async pool + COPY writer thread), TimescaleDB, numpy (already pinned:
`numpy==2.5.3` in `server/requirements.txt`), pytest + pytest-asyncio (`asyncio_mode = auto`).

**Sources of truth:** spec `docs/superpowers/specs/2026-10-11-apple-watch-design.md` (Server section) and the wire contract
`docs/superpowers/plans/2026-10-11-watch-contract.md`. **Where they differ, the contract wins** (e.g. no `caption` event; the
`/me/watch` prefs keys are `watch_taps/watch_arm/watch_double_tap/watch_captions`).

## Global Constraints

- Single uvicorn process (`--workers 1`); all live state stays in memory in that one process. No new process, thread pool or server.
- Migrations are idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, role-guarded `DO` blocks) and `schema.sql` stays in sync
  (a database built from `schema.sql` alone must already have every new table/column — `test_apply_sql` applies `roles.sql`
  right after `schema.sql` with no migrations).
- No new pip dependencies. `numpy` is already in `server/requirements.txt` and may be used.
- Per-patient event filtering is preserved: every new server event carries either `session_id` (routed to that session's
  patient by `main._patient_accepts`) or `patient_id` (limits/features events). The LCD (device token) only gets
  `DEVICE_EVENT_TYPES`.
- Demo pairing code `K7P4M2` keeps working (seed untouched except an added Maya prescription in Task 6).
- `GEMINI_MODE=fake` must work offline (tests run with `GEMINI_MODE=fake`, no keys).
- `PATCH /patients/{id}/limits` keeps the exact `therapist-page` contract: body `{therapist_id, hr_limit}` (id pattern
  `^[A-Za-z0-9_-]{1,64}$`, `hr_limit` 40–220 else 422), 404 unknown patient (checked first), 403 when the patient's
  `therapist_id` is set and differs, response `{patient_id, hr_limit}`, `agent_actions` row agent `therapist`, action
  `hr_limit_set`, detail `{"from": <old>, "to": <new>}`. Avoid touching therapist-page files beyond `main.py`'s router lines.
- Tests: `.venv/bin/python -m pytest <files> -q` from the repo root. DB tests need the local Docker TimescaleDB
  (`make db-local`); they skip when it is unreachable — if they skip, start it and re-run before committing.
- Per user preference: each task runs only its own (new or edited) test files; the whole suite runs once, in Task 9.
- Commit once per task, `git add` only that task's files, message ends with a blank line then
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never commit to `main`.
- Never write NaN/Infinity to clients (Hub already sanitises); never log patient phone numbers.

## Contract decisions made in this plan (the iOS plan relies on them)

1. Server events go out through `Hub.emit`, which does **not** add `seq` (only engine/injected events have `seq`). Clients must
   treat `seq` as optional on `breather`, `hr_recovery`, `rotation_rep`, `rotation_set`, `sync_progress`, `limits_changed`,
   `watch_features_changed`, `haptic`.
2. Sample files for request bodies use a `_request` suffix: `rest_post_me_tremor_request.json`,
   `rest_patch_patient_limits_request.json`, `rest_patch_patient_watch_features_request.json`; the un-suffixed file is the response.
   `rest_patch_patient_limits.json` is byte-identical to the one on `therapist-page` (`{"patient_id": "maya", "hr_limit": 140}`)
   so the branches merge without an add/add conflict.
3. `watch_sync` windows are **not** broadcast (they go straight to ClockSync); `wrist` keeps being broadcast but is
   sanitised to `{type, t?, quat?, accel?, rotation_deg?}`. `quat` is `[w, x, y, z]`.
4. Rotation reps counted from the watch are injected as normal `rep` events with `"source": "watch"`, quality `"good"` when the
   goal was reached else `"partial"` (no `fix_form` haptic for a short turn). Elbow drift = a `cue` `{id: "elbow_drift",
   text: "Keep your elbow at your side"}` + `haptic fix_form`; the next counted rep carries `flags: ["elbow_drift"]`.
5. Elbow drift is measured as the horizontal (x/z) distance between the watch-arm elbow and the same-side shoulder
   (BlazePose 12/14 right, 11/13 left), > 0.08 m for > 0.5 s. ClockSync correlates watch `accel_mag` with the camera wrist
   **acceleration** magnitude (second derivative of the smoothed wrist position) — correlating acceleration with speed would bias
   the offset by a quarter period. Offset sign: `offset_ms = watch_clock − server_clock`.
6. Breather: on an `hr` incident the Guardian also sends the engine `command pause` (`payload.source = "guardian"`) so the
   program actually stops; auto-resume sends `command start`. A patient "I'm OK" during the check-in resolves the alert as
   before but the breather (and the hold on user `start`/`resume`) stays until HR recovers, the incident escalates and closes,
   or the session stops.
7. `sync_progress` is only produced while a session is current (the offset is stored on that session).
8. `done_sets` in `/me/watch` = distinct `(session, set_no)` pairs in `reps` for today's (UTC) sessions of that exercise.

## File map

| File | Task | Responsibility |
|---|---|---|
| `server/db/migrations/007_watch.sql` | 1 | new columns/tables, copilot_ro grants |
| `server/db/schema.sql`, `server/db/roles.sql` | 1 | fresh-setup mirror, grants |
| `server/db/writes.py`, `server/db/queries.py` | 1 | watch writes/reads, purge/export, `RO_TABLES` |
| `server/agents/sql_guard.py` | 1 | allow-list |
| `server/exercises.py` | 1 | rotation exercises, `is_watch_exercise`, goals |
| `server/watch_prefs.py` | 2 | watch pref defaults (`device_prefs` keys) |
| `server/api/me_watch.py` | 2 | `GET /me/watch`, `POST /me/tremor` |
| `server/api/watch_admin.py` | 2 | `PATCH /patients/{id}/limits`, `PATCH …/watch-features`, `GET …/watch` |
| `server/watch_wire.py` | 3 | sanitise `wrist` / `watch_sync` |
| `server/db/writer.py`, `server/db/wrist.py` | 3 | COPY writer generalised; `WristRecorder` (≤ 10 Hz/patient) |
| `server/agents/haptics.py`, `server/devices.py`, `server/state.py` | 3 | `countdown_tick`, LCD `breather`, `AppState.clock_sync` |
| `server/agents/safety_guardian.py` | 4 | breather |
| `server/agents/recovery.py` | 5 | `RecoveryAgent` |
| `server/agents/rotation.py`, `server/engine_bridge.py` | 6 | `RotationCoach`, mock rotation program, `MOCK_WRIST` |
| `server/agents/clock_sync.py` | 7 | `ClockSync` |
| `server/agents/session_analyst.py`, `server/agents/insight.py`, prompts | 8 | AI context, tremor-trend rule |
| `server/main.py` | 2–7 | wiring (each task names its exact anchor) |
| README.md, `device/ios/NETWORKING.md`, `server/samples/README.md` | 9 | docs |

---

### Task 1: Migration 005, rotation exercises, watch writes/queries, copilot_ro grants

**Files:**
- Create: `server/db/migrations/007_watch.sql`
- Modify: `server/db/schema.sql` (append a block at the end), `server/db/roles.sql` (one GRANT line)
- Modify: `server/db/writes.py` (new section before `# ---- helpers`; `purge_patient_data` table tuples)
- Modify: `server/db/queries.py` (new section before `# ---- alerts`; `patient_export`; `RO_TABLES`)
- Modify: `server/agents/sql_guard.py` (`ALLOWED_TABLES`)
- Modify: `server/exercises.py`
- Create: `server/tests/test_watch_db.py`
- Modify tests: `server/tests/test_exercises.py`, `server/tests/test_sql_guard.py:131-133`, `server/tests/test_migrate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks use exactly these):
  - `server.exercises`: `Exercise(..., watch: bool = False)`; `WATCH_EXERCISES: frozenset[str]` =
    `{"forearm_rotation", "shoulder_rotation", "wrist_flexion"}`; `DEFAULT_ROTATION_GOAL_DEG = 80.0`;
    `is_watch_exercise(exercise_id: str | None) -> bool`; `rotation_goal_deg(targets: dict | None) -> float`.
    `describe()` keeps its 5 keys (no `watch`).
  - `server.db.writes`:
    `upsert_hr_recovery(db, session_id: str, patient_id: str, set_index: int, peak_bpm: int, bpm_60s: int, drop_bpm: int, time: Timestamp = None) -> bool`;
    `upsert_rotation_set(db, session_id: str, patient_id: str, exercise_id: str, set_index: int, angles: list[float], best_deg: float, avg_deg: float, goal_deg: float, time: Timestamp = None) -> bool`;
    `upsert_tremor_days(db, patient_id: str, days: list[dict]) -> int | None` (each dict: `day` date, `none_min`, `slight_min`, `mild_min`, `strong_min`, `unknown_min`, `dyskinesia`, `source`);
    `set_patient_hr_limit(db, patient_id: str, hr_limit: int, set_by: str | None) -> int | None`;
    `set_tremor_monitoring(db, patient_id: str, enabled: bool) -> bool`;
    `set_watch_clock_offset(db, session_id: str, offset_ms: int) -> bool`.
  - `server.db.queries`: `me_watch_row(db, patient_id) -> dict | None` (keys `id, name, hr_limit, hr_limit_set_by,
    tremor_monitoring, device_prefs, therapist_id, therapist_name, hr_limit_set_by_name`);
    `therapist_name(db, therapist_id) -> str | None`; `sets_done_today(db, patient_id) -> dict[str, int]`;
    `patient_hr_recovery(db, patient_id, limit=20)`, `patient_rotation_sets(db, patient_id, limit=20)`,
    `patient_tremor_days(db, patient_id, days=14)` (newest first); `session_hr_recovery(db, session_id)`,
    `session_rotation_sets(db, session_id)` (by set). Row keys: hr_recovery `session_id, set_index, peak_bpm, bpm_60s,
    drop_bpm, time`; rotation_sets `session_id, exercise_id, set_index, angles, best_deg, avg_deg, goal_deg, time`;
    tremor `day, none_min, slight_min, mild_min, strong_min, unknown_min, dyskinesia, source`.
  - Tables: `hr_recovery`, `rotation_sets`, `tremor_daily`; columns `patients.tremor_monitoring`,
    `patients.hr_limit_set_by`, `sessions.watch_clock_offset_ms`, `wrist_motion.patient_id`, `wrist_motion.accel`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_watch_db.py`:

```python
"""Migration 005 (Apple Watch): new columns and tables, idempotent, copilot_ro grants, and the writes/queries for
hr_recovery, rotation_sets, tremor_daily, the heart-rate limit, tremor monitoring and the watch clock offset."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta

import psycopg
import pytest

from server.agents.sql_guard import check_sql
from server.db import migrate, queries, writes
from server.db.apply_sql import apply_file
from server.db.pool import Database
from server.tests.dbutil import fresh_database, prepare, ro_dsn

WATCH_TABLES = ("hr_recovery", "rotation_sets", "tremor_daily")


def _columns(dsn: str, table: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                            (table,)).fetchall()
    return {r[0] for r in rows}


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def _session(dsn: str, patient_id: str = "maya", exercise_id: str = "forearm_rotation") -> str:
    sid = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at) VALUES (%s, %s, %s, now())",
                     (sid, patient_id, exercise_id))
    return sid


def _tremor_day(day: date, mild: int = 25) -> dict[str, object]:
    return {"day": day, "none_min": 420, "slight_min": 190, "mild_min": mild, "strong_min": 0, "unknown_min": 60,
            "dyskinesia": "few", "source": "simulated"}


def test_migration_005_adds_columns_and_tables(dsn: str) -> None:
    assert {"patient_id", "accel", "rotation_deg", "quat_w"} <= _columns(dsn, "wrist_motion")
    assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(dsn, "patients")
    assert "watch_clock_offset_ms" in _columns(dsn, "sessions")
    assert {"session_id", "patient_id", "set_index", "peak_bpm", "bpm_60s", "drop_bpm", "time"} <= _columns(
        dsn, "hr_recovery")
    assert {"session_id", "patient_id", "exercise_id", "set_index", "angles", "best_deg", "avg_deg", "goal_deg",
            "time"} <= _columns(dsn, "rotation_sets")
    assert {"patient_id", "day", "none_min", "slight_min", "mild_min", "strong_min", "unknown_min", "dyskinesia",
            "source", "updated_at"} <= _columns(dsn, "tremor_daily")
    with psycopg.connect(dsn) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    assert "007_watch.sql" in names


def test_migration_005_is_idempotent(dsn: str) -> None:
    first = migrate.apply_migrations(dsn)
    assert migrate.apply_migrations(dsn) == first and "007_watch.sql" in first


def test_schema_sql_alone_already_has_the_watch_ddl() -> None:
    with fresh_database() as url:
        apply_file(migrate.DB_DIR / "schema.sql", url)
        assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(url, "patients")
        assert "watch_clock_offset_ms" in _columns(url, "sessions")
        assert {"patient_id", "accel"} <= _columns(url, "wrist_motion")
        for table in WATCH_TABLES:
            assert _columns(url, table), table


def test_copilot_ro_reads_the_watch_tables_and_the_guard_allows_them(dsn: str) -> None:
    with psycopg.connect(ro_dsn(dsn)) as conn:
        for table in WATCH_TABLES:
            conn.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
    for table in WATCH_TABLES:
        assert table in check_sql(f"SELECT * FROM {table}")


async def test_copilot_ro_grant_check_is_clean(db: Database) -> None:
    assert await queries.copilot_ro_grant_problems(db) == []


async def test_hr_recovery_upsert_and_reads(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.upsert_hr_recovery(db, sid, "maya", 1, 126, 104, 22)
    assert await writes.upsert_hr_recovery(db, sid, "maya", 1, 128, 100, 28)  # same set: updated, not duplicated
    assert await writes.upsert_hr_recovery(db, sid, "maya", 2, 130, 110, 20)
    rows = await queries.session_hr_recovery(db, sid)
    assert [(r["set_index"], r["peak_bpm"], r["bpm_60s"], r["drop_bpm"]) for r in rows] == [(1, 128, 100, 28),
                                                                                             (2, 130, 110, 20)]
    recent = await queries.patient_hr_recovery(db, "maya")
    assert str(recent[0]["session_id"]) == sid and {"set_index", "peak_bpm", "time"} <= set(recent[0])


async def test_rotation_set_upsert_and_reads(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.upsert_rotation_set(db, sid, "maya", "forearm_rotation", 1, [62.0, 82.0], 82.0, 72.0, 80.0)
    assert await writes.upsert_rotation_set(db, sid, "maya", "forearm_rotation", 1, [62.0, 84.0], 84.0, 73.0, 80.0)
    [row] = await queries.session_rotation_sets(db, sid)
    assert row["angles"] == pytest.approx([62.0, 84.0]) and row["best_deg"] == pytest.approx(84.0)
    assert row["exercise_id"] == "forearm_rotation" and row["goal_deg"] == pytest.approx(80.0)
    assert str((await queries.patient_rotation_sets(db, "maya"))[0]["session_id"]) == sid


async def test_tremor_days_upsert_and_window(db: Database) -> None:
    today = date.today()
    assert await writes.upsert_tremor_days(db, "maya", [_tremor_day(today), _tremor_day(today - timedelta(days=20))]) == 2
    assert await writes.upsert_tremor_days(db, "maya", [_tremor_day(today, mild=40)]) == 1  # same day: updated
    rows = await queries.patient_tremor_days(db, "maya", 14)
    assert [r["day"] for r in rows] == [today]  # the 20-day-old row is outside the window
    assert rows[0]["mild_min"] == 40 and rows[0]["dyskinesia"] == "few" and rows[0]["source"] == "simulated"


async def test_hr_limit_set_by_and_tremor_flag(db: Database) -> None:
    try:
        assert await writes.set_patient_hr_limit(db, "maya", 120, "okafor") == 120
        assert await writes.set_tremor_monitoring(db, "maya", True) is True
        row = await queries.me_watch_row(db, "maya")
        assert row is not None
        assert row["hr_limit"] == 120 and row["hr_limit_set_by"] == "okafor"
        assert row["hr_limit_set_by_name"] == "Dr. Okafor" and row["therapist_name"] == "Dr. Okafor"
        assert row["tremor_monitoring"] is True
        assert await writes.set_patient_hr_limit(db, "nobody", 120, "okafor") is None
        assert await writes.set_tremor_monitoring(db, "nobody", True) is False
        assert await queries.therapist_name(db, "okafor") == "Dr. Okafor"
        assert await queries.therapist_name(db, "lee") is None
    finally:
        await writes.set_patient_hr_limit(db, "maya", 130, None)
        await writes.set_tremor_monitoring(db, "maya", False)


async def test_watch_clock_offset(dsn: str, db: Database) -> None:
    sid = _session(dsn)
    assert await writes.set_watch_clock_offset(db, sid, -42)
    row = await queries.get_session(db, sid)
    assert row is not None and row["watch_clock_offset_ms"] == -42


async def test_sets_done_today_counts_distinct_sets(dsn: str, db: Database) -> None:
    sid = _session(dsn, exercise_id="wrist_flexion")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for rep, set_no in ((1, 1), (2, 1), (3, 2)):
            conn.execute("INSERT INTO reps (session_id, rep, set_no) VALUES (%s, %s, %s)", (sid, rep, set_no))
    assert (await queries.sets_done_today(db, "maya"))["wrist_flexion"] == 2


async def test_purge_and_export_cover_the_watch_tables(dsn: str, db: Database) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO patients (id, name, therapist_id) VALUES ('w_purge', 'Wes Purge', 'okafor')")
    sid = _session(dsn, patient_id="w_purge")
    await writes.upsert_hr_recovery(db, sid, "w_purge", 1, 120, 100, 20)
    await writes.upsert_rotation_set(db, sid, "w_purge", "forearm_rotation", 1, [70.0], 70.0, 70.0, 80.0)
    await writes.upsert_tremor_days(db, "w_purge", [_tremor_day(date.today())])
    export = await queries.patient_export(db, "w_purge")
    assert len(export["hr_recovery"]) == 1 and len(export["rotation_sets"]) == 1 and len(export["tremor_daily"]) == 1
    counts = await writes.purge_patient_data(db, "w_purge")
    assert counts is not None
    assert counts["hr_recovery"] == 1 and counts["rotation_sets"] == 1 and counts["tremor_daily"] == 1
```

Append to `server/tests/test_exercises.py`:

```python
def test_watch_exercises() -> None:
    assert exercises.WATCH_EXERCISES == {"forearm_rotation", "shoulder_rotation", "wrist_flexion"}
    assert exercises.display_name("forearm_rotation") == "Forearm turns"
    assert exercises.display_name("shoulder_rotation") == "Shoulder turns"
    assert exercises.display_name("wrist_flexion") == "Wrist bends"
    assert exercises.is_watch_exercise("forearm_rotation") and not exercises.is_watch_exercise("squat")
    assert not exercises.is_watch_exercise(None)
    assert exercises.describe("forearm_rotation")["target_key"] == "rotation_deg"
    assert "watch" not in exercises.describe("forearm_rotation")  # describe() keeps its 5-key shape


def test_rotation_goal() -> None:
    assert exercises.rotation_goal_deg({"rotation_deg": 70}) == 70.0
    assert exercises.rotation_goal_deg({}) == exercises.DEFAULT_ROTATION_GOAL_DEG == 80.0
    assert exercises.rotation_goal_deg(None) == 80.0
    for bad in (True, "80", 5, 500):
        assert exercises.rotation_goal_deg({"rotation_deg": bad}) == 80.0
```

In `server/tests/test_sql_guard.py`, replace `test_allow_list_matches_copilot_ro_grants` with:

```python
def test_allow_list_matches_copilot_ro_grants() -> None:
    assert ALLOWED_TABLES == {"progress_v", "sessions", "reps", "form_events", "memory_events", "patients",
                              "prescriptions", "hr_recovery", "rotation_sets", "tremor_daily"}
```

In `server/tests/test_migrate.py`, inside `test_migrations_apply_and_are_idempotent` after the `clinic` assertion add:

```python
        assert "007_watch.sql" in first
        assert {"tremor_monitoring", "hr_limit_set_by"} <= _columns(dsn, "patients")
        assert {"patient_id", "accel"} <= _columns(dsn, "wrist_motion")
        assert "watch_clock_offset_ms" in _columns(dsn, "sessions")
```

and at the end of `test_schema_sql_already_contains_the_migration_ddl` add:

```python
        assert "tremor_monitoring" in _columns(dsn, "patients")
        assert "set_index" in _columns(dsn, "hr_recovery")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_watch_db.py server/tests/test_exercises.py server/tests/test_sql_guard.py server/tests/test_migrate.py -q`
Expected: FAIL (missing columns/tables, `AttributeError: module 'server.exercises' has no attribute 'WATCH_EXERCISES'`, allow-list mismatch).

- [ ] **Step 3: Create the migration**

Create `server/db/migrations/007_watch.sql`:

```sql
-- Apple Watch (tri-watch). Every statement is idempotent (applied by server/db/migrate.py on every start).
-- wrist_motion stays a hypertable; it gains the patient and the raw user acceleration.
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS patient_id text;
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS accel real[];
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tremor_monitoring boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS hr_limit_set_by text;      -- therapist id (no FK: dashboard contract)
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS watch_clock_offset_ms int;  -- watch clock minus server clock

CREATE TABLE IF NOT EXISTS hr_recovery (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  set_index   int  NOT NULL,
  peak_bpm    smallint NOT NULL,
  bpm_60s     smallint NOT NULL,
  drop_bpm    smallint NOT NULL,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, set_index)
);
CREATE INDEX IF NOT EXISTS hr_recovery_patient_idx ON hr_recovery (patient_id, time DESC);

CREATE TABLE IF NOT EXISTS rotation_sets (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  exercise_id text NOT NULL,
  set_index   int  NOT NULL,
  angles      real[] NOT NULL,                 -- one per counted rep, degrees measured by the watch
  best_deg    real,
  avg_deg     real,
  goal_deg    real,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, exercise_id, set_index)
);
CREATE INDEX IF NOT EXISTS rotation_sets_patient_idx ON rotation_sets (patient_id, time DESC);

CREATE TABLE IF NOT EXISTS tremor_daily (
  patient_id  text NOT NULL REFERENCES patients(id),
  day         date NOT NULL,
  none_min    int NOT NULL DEFAULT 0,
  slight_min  int NOT NULL DEFAULT 0,
  mild_min    int NOT NULL DEFAULT 0,
  strong_min  int NOT NULL DEFAULT 0,
  unknown_min int NOT NULL DEFAULT 0,
  dyskinesia  text CHECK (dyskinesia IN ('none','few','some','many')),
  source      text NOT NULL CHECK (source IN ('device','simulated')),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (patient_id, day)
);

-- copilot_ro (roles.sql) may not exist yet on a fresh setup: roles.sql grants these too.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_ro') THEN
    GRANT SELECT ON hr_recovery, rotation_sets, tremor_daily TO copilot_ro;
  END IF;
END
$$;

INSERT INTO schema_migrations (name) VALUES ('007_watch.sql') ON CONFLICT (name) DO NOTHING;
```

- [ ] **Step 4: Mirror it in `schema.sql` and grant in `roles.sql`**

Append to the end of `server/db/schema.sql` (same DDL, no `DO` block, no `schema_migrations` insert):

```sql

-- tri-watch (2026-10-11): Apple Watch. Mirrors db/migrations/007_watch.sql (grants are in roles.sql).
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS patient_id text;
ALTER TABLE wrist_motion ADD COLUMN IF NOT EXISTS accel real[];
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tremor_monitoring boolean NOT NULL DEFAULT false;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS hr_limit_set_by text;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS watch_clock_offset_ms int;
CREATE TABLE IF NOT EXISTS hr_recovery (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  set_index   int  NOT NULL,
  peak_bpm    smallint NOT NULL,
  bpm_60s     smallint NOT NULL,
  drop_bpm    smallint NOT NULL,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, set_index)
);
CREATE INDEX IF NOT EXISTS hr_recovery_patient_idx ON hr_recovery (patient_id, time DESC);
CREATE TABLE IF NOT EXISTS rotation_sets (
  session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  patient_id  text NOT NULL REFERENCES patients(id),
  exercise_id text NOT NULL,
  set_index   int  NOT NULL,
  angles      real[] NOT NULL,
  best_deg    real,
  avg_deg     real,
  goal_deg    real,
  time        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, exercise_id, set_index)
);
CREATE INDEX IF NOT EXISTS rotation_sets_patient_idx ON rotation_sets (patient_id, time DESC);
CREATE TABLE IF NOT EXISTS tremor_daily (
  patient_id  text NOT NULL REFERENCES patients(id),
  day         date NOT NULL,
  none_min    int NOT NULL DEFAULT 0,
  slight_min  int NOT NULL DEFAULT 0,
  mild_min    int NOT NULL DEFAULT 0,
  strong_min  int NOT NULL DEFAULT 0,
  unknown_min int NOT NULL DEFAULT 0,
  dyskinesia  text CHECK (dyskinesia IN ('none','few','some','many')),
  source      text NOT NULL CHECK (source IN ('device','simulated')),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (patient_id, day)
);
```

In `server/db/roles.sql`, directly after the line `                prescriptions TO copilot_ro;` add:

```sql
GRANT SELECT ON hr_recovery, rotation_sets, tremor_daily TO copilot_ro;   -- Apple Watch tables (migration 005)
```

- [ ] **Step 5: Exercises catalog**

Replace `server/exercises.py` with:

```python
"""The exercise catalog (MVP spec §9): one display name per exercise id, shared by the LCD caption, /me and the
phone's plan/progress screens so every surface says "Mini squats" the same way. Exercises flagged `watch` need the
Apple Watch's motion (rotation exercises; their goal is `prescriptions.targets.rotation_deg`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Exercise:
    exercise_id: str
    exercise_name: str
    metric_label: str   # the main metric, as the phone labels its big number
    unit: str
    target_key: str     # the prescriptions.targets key that holds the goal for the main metric
    watch: bool = False  # counted from the Apple Watch's rotation, not the cameras


EXERCISES: dict[str, Exercise] = {e.exercise_id: e for e in (
    Exercise("squat", "Mini squats", "Knee bend", "°", "knee_deg"),
    Exercise("sit_to_stand", "Sit to stand", "Time to stand", "s", "time_s"),
    Exercise("single_leg_stance", "Single-leg stance", "Hold", "s", "hold_s"),
    Exercise("heel_raise", "Heel raises", "Heel height", "cm", "height_cm"),
    Exercise("glute_bridge", "Glute bridge", "Hip lift", "cm", "hold_s"),
    Exercise("bird_dog", "Bird-dog", "Trunk stability", "°", "hold_s"),
    Exercise("arm_raise", "Arm raise", "Shoulder angle", "°", "shoulder_deg"),
    Exercise("forearm_rotation", "Forearm turns", "Turn", "°", "rotation_deg", watch=True),
    Exercise("shoulder_rotation", "Shoulder turns", "Turn", "°", "rotation_deg", watch=True),
    Exercise("wrist_flexion", "Wrist bends", "Bend", "°", "rotation_deg", watch=True),
)}

WATCH_EXERCISES: frozenset[str] = frozenset(e.exercise_id for e in EXERCISES.values() if e.watch)
DEFAULT_ROTATION_GOAL_DEG = 80.0
_GOAL_RANGE = (10.0, 180.0)


def display_name(exercise_id: str) -> str:
    """Catalog name, or the id humanised ("heel_slide" → "Heel slide"). Never raises."""
    found = EXERCISES.get(exercise_id)
    return found.exercise_name if found else exercise_id.replace("_", " ").capitalize()


def describe(exercise_id: str) -> dict[str, str]:
    """The 5-key description the phone's plan screens use (the `watch` flag is not part of it)."""
    found = EXERCISES.get(exercise_id)
    if found is None:
        found = Exercise(exercise_id, display_name(exercise_id), "Progress", "", "")
    out = asdict(found)
    out.pop("watch", None)
    return out


def is_watch_exercise(exercise_id: str | None) -> bool:
    return exercise_id in WATCH_EXERCISES


def rotation_goal_deg(targets: dict[str, Any] | None) -> float:
    """The prescription's `rotation_deg` goal when it is a plain number in 10..180, else the default (80°)."""
    value = (targets or {}).get("rotation_deg")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and _GOAL_RANGE[0] <= value <= _GOAL_RANGE[1]:
        return float(value)
    return DEFAULT_ROTATION_GOAL_DEG
```

- [ ] **Step 6: Writes**

In `server/db/writes.py`:

(a) In `purge_patient_data`, change the two table tuples to:

```python
                for table in ("pose_frames", "reps", "form_events", "wrist_motion", "heart_rate", "hr_recovery",
                              "rotation_sets"):
```
and
```python
            for table in ("alerts", "memory_events", "notifications", "agent_actions", "plan_proposals", "messages",
                          "tremor_daily"):
```

(b) Insert this section directly above `# ---- helpers ----`:

```python
# ---- Apple Watch (migration 005) ------------------------------------------------------------------------------

TREMOR_MINUTE_FIELDS = ("none_min", "slight_min", "mild_min", "strong_min", "unknown_min")


async def upsert_hr_recovery(db: Database | None, session_id: str, patient_id: str, set_index: int, peak_bpm: int,
                             bpm_60s: int, drop_bpm: int, time: Timestamp = None) -> bool:
    """One row per (session, set); a re-run for the same set overwrites it."""
    sql = ("INSERT INTO hr_recovery (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm, time) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (session_id, set_index) DO UPDATE SET "
           "peak_bpm = EXCLUDED.peak_bpm, bpm_60s = EXCLUDED.bpm_60s, drop_bpm = EXCLUDED.drop_bpm, "
           "time = EXCLUDED.time")
    params = (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm, ts(time))
    return await _safe(db, "upsert_hr_recovery", lambda d: _done(d.execute(sql, params)), False,
                       session_id=session_id)


async def upsert_rotation_set(db: Database | None, session_id: str, patient_id: str, exercise_id: str,
                              set_index: int, angles: list[float], best_deg: float, avg_deg: float, goal_deg: float,
                              time: Timestamp = None) -> bool:
    sql = ("INSERT INTO rotation_sets (session_id, patient_id, exercise_id, set_index, angles, best_deg, avg_deg, "
           "goal_deg, time) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
           "ON CONFLICT (session_id, exercise_id, set_index) DO UPDATE SET angles = EXCLUDED.angles, "
           "best_deg = EXCLUDED.best_deg, avg_deg = EXCLUDED.avg_deg, goal_deg = EXCLUDED.goal_deg, "
           "time = EXCLUDED.time")
    params = (session_id, patient_id, exercise_id, set_index, [float(a) for a in angles], best_deg, avg_deg,
              goal_deg, ts(time))
    return await _safe(db, "upsert_rotation_set", lambda d: _done(d.execute(sql, params)), False,
                       session_id=session_id)


async def upsert_tremor_days(db: Database | None, patient_id: str, days: list[dict[str, Any]]) -> int | None:
    """Daily tremor summaries from the watch (`POST /me/tremor`), one row per (patient, day), in one transaction.
    Returns how many days were stored, None on failure."""
    sql = ("INSERT INTO tremor_daily (patient_id, day, none_min, slight_min, mild_min, strong_min, unknown_min, "
           "dyskinesia, source, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
           "ON CONFLICT (patient_id, day) DO UPDATE SET none_min = EXCLUDED.none_min, "
           "slight_min = EXCLUDED.slight_min, mild_min = EXCLUDED.mild_min, strong_min = EXCLUDED.strong_min, "
           "unknown_min = EXCLUDED.unknown_min, dyskinesia = EXCLUDED.dyskinesia, source = EXCLUDED.source, "
           "updated_at = now()")

    async def run(d: Database) -> int:
        async with d.connection() as conn, conn.transaction():
            for day in days:
                await conn.execute(sql, (patient_id, day["day"], *(int(day[k]) for k in TREMOR_MINUTE_FIELDS),
                                         day["dyskinesia"], day["source"]))
        return len(days)

    return await _safe(db, "upsert_tremor_days", run, None, patient_id=patient_id)


async def set_patient_hr_limit(db: Database | None, patient_id: str, hr_limit: int, set_by: str | None) -> int | None:
    """Sets patients.hr_limit and who set it; returns the new limit, None if the patient doesn't exist (or on
    failure). Same contract as therapist-page's dashboard_writes.set_hr_limit, plus `hr_limit_set_by`."""
    sql = "UPDATE patients SET hr_limit = %s, hr_limit_set_by = %s WHERE id = %s RETURNING hr_limit"

    async def run(d: Database) -> int | None:
        row = await d.fetchrow(sql, (hr_limit, set_by, patient_id))
        return int(row["hr_limit"]) if row else None

    return await _safe(db, "set_patient_hr_limit", run, None, patient_id=patient_id)


async def set_tremor_monitoring(db: Database | None, patient_id: str, enabled: bool) -> bool:
    sql = "UPDATE patients SET tremor_monitoring = %s WHERE id = %s"
    return await _safe(db, "set_tremor_monitoring", lambda d: _ok(d.execute(sql, (enabled, patient_id))), False,
                       patient_id=patient_id)


async def set_watch_clock_offset(db: Database | None, session_id: str, offset_ms: int) -> bool:
    sql = "UPDATE sessions SET watch_clock_offset_ms = %s WHERE id = %s"
    return await _safe(db, "set_watch_clock_offset", lambda d: _ok(d.execute(sql, (int(offset_ms), session_id))),
                       False, session_id=session_id)
```

- [ ] **Step 7: Queries**

In `server/db/queries.py`:

(a) Replace `RO_TABLES` with:

```python
RO_TABLES = ("progress_v", "sessions", "reps", "form_events", "memory_events", "prescriptions", "pose_frames",
             "daily_progress_archive", "data_checks", "hr_recovery", "rotation_sets", "tremor_daily")
```

(b) In `patient_export`, before the `return`, add:

```python
    hr_recovery = await db.fetch(f"SELECT {HR_RECOVERY_COLUMNS} FROM hr_recovery WHERE patient_id = %s ORDER BY time",
                                 (patient_id,), timeout_s=10)
    rotation = await db.fetch(f"SELECT {ROTATION_SET_COLUMNS} FROM rotation_sets WHERE patient_id = %s "
                              "ORDER BY time", (patient_id,), timeout_s=10)
    tremor = await db.fetch(f"SELECT {TREMOR_COLUMNS} FROM tremor_daily WHERE patient_id = %s ORDER BY day",
                            (patient_id,), timeout_s=10)
```
and add `"hr_recovery": hr_recovery, "rotation_sets": rotation, "tremor_daily": tremor` to the returned dict.

(c) Insert this section directly above `# ---- alerts ----`:

```python
# ---- Apple Watch (migration 005) ------------------------------------------------------------------------------

HR_RECOVERY_COLUMNS = "session_id, set_index, peak_bpm, bpm_60s, drop_bpm, time"
ROTATION_SET_COLUMNS = "session_id, exercise_id, set_index, angles, best_deg, avg_deg, goal_deg, time"
TREMOR_COLUMNS = "day, none_min, slight_min, mild_min, strong_min, unknown_min, dyskinesia, source"

ME_WATCH_SQL = """
    SELECT p.id, p.name, p.hr_limit, p.hr_limit_set_by, p.tremor_monitoring, p.device_prefs,
           t.id AS therapist_id, t.name AS therapist_name, sb.name AS hr_limit_set_by_name
    FROM patients p
    LEFT JOIN therapists t ON t.id = p.therapist_id
    LEFT JOIN therapists sb ON sb.id = p.hr_limit_set_by
    WHERE p.id = %s"""


async def me_watch_row(db: Database, patient_id: str) -> dict[str, Any] | None:
    """What GET /me/watch needs about the patient, their therapist and who set the heart-rate limit."""
    return await db.fetchrow(ME_WATCH_SQL, (patient_id,))


async def therapist_name(db: Database, therapist_id: str) -> str | None:
    row = await db.fetchrow("SELECT name FROM therapists WHERE id = %s", (therapist_id,))
    return row["name"] if row else None


async def sets_done_today(db: Database, patient_id: str) -> dict[str, int]:
    """exercise_id → distinct (session, set) pairs with at least one rep, over today's sessions (UTC day)."""
    rows = await db.fetch(
        "SELECT s.exercise_id, count(DISTINCT r.session_id::text || ':' || r.set_no) AS done_sets "
        "FROM sessions s JOIN reps r ON r.session_id = s.id "
        "WHERE s.patient_id = %s AND r.set_no IS NOT NULL AND s.started_at >= date_trunc('day', now(), 'UTC') "
        "GROUP BY s.exercise_id", (patient_id,))
    return {r["exercise_id"]: int(r["done_sets"]) for r in rows}


async def patient_hr_recovery(db: Database, patient_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return await db.fetch(f"SELECT {HR_RECOVERY_COLUMNS} FROM hr_recovery WHERE patient_id = %s "
                          "ORDER BY time DESC LIMIT %s", (patient_id, limit))


async def patient_rotation_sets(db: Database, patient_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return await db.fetch(f"SELECT {ROTATION_SET_COLUMNS} FROM rotation_sets WHERE patient_id = %s "
                          "ORDER BY time DESC LIMIT %s", (patient_id, limit))


async def patient_tremor_days(db: Database, patient_id: str, days: int = 14) -> list[dict[str, Any]]:
    """The last `days` calendar days (today included), newest first."""
    return await db.fetch(f"SELECT {TREMOR_COLUMNS} FROM tremor_daily WHERE patient_id = %s "
                          "AND day > current_date - %s::int ORDER BY day DESC", (patient_id, days))


async def session_hr_recovery(db: Database, session_id: str) -> list[dict[str, Any]]:
    return await db.fetch(f"SELECT {HR_RECOVERY_COLUMNS} FROM hr_recovery WHERE session_id = %s "
                          "ORDER BY set_index", (session_id,))


async def session_rotation_sets(db: Database, session_id: str) -> list[dict[str, Any]]:
    return await db.fetch(f"SELECT {ROTATION_SET_COLUMNS} FROM rotation_sets WHERE session_id = %s "
                          "ORDER BY exercise_id, set_index", (session_id,))
```

- [ ] **Step 8: SQL guard allow-list**

In `server/agents/sql_guard.py` replace `ALLOWED_TABLES` with:

```python
ALLOWED_TABLES = frozenset({"progress_v", "sessions", "reps", "form_events", "memory_events", "patients",
                            "prescriptions", "hr_recovery", "rotation_sets", "tremor_daily"})
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_watch_db.py server/tests/test_exercises.py server/tests/test_sql_guard.py server/tests/test_migrate.py server/tests/test_apply_sql.py -q`
Expected: all PASS (no skips — if DB tests skip, run `make db-local` first).

- [ ] **Step 10: Commit**

```bash
git add server/db/migrations/007_watch.sql server/db/schema.sql server/db/roles.sql server/db/writes.py \
  server/db/queries.py server/agents/sql_guard.py server/exercises.py server/tests/test_watch_db.py \
  server/tests/test_exercises.py server/tests/test_sql_guard.py server/tests/test_migrate.py
git commit -F - <<'EOF'
Server: migration 005 (watch tables), rotation exercises, watch writes/queries, copilot_ro grants

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: Watch REST routes (`/me/watch`, `/me/tremor`, limits, watch-features, `/patients/{id}/watch`)

**Files:**
- Create: `server/watch_prefs.py`, `server/api/me_watch.py`, `server/api/watch_admin.py`
- Modify: `server/main.py` (imports + two `include_router` lines only)
- Create samples: `server/samples/rest_get_me_watch.json`, `rest_post_me_tremor_request.json`, `rest_post_me_tremor.json`,
  `rest_patch_patient_limits_request.json`, `rest_patch_patient_limits.json`, `rest_patch_patient_watch_features_request.json`,
  `rest_patch_patient_watch_features.json`, `rest_get_patient_watch.json`
- Modify: `server/samples/README.md`, `server/tests/test_samples.py` (`REST_SAMPLES_REQUIRED_KEYS`)
- Create test: `server/tests/test_watch_api.py`

**Interfaces:**
- Consumes (Task 1): `writes.set_patient_hr_limit`, `writes.set_tremor_monitoring`, `writes.upsert_tremor_days`,
  `writes.insert_agent_action`, `queries.me_watch_row`, `queries.sets_done_today`, `queries.prescriptions`,
  `queries.get_patient`, `queries.therapist_name`, `queries.patient_hr_recovery`, `queries.patient_rotation_sets`,
  `queries.patient_tremor_days`, `exercises.is_watch_exercise`, `exercises.rotation_goal_deg`.
- Produces:
  - `server.watch_prefs`: `WATCH_PREF_DEFAULTS = {"watch_taps": "normal", "watch_arm": "right", "watch_double_tap": True,
    "watch_captions": True}`; `watch_prefs(device_prefs: dict | None) -> dict`; `watch_arm(device_prefs: dict | None) -> str`
    (`"left"|"right"`) — used by Tasks 6 and 7.
  - `server.api.watch_admin.refresh_live_limit(patient_id: str, hr_limit: int) -> bool` (mutates
    `sessions.current.profile.hr_limit` when the current session is this patient's).
  - WS events emitted by `Hub.emit`: `limits_changed {patient_id, hr_limit, set_by_name, t}`,
    `watch_features_changed {patient_id, tremor_monitoring, t}`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_watch_api.py`:

```python
"""Apple Watch REST: GET /me/watch, POST /me/tremor, PATCH /patients/{id}/limits (therapist-page contract plus a live
push and a live-session refresh), PATCH /patients/{id}/watch-features and GET /patients/{id}/watch."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from server import security
from server.state import get_state
from server.tests.conftest import AUTH, TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn
from server.tests.test_pairing import CONTACT, PROFILE
from server.tests.test_ws_safety import read_until


@pytest.fixture(autouse=True)
def _clear_pairing_attempts() -> Iterator[None]:
    from server.api import pair as pair_api

    pair_api._attempts.clear()
    yield
    pair_api._attempts.clear()


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def _sql(dsn: str, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(autouse=True)
def _reset_maya(dsn: str) -> Iterator[None]:
    _sql(dsn, "UPDATE patients SET hr_limit = 130, hr_limit_set_by = NULL, tremor_monitoring = false, "
              "device_prefs = '{}' WHERE id = 'maya'")
    _sql(dsn, "DELETE FROM tremor_daily")
    yield


@pytest.fixture
def client(make_client: Callable[..., TestClient], dsn: str) -> TestClient:
    security.invalidate_token_cache()
    return make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))


@pytest.fixture
def phone(client: TestClient) -> dict[str, str]:
    r = client.post("/pair", json={"code": "K7P4M2", "profile": PROFILE, "emergency_contact": CONTACT})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _day(day: date, **overrides: Any) -> dict[str, Any]:
    body = {"day": day.isoformat(), "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0,
            "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}
    return {**body, **overrides}


# ---- GET /me/watch ---------------------------------------------------------------------------------------------

def test_get_me_watch_defaults(client: TestClient, phone: dict[str, str]) -> None:
    r = client.get("/me/watch", headers=phone)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"first_name", "hr_limit", "hr_limit_set_by_name", "therapist_name", "tremor_monitoring",
                         "prefs", "plan"}
    assert body["first_name"] == "Maya" and body["hr_limit"] == 130 and body["hr_limit_set_by_name"] is None
    assert body["therapist_name"] == "Dr. Okafor" and body["tremor_monitoring"] is False
    assert body["prefs"] == {"watch_taps": "normal", "watch_arm": "right", "watch_double_tap": True,
                             "watch_captions": True}
    squat = next(p for p in body["plan"] if p["exercise_id"] == "squat")
    assert squat["name"] == "Mini squats" and squat["watch"] is False and "goal_deg" not in squat
    assert isinstance(squat["done_sets"], int) and squat["sets"] == 3 and squat["reps"] == 10
    assert client.get("/me/watch").status_code == 401
    assert client.get("/me/watch", headers=AUTH).status_code == 400
    assert client.get("/me/watch?patient_id=maya", headers=AUTH).json()["first_name"] == "Maya"


def test_get_me_watch_prefs_and_watch_plan_items(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    prefs = {"watch_taps": "strong", "watch_arm": "left", "watch_double_tap": False, "watch_captions": False}
    assert client.put("/me", headers=phone, json={"device_prefs": {**prefs, "captions": True}}).status_code == 200
    put = client.put("/prescriptions/maya", headers=AUTH, json={
        "exercise_id": "forearm_rotation", "created_by": "okafor", "sets": 3, "reps": 10,
        "targets": {"rotation_deg": 75}})
    assert put.status_code == 200
    sid = str(uuid.uuid4())
    _sql(dsn, "INSERT INTO sessions (id, patient_id, exercise_id, started_at) "
              "VALUES (%s, 'maya', 'forearm_rotation', now())", sid)
    for rep, set_no in ((1, 1), (2, 1), (3, 2)):
        _sql(dsn, "INSERT INTO reps (session_id, rep, set_no, quality) VALUES (%s, %s, %s, 1.0)", sid, rep, set_no)
    body = client.get("/me/watch", headers=phone).json()
    assert body["prefs"] == prefs
    turns = next(p for p in body["plan"] if p["exercise_id"] == "forearm_rotation")
    assert turns == {"exercise_id": "forearm_rotation", "name": "Forearm turns", "sets": 3, "reps": 10,
                     "done_sets": 2, "watch": True, "goal_deg": 75.0}


# ---- PATCH /patients/{id}/limits --------------------------------------------------------------------------------

def test_patch_limits_contract_live_refresh_and_push(client: TestClient, dsn: str) -> None:
    state = get_state()
    current = state.sessions.current
    assert current is not None and current.patient_id == "maya"  # the mock boot session
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        r = client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 120})
        assert r.status_code == 200 and r.json() == {"patient_id": "maya", "hr_limit": 120}
        event = read_until(ws, lambda m: m.get("type") == "limits_changed")
    assert event["patient_id"] == "maya" and event["hr_limit"] == 120 and event["set_by_name"] == "Dr. Okafor"
    assert current.profile.hr_limit == 120  # Guardian and Coach read this object on every hr event
    assert state.guardian._profile(current.session_id).hr_limit == 120
    assert _sql(dsn, "SELECT hr_limit, hr_limit_set_by FROM patients WHERE id = 'maya'") == [(120, "okafor")]
    [(detail,)] = _sql(dsn, "SELECT detail FROM agent_actions WHERE action = 'hr_limit_set' ORDER BY time DESC "
                            "LIMIT 1")
    assert detail == {"from": 130, "to": 120}


def test_patch_limits_errors(client: TestClient, phone: dict[str, str]) -> None:
    ok = {"therapist_id": "okafor", "hr_limit": 120}
    assert client.patch("/patients/nobody/limits", headers=AUTH, json=ok).status_code == 404
    assert client.patch("/patients/maya/limits", headers=AUTH,
                        json={"therapist_id": "lee", "hr_limit": 120}).status_code == 403
    for bad in (39, 221):
        assert client.patch("/patients/maya/limits", headers=AUTH,
                            json={"therapist_id": "okafor", "hr_limit": bad}).status_code == 422
    assert client.patch("/patients/maya/limits", json=ok).status_code == 401
    assert client.patch("/patients/maya/limits", headers=phone, json=ok).status_code == 401  # full token only


def test_me_watch_shows_who_set_the_limit(client: TestClient, phone: dict[str, str]) -> None:
    client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 118})
    body = client.get("/me/watch", headers=phone).json()
    assert body["hr_limit"] == 118 and body["hr_limit_set_by_name"] == "Dr. Okafor"


def test_patient_socket_gets_its_own_limits_changed(client: TestClient, phone: dict[str, str]) -> None:
    token = phone["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        client.patch("/patients/maya/limits", headers=AUTH, json={"therapist_id": "okafor", "hr_limit": 125})
        assert read_until(ws, lambda m: m.get("type") == "limits_changed")["hr_limit"] == 125


# ---- watch features + tremor --------------------------------------------------------------------------------------

def test_tremor_upload_needs_monitoring_on(client: TestClient, phone: dict[str, str], dsn: str) -> None:
    today = date.today()
    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today)]})
    assert r.status_code == 403 and r.json() == {"detail": "tremor monitoring off"}

    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        r = client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
        assert r.status_code == 200 and r.json() == {"patient_id": "maya", "tremor_monitoring": True}
        event = read_until(ws, lambda m: m.get("type") == "watch_features_changed")
    assert event["patient_id"] == "maya" and event["tremor_monitoring"] is True
    assert client.get("/me/watch", headers=phone).json()["tremor_monitoring"] is True

    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today), _day(today - timedelta(days=1))]})
    assert r.status_code == 200 and r.json() == {"stored": 2}
    r = client.post("/me/tremor", headers=phone, json={"days": [_day(today, mild_min=40, dyskinesia="some")]})
    assert r.status_code == 200 and r.json() == {"stored": 1}
    assert _sql(dsn, "SELECT mild_min, dyskinesia FROM tremor_daily WHERE patient_id = 'maya' AND day = %s",
                today) == [(40, "some")]


@pytest.mark.parametrize("days", [
    [],
    [_day(date.today() - timedelta(days=i)) for i in range(15)],
    [_day(date.today(), dyskinesia="lots")],
    [_day(date.today(), source="guess")],
    [_day(date.today(), mild_min=-1)],
    [_day(date.today(), mild_min=1441)],
])
def test_tremor_upload_validation(client: TestClient, phone: dict[str, str], days: list[dict[str, Any]]) -> None:
    client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
    assert client.post("/me/tremor", headers=phone, json={"days": days}).status_code == 422


def test_tremor_upload_is_rate_limited(client: TestClient, phone: dict[str, str]) -> None:
    client.patch("/patients/maya/watch-features", headers=AUTH, json={"tremor_monitoring": True})
    codes = [client.post("/me/tremor", headers=phone, json={"days": [_day(date.today())]}).status_code
             for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_watch_features_errors(client: TestClient, phone: dict[str, str]) -> None:
    assert client.patch("/patients/nobody/watch-features", headers=AUTH,
                        json={"tremor_monitoring": True}).status_code == 404
    assert client.patch("/patients/maya/watch-features", headers=AUTH, json={}).status_code == 422
    assert client.patch("/patients/maya/watch-features", headers=phone,
                        json={"tremor_monitoring": True}).status_code == 401


# ---- GET /patients/{id}/watch -----------------------------------------------------------------------------------

def test_get_patient_watch(client: TestClient, dsn: str) -> None:
    sid = str(uuid.uuid4())
    _sql(dsn, "INSERT INTO sessions (id, patient_id, exercise_id, started_at) "
              "VALUES (%s, 'maya', 'forearm_rotation', now())", sid)
    _sql(dsn, "INSERT INTO hr_recovery (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm) "
              "VALUES (%s, 'maya', 2, 126, 104, 22)", sid)
    _sql(dsn, "INSERT INTO rotation_sets (session_id, patient_id, exercise_id, set_index, angles, best_deg, avg_deg, "
              "goal_deg) VALUES (%s, 'maya', 'forearm_rotation', 1, '{62,82}', 82, 72, 80)", sid)
    _sql(dsn, "INSERT INTO tremor_daily (patient_id, day, mild_min, source) VALUES ('maya', current_date, 25, "
              "'simulated')")
    r = client.get("/patients/maya/watch", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"patient_id", "hr_recovery", "rotation_sets", "tremor"}
    rec = next(x for x in body["hr_recovery"] if x["session_id"] == sid)
    assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (2, 126, 104, 22) and rec["time"]
    rot = next(x for x in body["rotation_sets"] if x["session_id"] == sid)
    assert rot["angles"] == [62.0, 82.0] and rot["best_deg"] == 82.0 and rot["goal_deg"] == 80.0
    assert body["tremor"][0]["mild_min"] == 25 and body["tremor"][0]["source"] == "simulated"
    assert client.get("/patients/nobody/watch", headers=AUTH).status_code == 404
    assert client.get("/patients/maya/watch").status_code == 401
```

In `server/tests/test_samples.py`, add to `REST_SAMPLES_REQUIRED_KEYS`:

```python
    "rest_get_me_watch.json": {"first_name", "hr_limit", "hr_limit_set_by_name", "therapist_name",
                               "tremor_monitoring", "prefs", "plan"},
    "rest_post_me_tremor_request.json": {"days"},
    "rest_post_me_tremor.json": {"stored"},
    "rest_patch_patient_limits_request.json": {"therapist_id", "hr_limit"},
    "rest_patch_patient_limits.json": {"patient_id", "hr_limit"},
    "rest_patch_patient_watch_features_request.json": {"tremor_monitoring"},
    "rest_patch_patient_watch_features.json": {"patient_id", "tremor_monitoring"},
    "rest_get_patient_watch.json": {"patient_id", "hr_recovery", "rotation_sets", "tremor"},
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_watch_api.py server/tests/test_samples.py -q`
Expected: FAIL (404 on the new routes, missing sample files).

- [ ] **Step 3: `server/watch_prefs.py`**

```python
"""Apple Watch settings, stored as flat keys in patients.device_prefs (written through `PUT /me`), with the
contract's defaults. Shared by GET /me/watch, RotationCoach (which arm) and ClockSync (which wrist)."""

from __future__ import annotations

from typing import Any

WATCH_PREF_DEFAULTS: dict[str, Any] = {"watch_taps": "normal", "watch_arm": "right", "watch_double_tap": True,
                                       "watch_captions": True}
_CHOICES: dict[str, tuple[str, ...]] = {"watch_taps": ("normal", "strong"), "watch_arm": ("left", "right")}


def watch_prefs(device_prefs: dict[str, Any] | None) -> dict[str, Any]:
    """The four watch_* prefs; a missing or invalid value falls back to its default."""
    prefs = device_prefs if isinstance(device_prefs, dict) else {}
    out = dict(WATCH_PREF_DEFAULTS)
    for key, default in WATCH_PREF_DEFAULTS.items():
        value = prefs.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(value, str) and value in _CHOICES[key]:
            out[key] = value
    return out


def watch_arm(device_prefs: dict[str, Any] | None) -> str:
    """"left" or "right": the arm the watch is worn on."""
    return str(watch_prefs(device_prefs)["watch_arm"])
```

- [ ] **Step 4: `server/api/me_watch.py`**

```python
"""The patient's watch (contract `rest_get_me_watch`, `rest_post_me_tremor`): `GET /me/watch` is what the iPhone
pushes to the watch as its `setup` context; `POST /me/tremor` stores the watch's daily tremor summaries."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import exercises, security
from server.api import ratelimit
from server.api.db_dep import require_db
from server.api.scope import patient_for
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state
from server.watch_prefs import watch_prefs

router = APIRouter()

MAX_TREMOR_DAYS = 14
MINUTES_PER_DAY = 1440


def _first_name(name: str | None) -> str:
    return (name or "").split(" ")[0] or "there"


def plan_item(rx: dict[str, Any], done: dict[str, int]) -> dict[str, Any]:
    exercise_id = rx["exercise_id"]
    item: dict[str, Any] = {"exercise_id": exercise_id, "name": exercises.display_name(exercise_id),
                            "sets": rx["sets"], "reps": rx["reps"], "done_sets": int(done.get(exercise_id, 0)),
                            "watch": exercises.is_watch_exercise(exercise_id)}
    if item["watch"]:
        item["goal_deg"] = exercises.rotation_goal_deg(rx.get("targets"))
    return item


async def build_me_watch(db: Database, patient_id: str) -> dict[str, Any]:
    row, rx, done = await asyncio.gather(queries.me_watch_row(db, patient_id), queries.prescriptions(db, patient_id),
                                         queries.sets_done_today(db, patient_id))
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return {
        "first_name": _first_name(row["name"]),
        "hr_limit": row["hr_limit"],
        "hr_limit_set_by_name": row["hr_limit_set_by_name"],
        "therapist_name": row["therapist_name"],
        "tremor_monitoring": bool(row["tremor_monitoring"]),
        "prefs": watch_prefs(row["device_prefs"]),
        "plan": [plan_item(r, done) for r in rx],
    }


@router.get("/me/watch")
async def get_me_watch(principal: security.Principal = Depends(security.require_principal),
                       patient_id: str | None = Query(default=None),
                       db: Database = Depends(require_db)) -> dict[str, Any]:
    return await build_me_watch(db, patient_for(principal, patient_id))


class TremorDayIn(BaseModel):
    day: date
    none_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    slight_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    mild_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    strong_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    unknown_min: int = Field(ge=0, le=MINUTES_PER_DAY)
    dyskinesia: Literal["none", "few", "some", "many"]
    source: Literal["device", "simulated"]


class TremorIn(BaseModel):
    days: list[TremorDayIn] = Field(min_length=1, max_length=MAX_TREMOR_DAYS)


@router.post("/me/tremor")
async def post_tremor(body: TremorIn, principal: security.Principal = Depends(security.require_principal),
                      patient_id: str | None = Query(default=None),
                      db: Database = Depends(require_db)) -> dict[str, Any]:
    pid = patient_for(principal, patient_id)
    ratelimit.check_patient(get_state().patient_limiter, principal.scope, "tremor", pid)
    row = await queries.me_watch_row(db, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if not row["tremor_monitoring"]:
        raise HTTPException(status_code=403, detail="tremor monitoring off")
    stored = await writes.upsert_tremor_days(db, pid, [d.model_dump() for d in body.days])
    if stored is None:
        raise HTTPException(status_code=503, detail="database unavailable: tremor not saved")
    return {"stored": stored}
```

- [ ] **Step 5: `server/api/watch_admin.py`**

```python
"""Therapist watch routes (full token; registered with `require_token`):
- `PATCH /patients/{id}/limits` — the therapist-page contract, plus `hr_limit_set_by`, a live-session refresh and a
  `limits_changed` push. When `therapist-page` merges, keep this route and delete its duplicate in
  `server/api/dashboard.py`.
- `PATCH /patients/{id}/watch-features` — tremor monitoring on/off (`watch_features_changed` push).
- `GET /patients/{id}/watch` — last 20 HR recoveries, last 20 rotation sets, 14 days of tremor."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from server.api.db_dep import require_db
from server.db import queries, writes
from server.db.pool import Database
from server.state import get_state

router = APIRouter()

ID_RE = r"^[A-Za-z0-9_-]{1,64}$"  # the dashboard's id shape (same as therapist-page)


class LimitsIn(BaseModel):
    therapist_id: str = Field(pattern=ID_RE, max_length=64)
    hr_limit: int = Field(ge=40, le=220)


class WatchFeaturesIn(BaseModel):
    tremor_monitoring: bool


def refresh_live_limit(patient_id: str, hr_limit: int) -> bool:
    """Apply a new limit to the live session at once. The Safety Guardian (`_profile`), the Coach (`_on_hr`) and an
    open incident all read this one `PatientProfile` object; SessionPersistence and LiveState cache no limit."""
    current = get_state().sessions.current
    if current is None or current.patient_id != patient_id:
        return False
    current.profile.hr_limit = hr_limit
    return True


@router.patch("/patients/{patient_id}/limits")
async def set_limits(body: LimitsIn, patient_id: str = Path(pattern=ID_RE, max_length=64),
                     db: Database = Depends(require_db)) -> dict[str, Any]:
    patient = await queries.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if patient["therapist_id"] not in (None, body.therapist_id):
        raise HTTPException(status_code=403, detail="this patient belongs to another therapist")
    before = patient["hr_limit"]
    new_limit = await writes.set_patient_hr_limit(db, patient_id, body.hr_limit, body.therapist_id)
    if new_limit is None:
        raise HTTPException(status_code=503, detail="database unavailable: heart-rate limit not saved")
    await writes.insert_agent_action(db, "therapist", "hr_limit_set", patient_id,
                                     detail={"from": before, "to": new_limit})
    refresh_live_limit(patient_id, new_limit)
    set_by_name = (patient["therapist_name"] if patient["therapist_id"] == body.therapist_id
                   else await queries.therapist_name(db, body.therapist_id))
    get_state().hub.emit("limits_changed", {"patient_id": patient_id, "hr_limit": new_limit,
                                            "set_by_name": set_by_name, "t": round(time.time(), 3)})
    return {"patient_id": patient_id, "hr_limit": new_limit}


@router.patch("/patients/{patient_id}/watch-features")
async def set_watch_features(body: WatchFeaturesIn, patient_id: str = Path(pattern=ID_RE, max_length=64),
                             db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if not await writes.set_tremor_monitoring(db, patient_id, body.tremor_monitoring):
        raise HTTPException(status_code=503, detail="database unavailable: watch features not saved")
    await writes.insert_agent_action(db, "therapist", "watch_features_set", patient_id,
                                     detail={"tremor_monitoring": body.tremor_monitoring})
    get_state().hub.emit("watch_features_changed", {"patient_id": patient_id,
                                                    "tremor_monitoring": body.tremor_monitoring,
                                                    "t": round(time.time(), 3)})
    return {"patient_id": patient_id, "tremor_monitoring": body.tremor_monitoring}


@router.get("/patients/{patient_id}/watch")
async def get_patient_watch(patient_id: str = Path(pattern=ID_RE, max_length=64),
                            db: Database = Depends(require_db)) -> dict[str, Any]:
    if await queries.get_patient(db, patient_id) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    recovery, rotation, tremor = await asyncio.gather(queries.patient_hr_recovery(db, patient_id),
                                                      queries.patient_rotation_sets(db, patient_id),
                                                      queries.patient_tremor_days(db, patient_id, 14))
    return {"patient_id": patient_id, "hr_recovery": recovery, "rotation_sets": rotation, "tremor": tremor}
```

- [ ] **Step 6: Register the routers in `server/main.py`**

Below `from server.api import me as me_api` add:

```python
from server.api import me_watch
from server.api import watch_admin
```

In `create_app()`, directly after `app.include_router(care_api.router)  # routes carry their own require_principal` add:

```python
    app.include_router(me_watch.router)  # routes carry their own require_principal
    app.include_router(watch_admin.router, dependencies=[Depends(require_token)])  # therapist-only
```

- [ ] **Step 7: Samples**

Write each file exactly (2-space JSON, trailing newline):

`server/samples/rest_get_me_watch.json`:
```json
{
  "first_name": "Maya",
  "hr_limit": 120,
  "hr_limit_set_by_name": "Dr. Lee",
  "therapist_name": "Dr. Lee",
  "tremor_monitoring": false,
  "prefs": {"watch_taps": "strong", "watch_arm": "right", "watch_double_tap": true, "watch_captions": true},
  "plan": [
    {"exercise_id": "squat", "name": "Mini squats", "sets": 3, "reps": 12, "done_sets": 2, "watch": false},
    {"exercise_id": "forearm_rotation", "name": "Forearm turns", "sets": 3, "reps": 10, "done_sets": 0, "watch": true, "goal_deg": 80}
  ]
}
```
`server/samples/rest_post_me_tremor_request.json`:
```json
{"days": [{"day": "2026-10-11", "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0, "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}]}
```
`server/samples/rest_post_me_tremor.json`:
```json
{"stored": 1}
```
`server/samples/rest_patch_patient_limits_request.json`:
```json
{"therapist_id": "lee", "hr_limit": 120}
```
`server/samples/rest_patch_patient_limits.json` — byte-identical to `therapist-page` (write it with
`git show therapist-page:server/samples/rest_patch_patient_limits.json > server/samples/rest_patch_patient_limits.json`;
its content is):
```json
{
  "patient_id": "maya",
  "hr_limit": 140
}
```
`server/samples/rest_patch_patient_watch_features_request.json`:
```json
{"tremor_monitoring": true}
```
`server/samples/rest_patch_patient_watch_features.json`:
```json
{"patient_id": "maya", "tremor_monitoring": true}
```
`server/samples/rest_get_patient_watch.json`:
```json
{
  "patient_id": "maya",
  "hr_recovery": [{"session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f", "set_index": 2, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22, "time": "2026-10-11T09:01:00Z"}],
  "rotation_sets": [{"session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f", "exercise_id": "forearm_rotation", "set_index": 1, "angles": [62.0, 82.0], "best_deg": 82.0, "avg_deg": 72.0, "goal_deg": 80.0, "time": "2026-10-11T09:05:00Z"}],
  "tremor": [{"day": "2026-10-11", "none_min": 420, "slight_min": 190, "mild_min": 25, "strong_min": 0, "unknown_min": 60, "dyskinesia": "few", "source": "simulated"}]
}
```

Append to `server/samples/README.md`:

```markdown

Apple Watch (tri-watch, contract `docs/superpowers/plans/2026-10-11-watch-contract.md`): `rest_get_me_watch` is
`GET /me/watch` (the iPhone's `setup` push to the watch); `rest_post_me_tremor_request` / `rest_post_me_tremor` are
`POST /me/tremor` (403 `{"detail":"tremor monitoring off"}` while the therapist hasn't enabled it);
`rest_patch_patient_limits_request` / `rest_patch_patient_limits` are the therapist-page `PATCH /patients/{id}/limits`
contract (the response file is byte-identical to therapist-page's); `rest_patch_patient_watch_features_request` /
`rest_patch_patient_watch_features` and `rest_get_patient_watch` are the therapist's watch routes. Files ending in
`_request` are request bodies; the others are responses.
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_watch_api.py server/tests/test_samples.py server/tests/test_security.py -q`
Expected: PASS (`test_every_rest_route_requires_token_except_public` covers the new routes).

- [ ] **Step 9: Commit**

```bash
git add server/watch_prefs.py server/api/me_watch.py server/api/watch_admin.py server/main.py \
  server/samples/rest_get_me_watch.json server/samples/rest_post_me_tremor_request.json \
  server/samples/rest_post_me_tremor.json server/samples/rest_patch_patient_limits_request.json \
  server/samples/rest_patch_patient_limits.json server/samples/rest_patch_patient_watch_features_request.json \
  server/samples/rest_patch_patient_watch_features.json server/samples/rest_get_patient_watch.json \
  server/samples/README.md server/tests/test_samples.py server/tests/test_watch_api.py
git commit -F - <<'EOF'
Server: watch REST (/me/watch, /me/tremor, limits with live push, watch-features, /patients/{id}/watch)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: WS protocol — resume, `watch_sync`, wrist sanitising + persistence, countdown haptic, filters, samples

**Files:**
- Create: `server/watch_wire.py`, `server/db/wrist.py`
- Modify: `server/db/writer.py` (generalise `FrameWriter`; add wrist COPY), `server/main.py`, `server/state.py`,
  `server/devices.py`, `server/agents/haptics.py`
- Samples (create/overwrite): `server/samples/ws_events_client_watch_button_resume.json`, `ws_events_client_wrist.json`,
  `ws_events_client_watch_sync.json`, `ws_events_haptic.json`, `ws_events_breather.json`, `ws_events_hr_recovery.json`,
  `ws_events_rotation_rep.json`, `ws_events_rotation_set.json`, `ws_events_sync_progress.json`,
  `ws_events_limits_changed.json`, `ws_events_watch_features_changed.json`; modify `server/samples/README.md`
- Tests: create `server/tests/test_watch_ws.py`, `server/tests/test_wrist_persist.py`; modify `server/tests/test_haptics.py`,
  `server/tests/test_samples.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2 except the `wrist_motion.patient_id/accel` columns.
- Produces:
  - `server.watch_wire.sanitize_wrist(msg: dict) -> dict | None`; `sanitize_watch_sync(msg: dict) -> dict | None`
    (returns `{"type": "watch_sync", "t0": float, "dt_ms": float, "accel_mag": list[float]}`).
  - `AppState.clock_sync: Any | None` — set by Task 7; must offer `on_window(msg: dict, session_id: str) -> None`.
  - `main.WATCH_BUTTON_COMMANDS = {"ready": "start", "stop": "stop", "resume": "start"}`; `watch_sync` in
    `CLIENT_EVENT_TYPES` and `PATIENT_CLIENT_EVENT_TYPES`; never broadcast.
  - `server.db.writer`: `FrameWriter(dsn, *, patient_provider=None, max_queue=…, flush_interval_s=…, backoff_max_s=…,
    copy_sql: str = COPY_SQL, row_fn = frame_row, thread_name: str = "frame-writer")`; `WRIST_COPY_SQL`,
    `WRIST_MAX_QUEUE = 3000`, `wrist_row(msg: dict, patient_id: str) -> tuple`.
  - `server.db.wrist.WristRecorder(writer, patient_of: Callable[[str], str | None], *, offset_s: Callable[[str], float |
    None] = …, min_interval_s: float = 0.1, clock=time.monotonic)` with `async on_event(ev)` and a public, assignable
    `offset_s` attribute (Task 7 sets it). In `main.lifespan` the recorder is the local `wrist_recorder` (None without DB).
  - `HapticCoach`: `HAPTIC_PATTERNS = ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather")`;
    emits `countdown_tick` on every `countdown`; ignores a `rep` with `source == "watch"` unless `quality == "good"`.
  - `DEVICE_EVENT_TYPES` gains `"breather"`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_watch_ws.py`:

```python
"""Watch traffic on /ws/events: watch_button resume, watch_sync to ClockSync (not broadcast), wrist sanitising,
and the per-client filters for the new server events."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import main
from server.db.wrist import WristRecorder
from server.db.writer import wrist_row
from server.devices import DEVICE_EVENT_TYPES
from server.state import get_state
from server.tests.conftest import TOKEN
from server.tests.test_ws_safety import read_until
from server.watch_wire import sanitize_watch_sync, sanitize_wrist


# ---- sanitising -------------------------------------------------------------------------------------------------

def test_sanitize_wrist_keeps_known_finite_fields() -> None:
    now = time.time()
    msg = {"type": "wrist", "t": now, "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
           "rotation_deg": 41.54, "extra": "x"}
    assert sanitize_wrist(msg) == {"type": "wrist", "t": now, "quat": [0.99, 0.01, 0.05, 0.02],
                                   "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}


@pytest.mark.parametrize("msg", [
    {"type": "wrist"},
    {"type": "wrist", "rotation_deg": "41"},
    {"type": "wrist", "rotation_deg": 400.0},
    {"type": "wrist", "quat": [1, 0, 0], "accel": [0, 0, "x"]},
    {"type": "wrist", "rotation_deg": True},
    {"type": "wrist", "t": time.time()},  # a time alone carries nothing
])
def test_sanitize_wrist_drops_empty_or_bad(msg: dict[str, Any]) -> None:
    assert sanitize_wrist(msg) is None


def test_sanitize_wrist_drops_an_implausible_time() -> None:
    clean = sanitize_wrist({"type": "wrist", "t": time.time() * 1000, "rotation_deg": 10})
    assert clean == {"type": "wrist", "rotation_deg": 10.0}  # inject() stamps the server time instead


def test_sanitize_watch_sync() -> None:
    ok = {"type": "watch_sync", "t0": 1791662259.0, "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9]}
    assert sanitize_watch_sync(ok) == {"type": "watch_sync", "t0": 1791662259.0, "dt_ms": 20.0,
                                       "accel_mag": [0.1, 0.4, 0.9]}
    for bad in ({**ok, "t0": "x"}, {**ok, "dt_ms": 1}, {**ok, "dt_ms": 500}, {**ok, "accel_mag": []},
                {**ok, "accel_mag": [0.1] * 61}, {**ok, "accel_mag": [0.1, -1.0]}, {**ok, "accel_mag": [0.1, 99]},
                {**ok, "accel_mag": "0.1"}):
        assert sanitize_watch_sync(bad) is None


# ---- routing ------------------------------------------------------------------------------------------------------

def test_watch_sync_is_a_client_and_patient_type() -> None:
    assert "watch_sync" in main.CLIENT_EVENT_TYPES and "watch_sync" in main.PATIENT_CLIENT_EVENT_TYPES
    assert main.WATCH_BUTTON_COMMANDS == {"ready": "start", "stop": "stop", "resume": "start"}


def test_watch_button_resume_sends_start(make_client: Callable[..., TestClient],
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert [(m["name"], m["payload"]) for m in sent] == [("start", {"source": "watch"})]


def test_watch_button_resume_is_blocked_while_the_guardian_holds(make_client: Callable[..., TestClient],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "command", "name": "pain", "payload": {"level": 8}})
        read_until(ws, lambda m: m.get("type") == "alert" and m.get("step") == "detected")
        sent: list[dict[str, Any]] = []
        monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert sent == []


class FakeClockSync:
    def __init__(self) -> None:
        self.windows: list[tuple[dict[str, Any], str]] = []

    def on_window(self, msg: dict[str, Any], session_id: str) -> None:
        self.windows.append((msg, session_id))


def test_watch_sync_goes_to_clock_sync_and_is_not_broadcast(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock")
    state = get_state()
    fake = FakeClockSync()
    state.clock_sync = fake
    window = {"type": "watch_sync", "t0": time.time(), "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9, 1.3, 0.8, 0.2]}
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json(window)
        ws.send_json({"type": "watch_sync", "t0": "bad", "dt_ms": 20, "accel_mag": [0.1]})
        ws.send_json({"type": "watch_button", "name": "marker"})
        seen: list[str] = []
        read_until(ws, lambda m: seen.append(str(m.get("type"))) or m.get("type") == "watch_button")
    assert "watch_sync" not in seen
    assert len(fake.windows) == 1
    msg, sid = fake.windows[0]
    assert msg["accel_mag"] == window["accel_mag"] and sid == state.sessions.current.session_id


def test_wrist_is_sanitised_then_broadcast(make_client: Callable[..., TestClient]) -> None:
    client = make_client(MIRA_SOURCE="mock")
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        ws.send_json({"type": "wrist", "rotation_deg": "nope"})  # dropped
        ws.send_json({"type": "wrist", "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
                      "rotation_deg": 41.5, "junk": 1})
        got = read_until(ws, lambda m: m.get("type") == "wrist")
    assert got["rotation_deg"] == 41.5 and "junk" not in got and got["session_id"]


def test_filters_for_the_new_server_events() -> None:
    assert "breather" in DEVICE_EVENT_TYPES and "hr" in DEVICE_EVENT_TYPES
    assert main._device_accepts({"type": "breather"})
    for kind in ("rotation_rep", "rotation_set", "hr_recovery", "sync_progress", "limits_changed",
                 "watch_features_changed", "haptic"):
        assert not main._device_accepts({"type": kind})
    accept = main._patient_accepts("maya")
    assert accept({"type": "limits_changed", "patient_id": "maya"})
    assert not accept({"type": "limits_changed", "patient_id": "p02"})
    assert not accept({"type": "watch_features_changed", "patient_id": "p02"})


# ---- wrist persistence (unit) ------------------------------------------------------------------------------------

class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def submit(self, frame: dict[str, Any]) -> None:
        self.rows.append(frame)


async def test_wrist_recorder_throttles_per_patient_and_applies_the_offset() -> None:
    now = [100.0]
    writer = FakeWriter()
    owners = {"s1": "maya", "s2": "liam"}
    recorder = WristRecorder(writer, owners.get, clock=lambda: now[0])
    for i in range(5):  # five samples within 50 ms → one row
        await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1000.0 + i / 100, "rotation_deg": i})
    now[0] += 0.2  # (0.1 exactly would be 0.0999… in floating point)
    await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1000.2, "rotation_deg": 9})
    await recorder.on_event({"type": "wrist", "session_id": "s2", "t": 1000.2, "rotation_deg": 3})  # other patient
    await recorder.on_event({"type": "wrist", "session_id": "unknown", "t": 1000.2, "rotation_deg": 3})
    await recorder.on_event({"type": "hr", "session_id": "s1", "t": 1000.2, "bpm": 90})
    assert [(r["patient_id"], r["rotation_deg"]) for r in writer.rows] == [("maya", 0), ("maya", 9), ("liam", 3)]
    recorder.offset_s = lambda sid: 0.25 if sid == "s1" else None
    now[0] += 0.2
    await recorder.on_event({"type": "wrist", "session_id": "s1", "t": 1001.0, "rotation_deg": 1})
    assert writer.rows[-1]["t"] == pytest.approx(1000.75)  # watch clock minus offset = server clock


def test_wrist_row_maps_quat_wxyz_and_accel() -> None:
    row = wrist_row({"t": 1791662259.706, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f",
                     "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}, "maya")
    assert row[1:] == ("maya", "219b6491-aff5-461e-9e5e-39ef73bde60f", 0.99, 0.01, 0.05, 0.02, 41.5,
                       [0.02, -9.79, 0.15])
    bare = wrist_row({"t": 1.0, "session_id": "s", "rotation_deg": 5.0}, "maya")
    assert bare[3:7] == (None, None, None, None) and bare[8] is None
```

Create `server/tests/test_wrist_persist.py`:

```python
"""Wrist samples relayed by the phone land in wrist_motion (patient, rotation, quat, accel), ≤ 10 Hz per patient."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.state import get_state
from server.tests.conftest import TOKEN
from server.tests.dbutil import fresh_database, prepare, ro_dsn


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        yield url


def _rows(dsn: str, sid: str) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT patient_id, rotation_deg, quat_w, accel FROM wrist_motion WHERE session_id = %s",
                            (sid,)).fetchall()


def test_wrist_burst_is_persisted_and_throttled(make_client: Callable[..., TestClient], dsn: str) -> None:
    client = make_client(TIGER_DSN=dsn, COPILOT_DSN=ro_dsn(dsn))
    sid = get_state().sessions.current.session_id
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        for _ in range(10):  # one burst: well under 0.1 s
            ws.send_json({"type": "wrist", "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15],
                          "rotation_deg": 41.5})
        time.sleep(0.3)
    deadline = time.monotonic() + 5
    while not _rows(dsn, sid) and time.monotonic() < deadline:
        time.sleep(0.1)
    rows = _rows(dsn, sid)
    assert 1 <= len(rows) <= 3  # one burst, throttled to ≤ 10 Hz (≤ 3 even if the burst straddles 0.2 s)
    patient, deg, quat_w, accel = rows[0]
    assert patient == "maya" and deg == pytest.approx(41.5) and quat_w == pytest.approx(0.99)
    assert accel == pytest.approx([0.02, -9.79, 0.15])
```

Append to `server/tests/test_haptics.py`:

```python
async def test_countdown_emits_countdown_tick(coach: HapticCoach, hub: FakeHub, live_session: LiveSession) -> None:
    await coach.on_event({"type": "countdown", "n": 3, "session_id": live_session.session_id, "t": 4.0})
    assert hub.events == [{"type": "haptic", "pattern": "countdown_tick", "session_id": live_session.session_id,
                           "t": 4.0}]


async def test_a_short_watch_counted_turn_has_no_haptic(coach: HapticCoach, hub: FakeHub,
                                                        live_session: LiveSession) -> None:
    sid = live_session.session_id
    await coach.on_event({"type": "rep", "quality": "partial", "source": "watch", "session_id": sid, "t": 5.0})
    assert hub.events == []
    await coach.on_event({"type": "rep", "quality": "good", "source": "watch", "session_id": sid, "t": 6.0})
    assert hub.events[0]["pattern"] == "good_rep"


def test_haptic_patterns_include_the_watch_ones() -> None:
    from server.agents.haptics import HAPTIC_PATTERNS

    assert HAPTIC_PATTERNS == ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather")
```

In `server/tests/test_samples.py` replace the two sets with:

```python
SERVER_EVENTS = {"arrived", "greeting", "awaiting_gesture", "gesture", "countdown", "cue", "rep", "set_done",
                 "paused", "resumed", "fatigue", "fall", "fall_status", "alert", "haptic", "summary_ready",
                 "proposal_created", "coach_adapt", "device_health", "device_status", "session_summary", "message",
                 "breather", "hr_recovery", "rotation_rep", "rotation_set", "sync_progress", "limits_changed",
                 "watch_features_changed"}
CLIENT_EVENTS = {"command", "hr", "wrist", "watch_button", "device_health", "watch_sync"}
```

and add this test:

```python
def test_watch_samples_match_the_contract() -> None:
    assert _load("ws_events_client_watch_button_resume") == {"type": "watch_button", "name": "resume"}
    assert "rotation_deg" in _load("ws_events_client_wrist")
    assert _load("ws_events_haptic")["pattern"] == "countdown_tick"
    assert _load("ws_events_breather")["state"] == "start"
    assert set(_load("ws_events_rotation_rep")) >= {"exercise_id", "set_index", "rep", "reps_target", "angle_deg",
                                                    "goal_deg", "reached"}
    assert set(_load("ws_events_rotation_set")) >= {"exercise_id", "set_index", "angles", "best_deg", "avg_deg",
                                                    "goal_deg"}
    assert set(_load("ws_events_hr_recovery")) >= {"set_index", "peak_bpm", "bpm_60s", "drop_bpm"}
    assert set(_load("ws_events_sync_progress")) >= {"progress", "locked"}
    assert set(_load("ws_events_limits_changed")) >= {"patient_id", "hr_limit", "set_by_name"}
    assert set(_load("ws_events_watch_features_changed")) >= {"patient_id", "tremor_monitoring"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_watch_ws.py server/tests/test_wrist_persist.py server/tests/test_haptics.py server/tests/test_samples.py -q`
Expected: FAIL (`ModuleNotFoundError: server.watch_wire`, missing samples, …).

- [ ] **Step 3: `server/watch_wire.py`**

```python
"""Validation of the watch's messages the phone relays on /ws/events (contract: `wrist`, `watch_sync`).
Malformed input is dropped or trimmed here, before the bridge, the agents or the database see it."""

from __future__ import annotations

import math
import time
from typing import Any

MAX_ROTATION_DEG = 360.0
MAX_SYNC_SAMPLES = 60      # ≤ 50 per 1 s window at 50 Hz, with slack
SYNC_DT_MS = (5.0, 100.0)
MAX_ACCEL_G = 16.0
MAX_CLOCK_SKEW_S = 3600.0  # a `t` further than this from the server clock is not trusted


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _vec(value: Any, n: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != n:
        return None
    nums = [_num(v) for v in value]
    return None if any(v is None for v in nums) else nums  # type: ignore[return-value]


def sanitize_wrist(msg: dict[str, Any]) -> dict[str, Any] | None:
    """`{type, t?, quat? [w,x,y,z], accel? [x,y,z], rotation_deg?}` with finite numbers only; None when nothing
    usable is left (a time alone is not a sample)."""
    out: dict[str, Any] = {"type": "wrist"}
    t = _num(msg.get("t"))
    if t is not None and abs(t - time.time()) <= MAX_CLOCK_SKEW_S:
        out["t"] = t
    quat, accel, deg = _vec(msg.get("quat"), 4), _vec(msg.get("accel"), 3), _num(msg.get("rotation_deg"))
    if quat is not None:
        out["quat"] = quat
    if accel is not None:
        out["accel"] = accel
    if deg is not None and abs(deg) <= MAX_ROTATION_DEG:
        out["rotation_deg"] = round(deg, 1)
    return out if set(out) - {"type", "t"} else None


def sanitize_watch_sync(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One sync-wave window: `t0` (watch clock of sample 0), `dt_ms` and 1..60 user-acceleration magnitudes in g."""
    t0, dt = _num(msg.get("t0")), _num(msg.get("dt_ms"))
    mags = msg.get("accel_mag")
    if t0 is None or dt is None or not SYNC_DT_MS[0] <= dt <= SYNC_DT_MS[1]:
        return None
    if not isinstance(mags, list) or not 1 <= len(mags) <= MAX_SYNC_SAMPLES:
        return None
    values = [_num(m) for m in mags]
    if any(v is None or v < 0 or v > MAX_ACCEL_G for v in values):
        return None
    return {"type": "watch_sync", "t0": t0, "dt_ms": dt, "accel_mag": values}
```

- [ ] **Step 4: Generalise `FrameWriter` and add the wrist COPY (`server/db/writer.py`)**

(a) Below `ANGLES = (...)` add:

```python
WRIST_COLUMNS = ("time", "patient_id", "session_id", "quat_w", "quat_x", "quat_y", "quat_z", "rotation_deg", "accel")
WRIST_COPY_SQL = f"COPY wrist_motion ({', '.join(WRIST_COLUMNS)}) FROM STDIN"
WRIST_MAX_QUEUE = 3000  # 5 minutes at 10 Hz
```

(b) Below `frame_row` add:

```python
def wrist_row(msg: dict[str, Any], patient_id: str) -> tuple[Any, ...]:
    """A sanitised `wrist` event → a wrist_motion row (quat is [w, x, y, z])."""
    quat = msg.get("quat") or [None, None, None, None]
    return (datetime.fromtimestamp(float(msg["t"]), UTC), patient_id, msg["session_id"], quat[0], quat[1], quat[2],
            quat[3], msg.get("rotation_deg"), msg.get("accel"))
```

(c) `FrameWriter.__init__` signature becomes:

```python
    def __init__(self, dsn: str, *, patient_provider: Callable[[], str | None] | None = None,
                 max_queue: int = MAX_QUEUE, flush_interval_s: float = FLUSH_INTERVAL_S,
                 backoff_max_s: float = BACKOFF_MAX_S, copy_sql: str = COPY_SQL,
                 row_fn: Callable[[dict[str, Any], str], tuple[Any, ...]] = frame_row,
                 thread_name: str = "frame-writer") -> None:
```
and add to its body: `self._copy_sql = copy_sql`, `self._row_fn = row_fn`, `self._thread_name = thread_name`.
In `start()` use `name=self._thread_name`. In `_flush()` replace `rows.append(frame_row(frame, patient_id))` with
`rows.append(self._row_fn(frame, patient_id))` and `cur.copy(COPY_SQL)` with `cur.copy(self._copy_sql)`.

- [ ] **Step 5: `server/db/wrist.py`**

```python
"""Wrist samples (the watch's motion relayed by the phone, or the mock's MOCK_WRIST stream) → wrist_motion through a
COPY-mode FrameWriter. At most one row per `min_interval_s` (0.1 s = 10 Hz) per patient. Once ClockSync has locked
the watch's clock offset for the session, sample times are moved onto the server clock."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

WRIST_MIN_INTERVAL_S = 0.1


def _no_offset(_session_id: str) -> float | None:
    return None


class WristRecorder:
    def __init__(self, writer: Any, patient_of: Callable[[str], str | None], *,
                 offset_s: Callable[[str], float | None] = _no_offset,
                 min_interval_s: float = WRIST_MIN_INTERVAL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.writer = writer
        self.patient_of = patient_of
        self.offset_s = offset_s  # assignable: main wires ClockSync.offset_s once it exists
        self.min_interval_s = min_interval_s
        self.clock = clock
        self._last: dict[str, float] = {}
        self.throttled = 0

    async def on_event(self, ev: dict[str, Any]) -> None:
        if ev.get("type") != "wrist":
            return
        sid, t = ev.get("session_id"), ev.get("t")
        if not isinstance(sid, str) or not isinstance(t, (int, float)) or isinstance(t, bool):
            return
        patient_id = self.patient_of(sid)
        if patient_id is None:
            return
        now = self.clock()
        last = self._last.get(patient_id)
        if last is not None and now - last < self.min_interval_s:
            self.throttled += 1
            return
        self._last[patient_id] = now
        offset = self.offset_s(sid)
        self.writer.submit({**ev, "patient_id": patient_id, "t": float(t) - offset if offset is not None else float(t)})
```

- [ ] **Step 6: `server/main.py`**

(a) Imports: add `from server import watch_wire`, `from server.db.wrist import WristRecorder`, and extend the writer
import to `from server.db.writer import WRIST_COPY_SQL, WRIST_MAX_QUEUE, FrameWriter, wrist_row`.

(b) Replace the two type sets:

```python
CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "device_health", "watch_sync"})

# A patient's phone may steer the session and relay its watch; it is never the LCD.
PATIENT_CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "watch_sync"})
```

(c) Replace `WATCH_BUTTON_COMMANDS` and `_on_client_event` with:

```python
#: watch_button name → the session command it maps to (unknown names are ignored). `resume` (the watch's Stopped
#: screen) is a `start`: the engine resumes a paused session on `start`.
WATCH_BUTTON_COMMANDS = {"ready": "start", "stop": "stop", "resume": "start"}


def _on_client_event(msg: dict[str, Any]) -> None:
    kind = msg.get("type")
    if kind not in CLIENT_EVENT_TYPES or len(msg) > MAX_CLIENT_MSG_KEYS:
        log.debug("dropping client event", extra={"event_type": str(kind)[:40]})
        return
    state = get_state()
    if kind == "watch_sync":
        _on_watch_sync(state, msg)
        return
    if kind == "wrist":
        clean = watch_wire.sanitize_wrist(msg)
        if clean is None:
            return
        msg = clean
    state.bridge.inject(msg)
    if kind == "command" and msg.get("name") in SESSION_COMMANDS:
        _forward_session_command(state, msg)
    elif kind == "watch_button":
        name = WATCH_BUTTON_COMMANDS.get(msg.get("name"))
        if name:
            _forward_session_command(state, {"type": "command", "name": name, "payload": {"source": "watch"}})


def _on_watch_sync(state: AppState, msg: dict[str, Any]) -> None:
    """A sync-wave window goes straight to ClockSync (never broadcast: ~50 numbers a second nobody else needs).
    Without a current session there is nothing to store the offset on, so the window is dropped."""
    clean = watch_wire.sanitize_watch_sync(msg)
    sync = state.clock_sync
    current = state.sessions.current
    if clean is None or sync is None or current is None:
        return
    sync.on_window(clean, current.session_id)
```

(d) In `lifespan`, directly after `writer: FrameWriter | None = None` add:

```python
    wrist_writer: FrameWriter | None = None
    wrist_recorder: WristRecorder | None = None
```

and inside the `if db is not None and persistence is not None and cfg.TIGER_DSN:` block, after `bridge.subscribe_events(persistence.on_event)` add:

```python
        wrist_writer = FrameWriter(cfg.TIGER_DSN, patient_provider=current_patient_id, copy_sql=WRIST_COPY_SQL,
                                   row_fn=wrist_row, max_queue=WRIST_MAX_QUEUE, thread_name="wrist-writer")
        wrist_writer.start()

        def patient_of(session_id: str) -> str | None:
            found = sessions.get(session_id)
            return found.patient_id if found is not None else None

        wrist_recorder = WristRecorder(wrist_writer, patient_of)
        bridge.subscribe_events(wrist_recorder.on_event)
```

In the shutdown `finally`, directly after `await asyncio.to_thread(writer.stop)`'s `if` block add:

```python
        if wrist_writer is not None:
            await asyncio.to_thread(wrist_writer.stop)
```

- [ ] **Step 7: `AppState`, devices, haptics**

`server/state.py`, in `AppState.__init__` after `self.devices: Any = None` add:

```python
        self.clock_sync: Any | None = None  # server.agents.clock_sync.ClockSync: receives watch_sync windows
```

`server/devices.py`: add `"breather"` to `DEVICE_EVENT_TYPES` (the LCD may show the breather; `hr` is already there).

Replace `server/agents/haptics.py` with:

```python
"""HapticCoach: turns engine events into haptic cues for the watch (§7, Apple Watch spec).

- `good_rep` / `fix_form` from `rep` (quality "good" or not). A rep counted from the watch (`source: "watch"`, by
  RotationCoach) only buzzes when it reached the goal: a short turn is not a form fault.
- `set_done` from `set_done`; `countdown_tick` from every `countdown` (the watch's 3-2-1).
- `check_in` and `breather` are documented here but emitted by the Safety Guardian itself (never duplicated).
"""

from __future__ import annotations

from typing import Any

from server.hub import Hub
from server.state import get_state

HAPTIC_PATTERNS = ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather")


class HapticCoach:
    """Subscribes to engine events; emits a `haptic` event only while a session is live."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "rep":
            good = ev.get("quality") == "good"
            if ev.get("source") == "watch" and not good:
                return
            pattern = "good_rep" if good else "fix_form"
        elif kind == "set_done":
            pattern = "set_done"
        elif kind == "countdown":
            pattern = "countdown_tick"
        else:
            return
        if not self._session_live():
            return
        self.hub.emit("haptic", {"pattern": pattern, "session_id": ev.get("session_id"), "t": ev.get("t")})

    @staticmethod
    def _session_live() -> bool:
        try:
            state = get_state()
        except RuntimeError:
            return False
        return state.sessions.current is not None
```

- [ ] **Step 8: WS samples**

Write (2-space indented JSON, trailing newline) with exactly the contract's fields:

- `ws_events_client_watch_button_resume.json`: `{"type": "watch_button", "name": "resume"}`
- `ws_events_client_wrist.json` (overwrite): `{"type": "wrist", "t": 1791662259.706, "quat": [0.99, 0.01, 0.05, 0.02], "accel": [0.02, -9.79, 0.15], "rotation_deg": 41.5}`
- `ws_events_client_watch_sync.json`: `{"type": "watch_sync", "t0": 1791662259.000, "dt_ms": 20, "accel_mag": [0.1, 0.4, 0.9, 1.3, 0.8, 0.2]}`
- `ws_events_haptic.json` (overwrite): `{"type": "haptic", "pattern": "countdown_tick", "seq": 1022, "t": 1791662259.706, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_breather.json`: `{"type": "breather", "state": "start", "bpm": 128, "limit": 120, "seq": 1100, "t": 1791662300.1, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_hr_recovery.json`: `{"type": "hr_recovery", "set_index": 2, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22, "seq": 1200, "t": 1791662360.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_rotation_rep.json`: `{"type": "rotation_rep", "exercise_id": "forearm_rotation", "set_index": 1, "rep": 4, "reps_target": 10, "angle_deg": 68.0, "goal_deg": 80.0, "reached": false, "seq": 1300, "t": 1791662400.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_rotation_set.json`: `{"type": "rotation_set", "exercise_id": "forearm_rotation", "set_index": 1, "angles": [62.0, 70.5, 78.0, 82.0, 75.0], "best_deg": 82.0, "avg_deg": 73.5, "goal_deg": 80.0, "seq": 1310, "t": 1791662460.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_sync_progress.json`: `{"type": "sync_progress", "progress": 0.6, "locked": false, "seq": 1400, "t": 1791662250.0, "session_id": "219b6491-aff5-461e-9e5e-39ef73bde60f"}`
- `ws_events_limits_changed.json`: `{"type": "limits_changed", "patient_id": "maya", "hr_limit": 120, "set_by_name": "Dr. Lee", "seq": 1500, "t": 1791662000.0}`
- `ws_events_watch_features_changed.json`: `{"type": "watch_features_changed", "patient_id": "maya", "tremor_monitoring": true, "seq": 1501, "t": 1791662001.0}`

Append to `server/samples/README.md`:

```markdown

Apple Watch events: `ws_events_client_watch_button_resume`, `ws_events_client_wrist` (now with `rotation_deg`) and
`ws_events_client_watch_sync` are what the phone sends for the watch; `ws_events_haptic` shows the new
`countdown_tick` pattern; `ws_events_breather`, `ws_events_hr_recovery`, `ws_events_rotation_rep`,
`ws_events_rotation_set`, `ws_events_sync_progress`, `ws_events_limits_changed` and `ws_events_watch_features_changed`
are the server events. These are emitted with `Hub.emit`, so on the wire `seq` may be absent — treat it as optional.
`watch_sync` is never broadcast back.
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_watch_ws.py server/tests/test_wrist_persist.py server/tests/test_haptics.py server/tests/test_samples.py server/tests/test_watch_button.py server/tests/test_ws_safety.py server/tests/test_db_resilience.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add server/watch_wire.py server/db/wrist.py server/db/writer.py server/main.py server/state.py \
  server/devices.py server/agents/haptics.py server/samples/ws_events_client_watch_button_resume.json \
  server/samples/ws_events_client_wrist.json server/samples/ws_events_client_watch_sync.json \
  server/samples/ws_events_haptic.json server/samples/ws_events_breather.json server/samples/ws_events_hr_recovery.json \
  server/samples/ws_events_rotation_rep.json server/samples/ws_events_rotation_set.json \
  server/samples/ws_events_sync_progress.json server/samples/ws_events_limits_changed.json \
  server/samples/ws_events_watch_features_changed.json server/samples/README.md server/tests/test_watch_ws.py \
  server/tests/test_wrist_persist.py server/tests/test_haptics.py server/tests/test_samples.py
git commit -F - <<'EOF'
Server: watch WS protocol (resume, watch_sync, sanitised wrist + wrist_motion writer, countdown_tick, filters)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: Safety Guardian breather (HR pause, auto-resume, escalation coexistence)

**Files:**
- Modify: `server/agents/safety_guardian.py`, `server/main.py` (`_forward_session_command`, one `on_stop` line)
- Test: create `server/tests/test_breather.py` (existing `test_guardian.py` / `test_ws_safety.py` must still pass unchanged)

**Interfaces:**
- Consumes: Task 3's `breather` haptic pattern name, `DEVICE_EVENT_TYPES` containing `breather`.
- Produces:
  - Constants `BREATHER_RESUME_S = 10.0`, `BREATHER_MARGIN_BPM = 5`; dataclass `Breather(session_id, patient_id, bpm,
    limit, below_since=None)`; `SafetyGuardian.breathers: dict[str, Breather]`.
  - `SafetyGuardian.holding(session_id: str | None) -> bool` (open incident **or** breather);
    `SafetyGuardian.end_breather(key: str, resolution: str) -> None`; `SafetyGuardian.on_session_stop(session) -> None`.
  - Events: `breather {state:"start", bpm, limit, session_id, t}`, `breather {state:"end", bpm, limit, resolution,
    session_id, t}` with resolution `hr_recovered | escalated | stopped`; `haptic {pattern:"breather"}`; engine commands
    `{"type":"command","name":"pause"|"start","payload":{"source":"guardian"},"session_id":…}`; therapist notification
    kind `heart_rate`, severity `high`, dedupe `breather:<alert_id>`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_breather.py`:

```python
"""Breather = the HR incident seen from the watch: start (pause + haptic + therapist note), auto-resume when HR stays
under limit - 5 for 10 s before escalation, no auto-resume after escalation, end on stop."""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import engine_bridge
from server.agents.safety_guardian import BREATHER_RESUME_S, SafetyGuardian
from server.state import SessionRegistry, get_state
from server.tests.conftest import TOKEN
from server.tests.test_guardian import FakeBridge, FakeHub, FakeNotifier, ev, until
from server.tests.test_ws_safety import read_until


async def _guardian(timeout_s: float = 60.0) -> tuple[SafetyGuardian, FakeHub, FakeBridge, FakeNotifier, str,
                                                        list[str]]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")  # default profile: hr_limit 130, therapist okafor
    hub, bridge, notifier = FakeHub(), FakeBridge(), FakeNotifier()
    steps: list[str] = []
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, timeout_s,
                              alert_hooks=[lambda inc, step, payload: steps.append(step)])
    return guardian, hub, bridge, notifier, session.session_id, steps


async def _hr(guardian: SafetyGuardian, sid: str, t0: float, seconds: int, bpm: int) -> float:
    """One reading per second for `seconds` seconds starting at t0; returns the next free time."""
    for i in range(seconds):
        await guardian.on_event(ev(sid, "hr", bpm=bpm, t=t0 + i))
    return t0 + seconds


async def _open(guardian: SafetyGuardian, sid: str) -> float:
    return await _hr(guardian, sid, time.time(), 6, 140)  # 0..5 s above 130 → incident at the 6th reading


def _commands(bridge: FakeBridge) -> list[str]:
    return [m["name"] for m in bridge.to_engine if m.get("type") == "command"]


async def test_hr_incident_starts_a_breather() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        await _open(guardian, sid)
        [start] = hub.of("breather")
        assert (start["state"], start["bpm"], start["limit"], start["session_id"]) == ("start", 140, 130, sid)
        assert "breather" in [h["pattern"] for h in hub.of("haptic")]
        assert _commands(bridge) == ["pause"] and bridge.to_engine[0]["payload"] == {"source": "guardian"}
        assert guardian.holding(sid) and steps[:2] == ["detected", "checked_in"]
    finally:
        await guardian.stop()


async def test_recovery_for_10s_resumes_and_resolves_the_incident() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        t = await _open(guardian, sid)
        inc = guardian.incidents[sid]
        await _hr(guardian, sid, t, int(BREATHER_RESUME_S) + 1, 120)  # 120 < 130 - 5 for 10 s
        assert sid not in guardian.incidents and sid not in guardian.breathers and not guardian.holding(sid)
        assert inc.state == "resolved" and "resolved" in steps
        assert _commands(bridge) == ["pause", "start"]
        end = hub.of("breather")[-1]
        assert (end["state"], end["resolution"], end["bpm"], end["limit"]) == ("end", "hr_recovered", 120, 130)
        await until(lambda: inc.timer is not None and inc.timer.done())
    finally:
        await guardian.stop()


async def test_only_a_sustained_drop_below_limit_minus_5_counts() -> None:
    guardian, hub, bridge, _, sid, _ = await _guardian()
    try:
        t = await _open(guardian, sid)
        t = await _hr(guardian, sid, t, 15, 125)  # exactly limit - 5: not below
        t = await _hr(guardian, sid, t, 9, 110)   # 9 s below …
        t = await _hr(guardian, sid, t, 1, 128)   # … interrupted: the clock restarts
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
        await _hr(guardian, sid, t, 11, 110)
        assert sid not in guardian.breathers and _commands(bridge) == ["pause", "start"]
        assert [b["state"] for b in hub.of("breather")] == ["start", "end"]
    finally:
        await guardian.stop()


async def test_after_escalation_there_is_no_auto_resume_until_the_incident_closes() -> None:
    guardian, hub, bridge, notifier, sid, _ = await _guardian(timeout_s=0.05)
    try:
        t = await _open(guardian, sid)
        inc = guardian.incidents[sid]
        await until(lambda: inc.state == "escalated" and inc.escalation is not None)
        await _hr(guardian, sid, t, 15, 100)
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
        await guardian.close(inc)
        end = hub.of("breather")[-1]
        assert (end["state"], end["resolution"]) == ("end", "escalated")
        assert _commands(bridge) == ["pause"]  # the patient resumes herself (watch Resume) once it is closed
        assert not guardian.holding(sid) and len(notifier.escalations) == 1
    finally:
        await guardian.stop()


async def test_im_ok_resolves_the_alert_but_the_breather_holds_until_hr_recovers() -> None:
    guardian, hub, bridge, _, sid, steps = await _guardian()
    try:
        t = await _open(guardian, sid)
        await guardian.on_event(ev(sid, "command", name="im_ok", payload={}, t=t))
        assert sid not in guardian.incidents and steps[-1] == "resolved"
        assert guardian.holding(sid) and _commands(bridge) == ["pause"]
        await _hr(guardian, sid, t + 1, 11, 110)
        assert not guardian.holding(sid) and _commands(bridge) == ["pause", "start"]
        assert hub.of("breather")[-1]["resolution"] == "hr_recovered"
    finally:
        await guardian.stop()


async def test_hr_recovery_never_resolves_a_different_incident() -> None:
    guardian, _, bridge, _, sid, _ = await _guardian()
    try:
        t = await _open(guardian, sid)
        await guardian.on_event(ev(sid, "command", name="im_ok", payload={}, t=t))
        await guardian.on_event(ev(sid, "command", name="pain", payload={"level": 8}, t=t))
        pain = guardian.incidents[sid]
        await _hr(guardian, sid, t + 1, 12, 110)
        assert guardian.incidents.get(sid) is pain and pain.state == "checking_in"
        assert sid in guardian.breathers and _commands(bridge) == ["pause"]
    finally:
        await guardian.stop()


async def test_stopped_event_and_session_stop_end_the_breather() -> None:
    guardian, hub, _, _, sid, _ = await _guardian()
    try:
        await _open(guardian, sid)
        await guardian.on_event(ev(sid, "stopped"))
        assert hub.of("breather")[-1]["resolution"] == "stopped" and sid not in guardian.breathers
        guardian.incidents.clear()
        guardian.hr_over_since.clear()
        await _open(guardian, sid)
        guardian.on_session_stop(guardian.sessions.get(sid))
        assert hub.of("breather")[-1]["resolution"] == "stopped" and sid not in guardian.breathers
    finally:
        await guardian.stop()


async def test_breather_notifies_the_therapist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def create(*args: Any) -> int:
        calls.append(args)
        return 1

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    guardian, _, _, _, sid, _ = await _guardian()
    try:
        await _open(guardian, sid)
        await until(lambda: bool(calls))
        kind, severity, therapist_id, patient_id, title, body, *_rest, dedupe = calls[0]
        assert (kind, severity, therapist_id, patient_id) == ("heart_rate", "high", "okafor", "maya")
        assert "140" in body and "130" in body and dedupe.startswith("breather:")
    finally:
        await guardian.stop()


def test_whole_app_breather_blocks_watch_resume(make_client: Callable[..., TestClient],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_bridge.MockSource, "_heart_rate", lambda self, pose: 150)  # the mock agrees
    client = make_client(MIRA_SOURCE="mock", CHECKIN_TIMEOUT_S="60")
    now = time.time()
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        for i in range(6):
            ws.send_json({"type": "hr", "bpm": 150, "t": now + i})
        start = read_until(ws, lambda m: m.get("type") == "breather")
        assert start["state"] == "start" and start["limit"] == 130
        sent: list[dict[str, Any]] = []
        monkeypatch.setattr(get_state().bridge, "send_to_engine", sent.append)
        ws.send_json({"type": "watch_button", "name": "resume"})
        time.sleep(0.2)
    assert sent == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_breather.py -q`
Expected: FAIL (`ImportError: cannot import name 'BREATHER_RESUME_S'`).

- [ ] **Step 3: Implement the breather in `server/agents/safety_guardian.py`**

(a) Under `RETELL_STEPS = (...)` add:

```python
BREATHER_RESUME_S = 10.0   # HR under (limit - BREATHER_MARGIN_BPM) this long ends a pre-escalation breather
BREATHER_MARGIN_BPM = 5
BREATHER_HAPTIC = "breather"
```

(b) After the `Incident` dataclass add:

```python
@dataclass(eq=False)
class Breather:
    """The watch's "Let's take a breather" screen: the HR incident's pause, from the patient's side. It outlives an
    "I'm OK" (the alert is resolved, the heart rate is still high) and ends on recovery, after an escalated incident
    closes, or when the session stops."""

    session_id: str | None
    patient_id: str
    bpm: int
    limit: int
    below_since: float | None = None
```

(c) In `__init__` after `self.last_hr: dict[str, int] = {}` add `self.breathers: dict[str, Breather] = {}`.

(d) In `on_event`, add before the `fall_status` branch:

```python
            elif kind == "stopped":
                self.end_breather(_session_key(ev), "stopped")
```

(e) At the end of `on_hr` (after the `if bpm > …: … else: …` block) add:

```python
        breather = self.breathers.get(key)
        if breather is not None:
            await self._breather_hr(key, breather, int(bpm), t)
```

(f) In `start()`, directly after the `if pause: self.bridge.inject(...)` lines add:

```python
        if kind == "hr":
            self._start_breather(inc)
```

(g) In `close()`, after `self._end(inc)` add:

```python
        if inc.kind == "hr":
            self.end_breather(str(inc.session_id or ""), "escalated")
```

(h) Add these methods (e.g. after `_hr_update`):

```python
    # ---- breather (Apple Watch) ------------------------------------------------------------------------

    def holding(self, session_id: str | None) -> bool:
        """The Guardian, not the patient, decides when this session resumes (open incident or breather)."""
        key = str(session_id or "")
        return key in self.incidents or key in self.breathers

    def _start_breather(self, inc: Incident) -> None:
        key = str(inc.session_id or "")
        if key in self.breathers:  # a second HR incident while the first breather still runs
            return
        bpm, limit = int(inc.hr_now or 0), int(inc.profile.hr_limit)
        self.breathers[key] = Breather(inc.session_id, inc.patient_id, bpm, limit)
        self.bridge.send_to_engine({"type": "command", "name": "pause", "payload": {"source": "guardian"},
                                    "session_id": inc.session_id})
        now = round(time.time(), 3)
        self.hub.emit("breather", {"state": "start", "bpm": bpm, "limit": limit, "session_id": inc.session_id,
                                   "t": now})
        self.hub.emit("haptic", {"pattern": BREATHER_HAPTIC, "session_id": inc.session_id, "t": now})
        self._spawn(self._notify_breather(inc, bpm, limit), name=f"guardian-breather-{inc.alert_id}")

    async def _notify_breather(self, inc: Incident, bpm: int, limit: int) -> None:
        therapist_id = inc.profile.therapist_id
        if not therapist_id:
            return
        try:
            from server.notify import notifications

            await notifications.create("heart_rate", "high", therapist_id, inc.patient_id,
                                       f"{inc.first_name}'s heart rate went over the limit",
                                       f"{bpm} bpm (limit {limit}). Mira paused the session for a breather.",
                                       f"/dashboard#/patients/{inc.patient_id}", "alerts", inc.alert_id,
                                       f"breather:{inc.alert_id}")
        except Exception as exc:  # noqa: BLE001 - a notification must never break the safety path
            log.warning("breather notification failed", extra={"alert_id": inc.alert_id, "error": repr(exc)})

    async def _breather_hr(self, key: str, br: Breather, bpm: int, t: float) -> None:
        br.bpm = bpm
        br.limit = int(self._profile(key).hr_limit)  # a limit changed mid-breather applies at once
        if bpm >= br.limit - BREATHER_MARGIN_BPM:
            br.below_since = None
            return
        if br.below_since is None:
            br.below_since = t
        if t - br.below_since < BREATHER_RESUME_S:
            return
        inc = self.incidents.get(key)
        if inc is not None and (inc.kind != "hr" or inc.state != "checking_in"):
            return  # escalated (the call decides; closing ends the breather) or another kind of incident
        if inc is not None:
            if inc.timer is not None:
                inc.timer.cancel()
            inc.state = "resolved"
            await self.log(inc, "resolved", payload={"resolution": "hr_recovered", "bpm": bpm})
            self._end(inc)
        self.bridge.send_to_engine({"type": "command", "name": "start", "payload": {"source": "guardian"},
                                    "session_id": br.session_id})
        self.end_breather(key, "hr_recovered")

    def end_breather(self, key: str, resolution: str) -> None:
        br = self.breathers.pop(key, None)
        if br is None:
            return
        self.hub.emit("breather", {"state": "end", "bpm": br.bpm, "limit": br.limit, "resolution": resolution,
                                   "session_id": br.session_id, "t": round(time.time(), 3)})

    def on_session_stop(self, session: Any) -> None:
        """SessionRegistry.on_stop hook (REST stop, last set, shutdown): the engine's own `stopped` may arrive after
        the session is no longer current, without its session_id."""
        self.end_breather(str(getattr(session, "session_id", "") or ""), "stopped")
```

- [ ] **Step 4: `server/main.py`**

In `_forward_session_command`, replace `session_id in guardian.incidents` with `guardian.holding(session_id)` and update
the docstring's last sentence to "(a pain/HR incident, or a breather)". In `lifespan`, directly after
`state.guardian, state.notifier = guardian, notifier` add:

```python
    sessions.on_stop.append(guardian.on_session_stop)  # a stopped session ends its breather
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_breather.py server/tests/test_guardian.py server/tests/test_ws_safety.py server/tests/test_watch_button.py server/tests/test_watch_ws.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/agents/safety_guardian.py server/main.py server/tests/test_breather.py
git commit -F - <<'EOF'
Server: Guardian breather (HR pause, auto-resume before escalation, therapist note, holds watch resume)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: RecoveryAgent (heart-rate recovery after each set)

**Files:**
- Create: `server/agents/recovery.py`, `server/tests/test_recovery.py`
- Modify: `server/main.py` (wiring after the HapticCoach lines; shutdown)

**Interfaces:**
- Consumes (Task 1): `writes.upsert_hr_recovery(db, session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm)`, `writes.fire`.
- Produces: `server.agents.recovery.RecoveryAgent(hub, sessions, db_provider=_default_db, *, recovery_s: float = 60.0)`
  with `async on_event(ev)`, `async close()`, `async drain()`; constant `RECOVERY_S = 60.0`; event
  `hr_recovery {set_index, peak_bpm, bpm_60s, drop_bpm, session_id, t}`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_recovery.py`:

```python
"""RecoveryAgent: peak HR over a set (countdown → set_done), then 60 s later — or at the next set's countdown,
whichever comes first — the latest reading: one hr_recovery event and one hr_recovery row per set."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server.agents import recovery as recovery_mod
from server.agents.recovery import RecoveryAgent
from server.state import SessionRegistry


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


async def _setup(recovery_s: float, db: Any = None) -> tuple[RecoveryAgent, FakeHub, str]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    hub = FakeHub()
    return RecoveryAgent(hub, sessions, db_provider=lambda: db, recovery_s=recovery_s), hub, session.session_id


def ev(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": sid, "t": 1.0, **fields}


async def _set(agent: RecoveryAgent, sid: str, set_no: int, bpms: list[int]) -> None:
    for n in (3, 2, 1):
        await agent.on_event(ev(sid, "countdown", n=n, set=set_no))
    for bpm in bpms:
        await agent.on_event(ev(sid, "hr", bpm=bpm))
    await agent.on_event(ev(sid, "set_done", set=set_no))


async def test_recovery_after_the_delay_uses_the_latest_reading() -> None:
    agent, hub, sid = await _setup(0.05)
    try:
        await agent.on_event(ev(sid, "hr", bpm=150))  # before the set: not part of the peak
        await _set(agent, sid, 1, [110, 126, 120])
        await agent.on_event(ev(sid, "hr", bpm=112))
        await agent.on_event(ev(sid, "hr", bpm=104))
        assert hub.of("hr_recovery") == []
        await asyncio.sleep(0.15)
        [rec] = hub.of("hr_recovery")
        assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (1, 126, 104, 22)
        assert rec["session_id"] == sid
    finally:
        await agent.close()


async def test_next_set_start_comes_first() -> None:
    agent, hub, sid = await _setup(60.0)
    try:
        await _set(agent, sid, 1, [118, 124])
        await agent.on_event(ev(sid, "hr", bpm=108))
        await agent.on_event(ev(sid, "countdown", n=3, set=2))
        [rec] = hub.of("hr_recovery")
        assert (rec["set_index"], rec["peak_bpm"], rec["bpm_60s"], rec["drop_bpm"]) == (1, 124, 108, 16)
        await agent.on_event(ev(sid, "hr", bpm=130))  # set 2 is now running
        await agent.on_event(ev(sid, "set_done", set=2))
        await agent.on_event(ev(sid, "countdown", n=3, set=3))
        assert [r["set_index"] for r in hub.of("hr_recovery")] == [1, 2]
        assert hub.of("hr_recovery")[1]["peak_bpm"] == 130
    finally:
        await agent.close()


async def test_no_heart_rate_during_the_set_means_no_recovery() -> None:
    agent, hub, sid = await _setup(0.01)
    try:
        await _set(agent, sid, 1, [])
        await agent.on_event(ev(sid, "hr", bpm=100))
        await asyncio.sleep(0.05)
        assert hub.of("hr_recovery") == []
    finally:
        await agent.close()


async def test_recovery_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[Any, ...]] = []

    async def fake_upsert(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(recovery_mod.writes, "upsert_hr_recovery", fake_upsert)
    db = object()
    agent, hub, sid = await _setup(60.0, db=db)
    try:
        await _set(agent, sid, 1, [126])
        await agent.on_event(ev(sid, "hr", bpm=104))
        await agent.on_event(ev(sid, "countdown", n=3, set=2))
        for _ in range(3):
            await asyncio.sleep(0)
        assert written == [(db, sid, "maya", 1, 126, 104, 22)]
    finally:
        await agent.close()


async def test_close_cancels_the_pending_timer() -> None:
    agent, hub, sid = await _setup(0.05)
    await _set(agent, sid, 1, [120])
    await agent.close()
    await asyncio.sleep(0.1)
    assert hub.of("hr_recovery") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_recovery.py -q`
Expected: FAIL (`ModuleNotFoundError: server.agents.recovery`).

- [ ] **Step 3: Implement `server/agents/recovery.py`**

```python
"""RecoveryAgent: heart-rate recovery after each set (the watch's Recovery screen: "Down 22 in 1 min").

The peak is the highest `hr` between a set's first `countdown` and its `set_done`. RECOVERY_S after `set_done` — or at
the next set's first `countdown`, whichever comes first — the latest reading is the recovery value: one `hr_recovery`
event `{set_index, peak_bpm, bpm_60s, drop_bpm}` and one `hr_recovery` row (fire-and-forget). No reading during the set
means no recovery for it. Nothing here waits on the database.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from server.db import writes
from server.logging_setup import get_logger
from server.state import SessionRegistry

log = get_logger("recovery")

RECOVERY_S = 60.0
MAX_TRACKED_SESSIONS = 8


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


@dataclass(eq=False)
class _Pending:
    set_index: int
    peak: int
    task: asyncio.Task[None] | None = None


@dataclass
class _Track:
    in_set: bool = False
    peak: int | None = None
    last_bpm: int | None = None
    pending: _Pending | None = None


def _bpm(ev: dict[str, Any]) -> int | None:
    value = ev.get("bpm")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class RecoveryAgent:
    def __init__(self, hub: Any, sessions: SessionRegistry, db_provider: Callable[[], Any | None] = _default_db, *,
                 recovery_s: float = RECOVERY_S) -> None:
        self.hub = hub
        self.sessions = sessions
        self.db_provider = db_provider
        self.recovery_s = recovery_s
        self._tracks: dict[str, _Track] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    async def on_event(self, ev: dict[str, Any]) -> None:
        sid = ev.get("session_id")
        if not isinstance(sid, str):
            return
        kind = ev.get("type")
        try:
            if kind == "hr":
                bpm = _bpm(ev)
                if bpm is None:
                    return
                track = self._track(sid)
                track.last_bpm = bpm
                if track.in_set:
                    track.peak = bpm if track.peak is None else max(track.peak, bpm)
            elif kind == "countdown":
                track = self._track(sid)
                if not track.in_set:  # the first number of the next set's 3-2-1
                    self._complete(sid, track)
                    track.in_set, track.peak = True, None
            elif kind == "set_done":
                track = self._tracks.get(sid)
                if track is None or not track.in_set:
                    return
                track.in_set = False
                if track.peak is None:
                    return
                self._complete(sid, track)  # never two pending sets
                set_index = ev.get("set")
                pending = _Pending(int(set_index) if isinstance(set_index, int) else 0, track.peak)
                track.pending = pending
                pending.task = self._spawn(self._after_delay(sid, track, pending))
        except Exception:  # noqa: BLE001 - one bad event must never stop the agent
            log.exception("recovery failed on event", extra={"event_type": kind, "session_id": sid})

    def _track(self, sid: str) -> _Track:
        track = self._tracks.get(sid)
        if track is None:
            while len(self._tracks) >= MAX_TRACKED_SESSIONS:
                oldest = next(k for k in self._tracks)
                self._complete(oldest, self._tracks.pop(oldest))
            track = self._tracks[sid] = _Track()
        return track

    async def _after_delay(self, sid: str, track: _Track, pending: _Pending) -> None:
        await asyncio.sleep(self.recovery_s)
        if track.pending is pending:
            self._complete(sid, track)

    def _complete(self, sid: str, track: _Track) -> None:
        pending = track.pending
        if pending is None:
            return
        track.pending = None
        if pending.task is not None and pending.task is not asyncio.current_task():
            pending.task.cancel()
        bpm = track.last_bpm
        if bpm is None:
            return
        drop = pending.peak - bpm
        self.hub.emit("hr_recovery", {"set_index": pending.set_index, "peak_bpm": pending.peak, "bpm_60s": bpm,
                                      "drop_bpm": drop, "session_id": sid, "t": round(time.time(), 3)})
        db = self.db_provider()
        session = self.sessions.get(sid)
        if db is not None and session is not None:
            writes.fire(writes.upsert_hr_recovery(db, sid, session.patient_id, pending.set_index, pending.peak, bpm,
                                                  drop))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for track in self._tracks.values():
            track.pending = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
```

- [ ] **Step 4: Wire it in `server/main.py`**

Import `from server.agents.recovery import RecoveryAgent`. In `lifespan`, directly after
`bridge.subscribe_events(haptics.on_event)` add:

```python
    recovery = RecoveryAgent(hub, sessions)
    bridge.subscribe_events(recovery.on_event)
```

In the shutdown `finally`, directly after `await coach.close()` add `await recovery.close()`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_recovery.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/agents/recovery.py server/main.py server/tests/test_recovery.py
git commit -F - <<'EOF'
Server: RecoveryAgent (hr_recovery event + row: peak over the set, reading 60 s later or at the next set)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: RotationCoach + mock rotation program (MOCK_WRIST) + elbow drift

**Files:**
- Create: `server/agents/rotation.py`, `server/tests/test_rotation.py`
- Modify: `server/engine_bridge.py` (schedule per exercise, rotation body, mock wrist stream, `notify_session_start`,
  `LiveState` main angle), `server/config.py` (`MOCK_WRIST`), `.env.example`, `server/db/persist.py`
  (`QUALITY_SCORES`), `server/db/seed.py` (Maya's forearm-turns prescription), `server/main.py`
- Modify tests: `server/tests/test_api_db.py:144`, `server/tests/test_pairing.py:60` (prescription sets gain
  `"forearm_rotation"`)

**Interfaces:**
- Consumes: Task 1 `exercises.is_watch_exercise`, `exercises.rotation_goal_deg`, `writes.upsert_rotation_set`,
  `queries.ro_prescriptions`; Task 2 `watch_prefs.watch_arm`; Task 3 HapticCoach's `source == "watch"` rule.
- Produces:
  - `server.agents.rotation`: `ZERO_ZONE_DEG = 15.0`, `MIN_GOAL_FRACTION = 0.6`, `DRIFT_M = 0.08`, `DRIFT_HOLD_S = 0.5`,
    `ELBOW_CUE_ID = "elbow_drift"`, `ELBOW_CUE_TEXT = "Keep your elbow at your side"`;
    `class RepCounter(goal_deg: float)` with `update(deg: float) -> float | None` (returns the rep's peak |angle|);
    `elbow_drift_m(joints: list, arm: str) -> float | None`;
    `class RotationCoach(hub, bridge, sessions, db_provider=_default_db)` with `async on_event(ev)`, `on_frame(frame)`.
  - Events: `rotation_rep {exercise_id, set_index, rep, reps_target, angle_deg, goal_deg, reached, session_id, t}`,
    `rotation_set {exercise_id, set_index, angles, best_deg, avg_deg, goal_deg, session_id, t}`; injected `rep`
    `{index, set, exercise_id, quality: "good"|"partial", flags, peak_angle, rom, source: "watch"}`; injected `cue`
    `{id: "elbow_drift", text}` + `haptic fix_form`.
  - `server.engine_bridge`: `MockSource(..., exercise_id: str = "squat")`; `_program_schedule(exercise_id: str = "squat")`;
    `_ProgramPose.frac: float = 0.0`; `mock_rotation_deg(pose: _ProgramPose) -> float`;
    `rotation_frame_body(drift_m: float, rng) -> dict`; `_ROTATION_PEAKS`; `EngineBridge.notify_session_start(patient_id:
    str, exercise_id: str | None = None)`; `SESSION_START` message gains `exercise_id`.
  - `cfg.MOCK_WRIST: bool = False`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_rotation.py`:

```python
"""RotationCoach: reps from the watch's rotation_deg (excursion ≥ 60 % of the goal, then back within 15° of zero),
rotation_rep/rotation_set events and rows, rep events for the rest of the system, elbow drift from pose keypoints;
and the mock rotation program (MOCK_WRIST=1) running end to end without a watch."""

from __future__ import annotations

import random
from typing import Any

import pytest

from server import engine_bridge
from server.agents import rotation as rotation_mod
from server.agents.rotation import ELBOW_CUE_ID, RepCounter, RotationCoach, elbow_drift_m
from server.config import reload_config
from server.engine_bridge import LiveState
from server.state import SessionRegistry


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]


class FakeBridge:
    def __init__(self) -> None:
        self.injected: list[dict[str, Any]] = []

    def inject(self, msg: dict[str, Any]) -> None:
        self.injected.append(msg)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.injected if m["type"] == kind]


async def _coach(exercise_id: str = "forearm_rotation", db: Any = None) -> tuple[RotationCoach, FakeHub, FakeBridge,
                                                                                    str]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", exercise_id)
    hub, bridge = FakeHub(), FakeBridge()
    return RotationCoach(hub, bridge, sessions, db_provider=lambda: db), hub, bridge, session.session_id


def ev(sid: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": sid, "t": fields.pop("t", 1.0), **fields}


async def _turn(coach: RotationCoach, sid: str, peak: float) -> None:
    for deg in (0, 10, peak / 2, peak, peak / 2, 10, 0):
        await coach.on_event(ev(sid, "wrist", rotation_deg=deg))


async def _start_set(coach: RotationCoach, sid: str, set_no: int = 1) -> None:
    for n in (3, 2, 1):
        await coach.on_event(ev(sid, "countdown", n=n, set=set_no, reps_target=10, exercise_id="forearm_rotation"))


# ---- pure parts ---------------------------------------------------------------------------------------------------

def test_rep_counter_hysteresis() -> None:
    counter = RepCounter(80.0)
    assert [counter.update(d) for d in (0, 10, 30, 47, 30, 10)] == [None] * 6  # 47 < 60 % of 80: no rep
    out = [counter.update(d) for d in (0, 20, 60, 82, 40, 16)]
    assert out == [None] * 6  # 16 is still outside the 15° zero zone
    assert counter.update(15) == 82  # back within 15° of zero: one rep at its peak
    assert counter.update(-70) is None and counter.update(-5) == 70  # signed turns count by magnitude


def test_elbow_drift_is_horizontal_distance_from_the_shoulder() -> None:
    joints = [[0.0, 0.0, 0.0] for _ in range(33)]
    joints[12], joints[14] = [-0.19, 1.40, 0.0], [-0.25, 1.13, 0.08]
    assert elbow_drift_m(joints, "right") == pytest.approx(0.1)
    joints[11], joints[13] = [0.19, 1.40, 0.0], [0.19, 1.13, 0.0]
    assert elbow_drift_m(joints, "left") == pytest.approx(0.0)
    assert elbow_drift_m([], "right") is None


# ---- the coach ----------------------------------------------------------------------------------------------------

async def test_reps_sets_and_rep_events() -> None:
    coach, hub, bridge, sid = await _coach()
    await _turn(coach, sid, 70.0)  # before the set: ignored
    await _start_set(coach, sid)
    await _turn(coach, sid, 62.0)
    await _turn(coach, sid, 82.0)
    await _turn(coach, sid, 30.0)  # too small: not a rep
    await coach.on_event(ev(sid, "set_done", set=1))
    reps = hub.of("rotation_rep")
    assert [(r["rep"], r["angle_deg"], r["reached"], r["reps_target"], r["set_index"]) for r in reps] == [
        (1, 62.0, False, 10, 1), (2, 82.0, True, 10, 1)]
    assert reps[0]["goal_deg"] == 80.0 and reps[0]["exercise_id"] == "forearm_rotation"
    [summary] = hub.of("rotation_set")
    assert summary["angles"] == [62.0, 82.0] and summary["best_deg"] == 82.0 and summary["avg_deg"] == 72.0
    assert summary["set_index"] == 1 and summary["goal_deg"] == 80.0
    injected = bridge.of("rep")
    assert [(r["index"], r["set"], r["quality"], r["source"], r["peak_angle"]) for r in injected] == [
        (1, 1, "partial", "watch", 62.0), (2, 1, "good", "watch", 82.0)]


async def test_paused_turns_do_not_count_and_watch_reps_are_ignored() -> None:
    coach, hub, _, sid = await _coach()
    await _start_set(coach, sid)
    await coach.on_event(ev(sid, "paused", reason="hr"))
    await _turn(coach, sid, 80.0)
    await coach.on_event(ev(sid, "resumed"))
    await coach.on_event(ev(sid, "rep", index=9, quality="good", source="watch"))
    await _turn(coach, sid, 80.0)
    assert [r["rep"] for r in hub.of("rotation_rep")] == [1]


async def test_non_watch_exercises_are_ignored() -> None:
    coach, hub, bridge, sid = await _coach("squat")
    await _start_set(coach, sid)
    await _turn(coach, sid, 80.0)
    await coach.on_event(ev(sid, "set_done", set=1))
    assert hub.events == [] and bridge.injected == []


async def test_rotation_set_is_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    written: list[tuple[Any, ...]] = []

    async def fake(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(rotation_mod.writes, "upsert_rotation_set", fake)
    db = object()
    coach, _, _, sid = await _coach(db=db)

    async def no_rx(*_: Any) -> list[dict[str, Any]]:
        return [{"exercise_id": "forearm_rotation", "reps": 10, "targets": {"rotation_deg": 70}}]

    monkeypatch.setattr(rotation_mod.queries, "ro_prescriptions", no_rx)
    coach._states.clear()
    await _start_set(coach, sid)
    await _turn(coach, sid, 72.0)
    await coach.on_event(ev(sid, "set_done", set=1))
    for _ in range(3):
        await asyncio.sleep(0)
    assert written == [(db, sid, "maya", "forearm_rotation", 1, [72.0], 72.0, 72.0, 70.0)]


def _frame(sid: str, t: float, drift: float) -> dict[str, Any]:
    body = engine_bridge.rotation_frame_body(drift, random.Random(1))
    return {"type": "frame", "session_id": sid, "t": t, **body}


async def test_elbow_drift_cues_once_and_flags_the_next_rep() -> None:
    coach, hub, bridge, sid = await _coach()
    await _start_set(coach, sid)
    t = 100.0
    for i in range(30):  # 1 s at 30 Hz with the elbow 12 cm out
        coach.on_frame(_frame(sid, t + i / 30, 0.12))
    cues = bridge.of("cue")
    assert [(c["id"], c["text"]) for c in cues] == [(ELBOW_CUE_ID, "Keep your elbow at your side")]
    assert [h["pattern"] for h in hub.of("haptic")] == ["fix_form"]
    for i in range(30):  # back at the side, then out again → a second cue
        coach.on_frame(_frame(sid, t + 1 + i / 30, 0.0))
    for i in range(20):
        coach.on_frame(_frame(sid, t + 2 + i / 30, 0.12))
    assert len(bridge.of("cue")) == 2
    await _turn(coach, sid, 80.0)
    await _turn(coach, sid, 80.0)
    reps = bridge.of("rep")
    assert reps[0]["flags"] == [ELBOW_CUE_ID] and reps[1]["flags"] == []


async def test_a_short_lift_does_not_cue() -> None:
    coach, _, bridge, sid = await _coach()
    await _start_set(coach, sid)
    for i in range(12):  # 0.4 s
        coach.on_frame(_frame(sid, 100 + i / 30, 0.12))
    coach.on_frame(_frame(sid, 100.5, 0.0))
    assert bridge.of("cue") == []


# ---- the mock program -------------------------------------------------------------------------------------------

def test_rotation_schedule_has_no_engine_reps() -> None:
    schedule = [e for _, e in engine_bridge._program_schedule("forearm_rotation")]
    assert not [e for e in schedule if e["type"] == "rep"]
    assert [e["set"] for e in schedule if e["type"] == "set_done"] == [1, 2, 3]
    assert {e["exercise_id"] for e in schedule if e["type"] == "countdown"} == {"forearm_rotation"}
    squat = [e for _, e in engine_bridge._program_schedule()]
    assert len([e for e in squat if e["type"] == "rep"]) == 30  # the default program is unchanged


async def test_mock_rotation_program_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_WRIST", "1")
    reload_config()
    emitted: list[dict[str, Any]] = []

    class Inbox:
        def get_nowait(self) -> Any:
            import queue

            raise queue.Empty

    class Stop:
        def __init__(self) -> None:
            self.n = 0

        def is_set(self) -> bool:
            self.n += 1
            return self.n > int((engine_bridge._PROGRAM_S + 40) * engine_bridge.FRAME_HZ)

        def wait(self, timeout: float | None = None) -> bool:
            return False

    src = engine_bridge.MockSource(speed=1e9, exercise_id="forearm_rotation")
    src._arrive_due_s = 0.0
    monkeypatch.setattr(engine_bridge, "MOCK_GREETING_HOLD_S", 0.0)
    monkeypatch.setattr(engine_bridge, "MOCK_GESTURE_AFTER_S", 0.0)
    src.run(emitted.append, Inbox(), Stop())

    assert not [e for e in emitted if e["type"] == "rep"]
    assert [e for e in emitted if e["type"] == "wrist"]
    coach, hub, bridge, sid = await _coach()
    for msg in emitted:
        msg["session_id"] = sid
        if msg["type"] == "frame":
            coach.on_frame(msg)
        else:
            await coach.on_event(msg)
    sets = hub.of("rotation_set")
    assert [s["set_index"] for s in sets] == [1, 2, 3]
    for s in sets:
        assert len(s["angles"]) == 10
        for got, peak in zip(s["angles"], engine_bridge._ROTATION_PEAKS, strict=True):
            assert peak - 3.0 <= got <= peak + 0.05
    assert [c["id"] for c in bridge.of("cue")] == [ELBOW_CUE_ID] * 3
    reps = bridge.of("rep")
    assert len(reps) == 30 and reps[5]["flags"] == [ELBOW_CUE_ID]


def test_live_state_main_angle_follows_the_watch_for_rotation() -> None:
    live = LiveState()
    live.on_session_start("Maya", "forearm_rotation")
    live.on_frame({"type": "frame", "exercise_id": "forearm_rotation", "angles": {"knee_l": 5.0, "knee_r": 6.0}})
    assert live.main_angle is None
    live.on_event({"type": "wrist", "rotation_deg": -41.5})
    assert live.main_angle == 41.5
    live.on_session_start("Maya", "squat")
    live.on_event({"type": "wrist", "rotation_deg": 30.0})
    live.on_frame({"type": "frame", "exercise_id": "squat", "angles": {"knee_l": 50.0, "knee_r": 60.0}})
    assert live.main_angle == 60.0
```

Update `server/tests/test_api_db.py:144` and `server/tests/test_pairing.py:60` expected sets to
`{"squat", "glute_bridge", "single_leg_stance", "forearm_rotation"}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_rotation.py -q`
Expected: FAIL (`ModuleNotFoundError: server.agents.rotation`).

- [ ] **Step 3: Config, persistence quality, seed**

`server/config.py`, after `MOCK_LOOP: bool = False …` add:

```python
    MOCK_WRIST: bool = False  # mock mode: synthesise the watch's wrist rotation for rotation exercises (no watch)
```

`.env.example`, after the `MOCK_LOOP=0` line add:

```
MOCK_WRIST=0                     # 1 = the mock synthesises Apple Watch wrist rotation for forearm/shoulder/wrist turns
```

`server/db/persist.py`: `QUALITY_SCORES = {"good": 1.0, "fix_form": 0.5, "partial": 0.5, "poor": 0.0}` (a watch rep short
of the goal).

`server/db/seed.py`: add below `PRESCRIPTIONS`:

```python
# Maya's Apple Watch exercise (forearm turns; the goal is the watch-measured twist in degrees).
WATCH_PRESCRIPTIONS: list[tuple[str, int, int, dict[str, Any]]] = [
    ("forearm_rotation", 3, 10, {"rotation_deg": 80}),
]
```

change `ensure_prescriptions` to take the list:

```python
def ensure_prescriptions(conn: psycopg.Connection[Any], patient_id: str,
                         prescriptions: list[tuple[str, int, int, dict[str, Any]]] | None = None) -> None:
    """Version 1 of each exercise, only if the patient has no version yet (later versions are never touched)."""
    rows = prescriptions if prescriptions is not None else PRESCRIPTIONS
    for exercise_id, sets, reps, targets in rows:
```
(loop body unchanged) and in `seed()` after `ensure_prescriptions(conn, MAYA_ID)` add
`ensure_prescriptions(conn, MAYA_ID, WATCH_PRESCRIPTIONS)`.

- [ ] **Step 4: Mock rotation program in `server/engine_bridge.py`**

(a) Below `_EXERCISE = "squat"` add:

```python
_ROTATION_PEAKS = (62.0, 70.5, 78.0, 82.0, 75.0, 85.0, 80.0, 72.0, 88.0, 79.0)  # the mock watch's turn per rep
_ELBOW_DRIFT_REP = 6       # rotation program: the elbow lifts off the side during this rep of every set
_ELBOW_DRIFT_M = 0.12
_WRIST_EVERY_TICKS = 3     # 30 Hz frames / 3 = 10 Hz wrist samples (MOCK_WRIST=1)
```

(b) Replace `_program_schedule` with:

```python
def _program_schedule(exercise_id: str = _EXERCISE) -> list[tuple[float, Message]]:
    """Events of one loop of the mock program, as (seconds from loop start, payload). A watch (rotation) exercise has
    the same countdown/set lifecycle but no engine reps: RotationCoach counts those from the wrist stream."""
    watch = exercises.is_watch_exercise(exercise_id)
    events: list[tuple[float, Message]] = []
    for set_no in range(1, _SETS + 1):
        base = (set_no - 1) * _SET_S
        for i, n in enumerate((3, 2, 1)):
            events.append((base + i, {"type": "countdown", "n": n, "set": set_no, "reps_target": _REPS,
                                      "sets_target": _SETS, "exercise_id": exercise_id}))
        start = base + _COUNTDOWN_S
        events.append((start, {"type": "cue", "id": "set_start", "text": "Nice and slow. Let's begin.",
                               "audio_url": "/audio/set_start.mp3"}))
        if not watch:
            for rep in range(1, _REPS + 1):
                caved = rep == _KNEE_CAVE_REP
                if caved:
                    events.append((start + (rep - 0.5) * _REP_S,
                                   {"type": "cue", "id": "knees_out", "text": "Push your knees out over your toes.",
                                    "audio_url": "/audio/knees_out.mp3"}))
                events.append((start + rep * _REP_S, {
                    "type": "rep", "index": rep, "set": set_no, "exercise_id": exercise_id,
                    "quality": "fix_form" if caved else "good", "flags": ["knee_cave_l"] if caved else []}))
        done: Message = {"type": "set_done", "set": set_no, "exercise_id": exercise_id}
        if not watch:
            done["reps"] = _REPS
        events.append((start + _REPS * _REP_S, done))
    return sorted(events, key=lambda e: e[0])
```

(c) `_ProgramPose` gains a last field `frac: float = 0.0  # 0..1 through the current rep`, and the active return in
`_program_pose` becomes:

```python
    return _ProgramPose(set_no, done, depth, 0.03 if done + 1 == _KNEE_CAVE_REP else 0.0, True, r - done)
```

(d) After `_program_pose` add:

```python
def mock_rotation_deg(pose: _ProgramPose) -> float:
    """The mock watch's twist (MOCK_WRIST=1): 0 → this rep's peak → 0 over one rep (sin² shaped)."""
    if not pose.active:
        return 0.0
    peak = _ROTATION_PEAKS[pose.rep % len(_ROTATION_PEAKS)]
    return round(peak * math.sin(math.pi * pose.frac) ** 2, 1)


def _mock_elbow_drift(pose: _ProgramPose) -> float:
    return _ELBOW_DRIFT_M if pose.active and pose.rep + 1 == _ELBOW_DRIFT_REP and 0.25 <= pose.frac <= 0.75 else 0.0


def rotation_frame_body(drift_m: float, rng: random.Random) -> Message:
    """Standing, right elbow at the side and bent 90° (forearm forward): the rotation exercises' start position.
    `drift_m` moves the right elbow out to the side (horizontal distance from the right shoulder)."""
    body = squat_frame_body(0.0, 0.0, 0.0, rng)
    joints = body["joints"]
    shoulder = joints[12]
    elbow = [round(shoulder[0] - drift_m, 4), round(shoulder[1] - _UPPER_ARM, 4), shoulder[2]]
    joints[14] = elbow
    joints[16] = [elbow[0], elbow[1], round(elbow[2] - _FOREARM, 4)]
    return body
```

(e) `MockSource`:
- `__init__` gains a keyword `exercise_id: str = _EXERCISE`; set `self._exercise = exercise_id`,
  `self._schedule = _program_schedule(exercise_id)` (replacing the existing `_program_schedule()` line) and `self._tick = 0`.
- Add the property:
  ```python
      @property
      def _watch(self) -> bool:
          return exercises.is_watch_exercise(self._exercise)
  ```
- In `run`: the `arrived` event uses `"exercise_id": self._exercise`; the paused/stopped branch body becomes
  `body = rotation_frame_body(0.0, self._rng) if self._watch else squat_frame_body(0.0, 0.0, 0.0, self._rng)`; the normal
  branch body becomes
  ```python
                  pose = _program_pose(self._program_s)
                  if self._watch:
                      body = rotation_frame_body(_mock_elbow_drift(pose) if self._ready else 0.0, self._rng)
                  else:
                      body = squat_frame_body(pose.depth, pose.depth * 0.9, pose.knee_in, self._rng)
  ```
  the frame uses `"exercise_id": self._exercise`, and directly after the `for ev in events: self._emit(emit, ev)` loop add:
  ```python
              if (self._watch and cfg.MOCK_WRIST and self._fall is None and self._ready and not self._paused
                      and not self._stopped and pose.active and self._tick % _WRIST_EVERY_TICKS == 0):
                  self._emit(emit, {"type": "wrist", "rotation_deg": mock_rotation_deg(pose)})
              self._tick += 1
  ```
- `_drain`: `self._restart_session(msg.get("patient_id"), msg.get("exercise_id"))`.
- `_restart_session(self, patient_id: str | None, exercise_id: str | None = None)`: add
  `self._exercise = exercise_id or _EXERCISE` and `self._schedule = _program_schedule(self._exercise)`.
- `_skip_set` and `_start_fall` use `self._exercise` instead of `_EXERCISE`.

(f) `EngineBridge.notify_session_start`:

```python
    def notify_session_start(self, patient_id: str, exercise_id: str | None = None) -> None:
        """A new live session started (SessionRegistry.on_start hook). In mock modes, restart the mock program for this
        patient and exercise (a watch exercise runs the rotation program); live/replay sources are unaffected."""
        if self.source.startswith("mock"):
            self.send_to_engine({"type": SESSION_START, "patient_id": patient_id, "exercise_id": exercise_id})
```

(g) `LiveState`: replace `on_frame` with

```python
    def on_frame(self, frame: Message) -> None:
        self.exercise_id = frame.get("exercise_id", self.exercise_id)
        if not exercises.is_watch_exercise(self.exercise_id):  # rotation exercises: the watch's turn is the angle
            angles = frame.get("angles") or {}
            knees = [a for a in (angles.get("knee_l"), angles.get("knee_r")) if isinstance(a, (int, float))]
            self.main_angle = round(max(knees), 1) if knees else None  # deepest bend (flexion)
        self.load_l_pct = frame.get("load_l_pct")
        self.load_conf = frame.get("confidence_avg")
        self.flags = list(frame.get("flags") or [])
        self.set = frame.get("set_no", self.set)
```

and in `on_event`, add before the `elif kind == "hr"` branch:

```python
        elif (kind == "wrist" and exercises.is_watch_exercise(self.exercise_id)
              and isinstance(ev.get("rotation_deg"), (int, float)) and not isinstance(ev.get("rotation_deg"), bool)):
            self.main_angle = round(abs(float(ev["rotation_deg"])), 1)
```

- [ ] **Step 5: `server/agents/rotation.py`**

```python
"""RotationCoach: the Apple Watch rotation exercises (forearm turns, shoulder turns, wrist bends).

Active only for a session whose exercise is a watch exercise. While a set runs (from the countdown's "1" or the
`set_start` cue, until `set_done`; not while paused) it counts reps from the watch's `wrist.rotation_deg` stream:
a rep is an excursion of at least MIN_GOAL_FRACTION of the goal that comes back within ZERO_ZONE_DEG of zero. Each rep
→ a `rotation_rep` event and a `rep` event injected for everyone else (persistence, Coach, Voice, LiveState,
HapticCoach: `source: "watch"`, quality "good" when the goal was reached, else "partial"). `set_done` → a
`rotation_set` event and a rotation_sets row.

Elbow drift (pose frames): the watch-arm elbow more than DRIFT_M (horizontally) from the same-side shoulder for more
than DRIFT_HOLD_S → a `cue` "Keep your elbow at your side" + `haptic fix_form`, once until the elbow is back
(< DRIFT_REARM_M); the next counted rep carries the `elbow_drift` flag (a form_events row via persistence).
The goal is the prescription's `targets.rotation_deg` (read once per session as copilot_ro), default 80°.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from server import exercises
from server.db import queries, writes
from server.logging_setup import get_logger
from server.state import SessionRegistry
from server.watch_prefs import watch_arm

log = get_logger("rotation")

ZERO_ZONE_DEG = 15.0
MIN_GOAL_FRACTION = 0.6
DRIFT_M = 0.08
DRIFT_HOLD_S = 0.5
DRIFT_REARM_M = 0.06
PRESCRIPTION_TIMEOUT_S = 1.5
ELBOW_CUE_ID = "elbow_drift"
ELBOW_CUE_TEXT = "Keep your elbow at your side"
ARM_JOINTS = {"left": (11, 13), "right": (12, 14)}  # BlazePose (shoulder, elbow) per side
MAX_SKIPPED = 200


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


class RepCounter:
    """Hysteresis over |rotation_deg|: leaving the zero zone starts an excursion, coming back ends it; the excursion
    is a rep when its peak reached MIN_GOAL_FRACTION of the goal."""

    def __init__(self, goal_deg: float) -> None:
        self.goal_deg = goal_deg
        self._out = False
        self._peak = 0.0

    def update(self, deg: float) -> float | None:
        a = abs(deg)
        if not self._out:
            if a > ZERO_ZONE_DEG:
                self._out, self._peak = True, a
            return None
        self._peak = max(self._peak, a)
        if a > ZERO_ZONE_DEG:
            return None
        peak, self._out, self._peak = self._peak, False, 0.0
        return peak if peak >= MIN_GOAL_FRACTION * self.goal_deg else None


def elbow_drift_m(joints: Any, arm: str) -> float | None:
    """Horizontal (x/z) distance in metres between the watch-arm elbow and the same-side shoulder."""
    shoulder_i, elbow_i = ARM_JOINTS.get(arm, ARM_JOINTS["right"])
    try:
        s, e = joints[shoulder_i], joints[elbow_i]
        return math.hypot(float(e[0]) - float(s[0]), float(e[2]) - float(s[2]))
    except (IndexError, TypeError, ValueError, KeyError):
        return None


@dataclass
class _RotState:
    session_id: str
    patient_id: str
    exercise_id: str
    goal_deg: float
    arm: str
    reps_target: int | None = None
    set_index: int = 0
    in_set: bool = False
    paused: bool = False
    rep: int = 0
    angles: list[float] = field(default_factory=list)
    flags_next: list[str] = field(default_factory=list)
    drift_since: float | None = None
    drift_armed: bool = True
    counter: RepCounter = field(init=False)

    def __post_init__(self) -> None:
        self.counter = RepCounter(self.goal_deg)

    @property
    def counting(self) -> bool:
        return self.in_set and not self.paused

    def new_set(self, set_index: int) -> None:
        self.set_index, self.rep, self.angles, self.flags_next = set_index, 0, [], []
        self.counter = RepCounter(self.goal_deg)
        self.in_set = False


class RotationCoach:
    def __init__(self, hub: Any, bridge: Any, sessions: SessionRegistry,
                 db_provider: Callable[[], Any | None] = _default_db) -> None:
        self.hub = hub
        self.bridge = bridge
        self.sessions = sessions
        self.db_provider = db_provider
        self._states: dict[str, _RotState] = {}
        self._skip: set[str] = set()

    # ---- events (bridge.subscribe_events) -------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        sid = ev.get("session_id")
        kind = ev.get("type")
        if not isinstance(sid, str) or sid in self._skip or (kind == "rep" and ev.get("source") == "watch"):
            return
        try:
            st = self._states.get(sid) or await self._open(sid, ev)
            if st is None:
                return
            if kind == "countdown":
                self._on_countdown(st, ev)
            elif kind == "cue" and ev.get("id") == "set_start":
                st.in_set = True
            elif kind == "paused":
                st.paused = True
            elif kind == "resumed":
                st.paused = False
            elif kind == "wrist":
                self._on_wrist(st, ev)
            elif kind == "set_done":
                self._on_set_done(st, ev)
            elif kind == "stopped":
                st.in_set = False
        except Exception:  # noqa: BLE001 - one bad event must never stop the coach
            log.exception("rotation coach failed on event", extra={"event_type": kind, "session_id": sid})

    async def _open(self, sid: str, ev: dict[str, Any]) -> _RotState | None:
        session = self.sessions.get(sid)
        exercise_id = session.exercise_id if session is not None else ev.get("exercise_id")
        if session is None:
            return None  # not a session we know (yet)
        if not exercises.is_watch_exercise(exercise_id):
            if len(self._skip) >= MAX_SKIPPED:
                self._skip.clear()
            self._skip.add(sid)
            return None
        goal, reps = await self._prescription(session.patient_id, str(exercise_id))
        self._states.clear()  # one live session at a time
        st = self._states[sid] = _RotState(sid, session.patient_id, str(exercise_id), goal,
                                           watch_arm(session.profile.device_prefs), reps_target=reps)
        return st

    async def _prescription(self, patient_id: str, exercise_id: str) -> tuple[float, int | None]:
        db = self.db_provider()
        if db is None:
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        try:
            rows = await asyncio.wait_for(queries.ro_prescriptions(db, patient_id, PRESCRIPTION_TIMEOUT_S),
                                          PRESCRIPTION_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a missing/slow prescription → the default goal
            log.info("rotation: default goal", extra={"patient_id": patient_id, "error": repr(exc)})
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        row = next((r for r in rows if r.get("exercise_id") == exercise_id), None)
        if row is None:
            return exercises.DEFAULT_ROTATION_GOAL_DEG, None
        reps = row.get("reps")
        return exercises.rotation_goal_deg(row.get("targets")), int(reps) if isinstance(reps, int) else None

    def _on_countdown(self, st: _RotState, ev: dict[str, Any]) -> None:
        set_no = ev.get("set")
        set_index = set_no if isinstance(set_no, int) else max(st.set_index, 1)
        if set_index != st.set_index:
            st.new_set(set_index)
        if isinstance(ev.get("reps_target"), int):
            st.reps_target = ev["reps_target"]
        if ev.get("n") == 1:
            st.in_set = True

    def _on_wrist(self, st: _RotState, ev: dict[str, Any]) -> None:
        deg = _number(ev.get("rotation_deg"))
        if deg is None or not st.counting:
            return
        peak = st.counter.update(deg)
        if peak is not None:
            self._rep(st, peak, ev.get("t"))

    def _rep(self, st: _RotState, peak: float, t: Any) -> None:
        st.rep += 1
        angle = round(peak, 1)
        reached = angle >= st.goal_deg
        st.angles.append(angle)
        when = _number(t) or round(time.time(), 3)
        self.hub.emit("rotation_rep", {"exercise_id": st.exercise_id, "set_index": st.set_index, "rep": st.rep,
                                       "reps_target": st.reps_target, "angle_deg": angle, "goal_deg": st.goal_deg,
                                       "reached": reached, "session_id": st.session_id, "t": when})
        flags, st.flags_next = st.flags_next, []
        self.bridge.inject({"type": "rep", "index": st.rep, "set": st.set_index, "exercise_id": st.exercise_id,
                            "quality": "good" if reached else "partial", "flags": flags, "peak_angle": angle,
                            "rom": angle, "source": "watch", "session_id": st.session_id, "t": when})

    def _on_set_done(self, st: _RotState, ev: dict[str, Any]) -> None:
        st.in_set = False
        set_no = ev.get("set") if isinstance(ev.get("set"), int) else st.set_index
        if st.angles:
            angles = list(st.angles)
            best = max(angles)
            avg = round(sum(angles) / len(angles), 1)
            self.hub.emit("rotation_set", {"exercise_id": st.exercise_id, "set_index": set_no, "angles": angles,
                                           "best_deg": best, "avg_deg": avg, "goal_deg": st.goal_deg,
                                           "session_id": st.session_id, "t": _number(ev.get("t")) or time.time()})
            db = self.db_provider()
            if db is not None:
                writes.fire(writes.upsert_rotation_set(db, st.session_id, st.patient_id, st.exercise_id, set_no,
                                                       angles, best, avg, st.goal_deg))
        st.new_set(set_no)

    # ---- frames (bridge.subscribe_frames; sync, cheap) ------------------------------------------------------

    def on_frame(self, frame: dict[str, Any]) -> None:
        sid = frame.get("session_id")
        st = self._states.get(sid) if isinstance(sid, str) else None
        if st is None:
            return
        t = _number(frame.get("t"))
        drift = elbow_drift_m(frame.get("joints"), st.arm)
        if not st.counting or t is None or drift is None:
            st.drift_since = None
            return
        if drift <= DRIFT_M:
            st.drift_since = None
            if drift < DRIFT_REARM_M:
                st.drift_armed = True
            return
        if st.drift_since is None:
            st.drift_since = t
        elif st.drift_armed and t - st.drift_since >= DRIFT_HOLD_S:
            st.drift_armed = False
            self._drift(st, t)

    def _drift(self, st: _RotState, t: float) -> None:
        self.bridge.inject({"type": "cue", "id": ELBOW_CUE_ID, "text": ELBOW_CUE_TEXT, "session_id": st.session_id,
                            "t": t})
        self.hub.emit("haptic", {"pattern": "fix_form", "session_id": st.session_id, "t": t})
        if ELBOW_CUE_ID not in st.flags_next:
            st.flags_next.append(ELBOW_CUE_ID)
```

Note on `test_rotation_set_is_persisted`: the test clears `_states` and monkeypatches `queries.ro_prescriptions` so the
next event re-opens the state with goal 70.

- [ ] **Step 6: Wire it in `server/main.py`**

Import `from server.agents.rotation import RotationCoach`. After the RecoveryAgent lines (Task 5) add:

```python
    rotation = RotationCoach(hub, bridge, sessions)
    bridge.subscribe_events(rotation.on_event)
    bridge.subscribe_frames(rotation.on_frame)
```

Replace the on_start hook `sessions.on_start.append(lambda session: bridge.notify_session_start(session.patient_id))`
with:

```python
    sessions.on_start.append(lambda session: bridge.notify_session_start(session.patient_id, session.exercise_id))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_rotation.py server/tests/test_engine_bridge.py server/tests/test_live_state.py server/tests/test_session_end.py server/tests/test_api_db.py server/tests/test_pairing.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add server/agents/rotation.py server/engine_bridge.py server/config.py .env.example server/db/persist.py \
  server/db/seed.py server/main.py server/tests/test_rotation.py server/tests/test_api_db.py server/tests/test_pairing.py
git commit -F - <<'EOF'
Server: RotationCoach (watch-counted turns, rotation_rep/rotation_set, elbow drift) and mock rotation program

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 7: ClockSync (watch ↔ camera clock offset)

**Files:**
- Create: `server/agents/clock_sync.py`, `server/tests/test_clock_sync.py`
- Modify: `server/main.py` (wiring after the RotationCoach lines)

**Interfaces:**
- Consumes: Task 1 `writes.set_watch_clock_offset`; Task 2 `watch_prefs.watch_arm`; Task 3 `AppState.clock_sync`,
  `main._on_watch_sync` calling `on_window(clean_msg, session_id)`, `WristRecorder.offset_s` attribute and the
  `wrist_recorder` local in `lifespan`.
- Produces: `server.agents.clock_sync`: `camera_accel_series(samples: list[tuple[float, list[float]]]) ->
  tuple[np.ndarray, np.ndarray] | None`; `estimate_offset(watch_t, watch_mag, cam_t, cam_mag, *, max_lag_ms=1000,
  step_ms=20) -> tuple[int, float] | None` (`(offset_ms, pearson_r)`, offset = watch − server);
  `class ClockSync(hub, sessions, db_provider=_default_db, *, clock=time.monotonic, wall=time.time)` with
  `on_frame(frame)`, `on_window(msg, session_id)`, `offset_s(session_id) -> float | None`; constants `LOCK_CORR = 0.7`,
  `LOCK_WINDOWS = 2`, `LOCK_AGREE_MS = 100`, `FALLBACK_S = 8.0`. Event `sync_progress {progress, locked, session_id, t}`
  and, once locked, `{progress: 1.0, locked: true, offset_ms, method: "xcorr"|"rtt"}`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_clock_sync.py`:

```python
"""ClockSync: the watch's sync-wave accel magnitude vs the cameras' wrist acceleration, cross-correlated over ±1 s;
locks after 2 agreeing windows with r ≥ 0.7, else falls back to the receive-time ("rtt") estimate after 8 s."""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from server.agents import clock_sync as clock_sync_mod
from server.agents.clock_sync import ClockSync, camera_accel_series, estimate_offset
from server.state import SessionRegistry

T0 = 1_791_662_000.0


def x(t: Any) -> Any:  # the wrist's wave (non-periodic over the window, so one correlation peak)
    return 0.10 * np.sin(2 * np.pi * 1.3 * t) + 0.05 * np.sin(2 * np.pi * 2.9 * t + 1.0)


def xdd(t: Any) -> Any:
    return -(0.10 * (2 * np.pi * 1.3) ** 2 * np.sin(2 * np.pi * 1.3 * t)
             + 0.05 * (2 * np.pi * 2.9) ** 2 * np.sin(2 * np.pi * 2.9 * t + 1.0))


def _camera(wrist_index: int = 16) -> list[dict[str, Any]]:
    frames = []
    for t in T0 + np.arange(0, 6, 1 / 30):
        joints = [[0.0, 1.0, 0.0] for _ in range(33)]
        joints[wrist_index] = [float(x(t - T0)), 1.0, -0.3]
        frames.append({"type": "frame", "t": float(t), "joints": joints})
    return frames


def _window(k: int, offset_s: float) -> dict[str, Any]:
    server_t = T0 + 2.5 + k + np.arange(0, 1, 0.02)
    return {"type": "watch_sync", "t0": float(server_t[0] + offset_s), "dt_ms": 20.0,
            "accel_mag": [float(v) for v in np.abs(xdd(server_t - T0)) / 9.81]}


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


@pytest.mark.parametrize("offset_ms", [240, -420, 0, 900])
def test_estimate_offset_finds_the_lag(offset_ms: int) -> None:
    cam = camera_accel_series([(f["t"], f["joints"][16]) for f in _camera()])
    assert cam is not None
    st = T0 + 2.5 + np.arange(0, 3, 0.02)
    lag, r = estimate_offset(st + offset_ms / 1000, np.abs(xdd(st - T0)) / 9.81, *cam)  # type: ignore[misc]
    assert abs(lag - offset_ms) <= 20 and r > 0.9


def test_camera_series_needs_enough_distinct_frames() -> None:
    assert camera_accel_series([(1.0, [0.0, 0.0, 0.0])] * 20) is None
    assert camera_accel_series([(1.0 + i / 30, [0.0, 0.0, 0.0]) for i in range(5)]) is None


async def _sync(arm: str = "right", db: Any = None) -> tuple[ClockSync, FakeHub, str, list[float]]:
    sessions = SessionRegistry()
    session = await sessions.start("maya", "squat")
    session.profile.device_prefs = {"watch_arm": arm}
    hub, now = FakeHub(), [0.0]
    sync = ClockSync(hub, sessions, db_provider=lambda: db, clock=lambda: now[0], wall=lambda: T0 + 6.0)
    return sync, hub, session.session_id, now


async def test_xcorr_lock_after_two_agreeing_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[Any, ...]] = []

    async def fake(*args: Any) -> bool:
        written.append(args)
        return True

    monkeypatch.setattr(clock_sync_mod.writes, "set_watch_clock_offset", fake)
    db = object()
    sync, hub, sid, now = await _sync(db=db)
    for frame in _camera():
        sync.on_frame({**frame, "session_id": sid})
    sync.on_window(_window(0, 0.24), sid)
    first = hub.events[-1]
    assert first["type"] == "sync_progress" and first["locked"] is False and 0.5 < first["progress"] < 1.0
    now[0] = 1.0
    sync.on_window(_window(1, 0.24), sid)
    locked = hub.events[-1]
    assert locked["locked"] is True and locked["progress"] == 1.0 and locked["method"] == "xcorr"
    assert abs(locked["offset_ms"] - 240) <= 20 and locked["session_id"] == sid
    assert sync.offset_s(sid) == pytest.approx(locked["offset_ms"] / 1000)
    await asyncio.sleep(0)
    assert written and written[0][0] is db and written[0][1] == sid and abs(written[0][2] - 240) <= 20


async def test_left_arm_uses_the_left_wrist() -> None:
    sync, hub, sid, now = await _sync(arm="left")
    for frame in _camera(wrist_index=15):
        sync.on_frame({**frame, "session_id": sid})
    sync.on_window(_window(0, -0.1), sid)
    now[0] = 1.0
    sync.on_window(_window(1, -0.1), sid)
    assert hub.events[-1]["locked"] is True and abs(hub.events[-1]["offset_ms"] + 100) <= 20


async def test_rtt_fallback_after_8_seconds() -> None:
    sync, hub, sid, now = await _sync()  # no frames: nothing to correlate with
    for k in range(9):
        now[0] = float(k)
        window = {"type": "watch_sync", "t0": T0 + 6.0 - 0.15 - 0.98, "dt_ms": 20.0, "accel_mag": [0.5] * 50}
        sync.on_window(window, sid)  # last sample = wall - 150 ms
    progress = [e for e in hub.events if not e["locked"]]
    assert len(progress) == 8 and all(e["progress"] == 0.0 for e in progress)
    locked = hub.events[-1]
    assert locked["locked"] is True and locked["method"] == "rtt" and locked["offset_ms"] == -150


async def test_frames_of_other_sessions_are_ignored_and_lock_is_sticky() -> None:
    sync, hub, sid, now = await _sync()
    for frame in _camera():
        sync.on_frame({**frame, "session_id": "someone-else"})
    sync.on_window(_window(0, 0.24), sid)
    assert hub.events[-1]["progress"] == 0.0  # no camera data for this session
    for frame in _camera():
        sync.on_frame({**frame, "session_id": sid})
    for k in (1, 2):
        now[0] = float(k)
        sync.on_window(_window(k, 0.24), sid)
    offset = hub.events[-1]["offset_ms"]
    now[0] = 3.0
    sync.on_window(_window(0, 0.9), sid)  # after the lock: re-announced, unchanged
    assert hub.events[-1]["locked"] is True and hub.events[-1]["offset_ms"] == offset
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_clock_sync.py -q`
Expected: FAIL (`ModuleNotFoundError: server.agents.clock_sync`).

- [ ] **Step 3: Implement `server/agents/clock_sync.py`**

```python
"""ClockSync: line up the Apple Watch's clock with the cameras' (watch Setup screen 4, "Sync Wave").

The patient waves the watch arm. The phone sends one `watch_sync` window per second (≤ 50 user-acceleration
magnitudes at 50 Hz, `t0` = the watch clock of sample 0); the cameras see the same wrist in the pose frames. The camera
wrist position (watch arm from device_prefs.watch_arm) is smoothed and differentiated twice into an acceleration
magnitude, and the last 3 s of watch samples are cross-correlated (Pearson r) against it over lags of ±1 s in 20 ms
steps. Acceleration is compared with acceleration: comparing with speed would shift the peak by a quarter period.

progress = best r / 0.7 (capped below 1 until locked). Lock = r ≥ 0.7 in 2 consecutive windows whose lags agree within
100 ms (method "xcorr"); otherwise 8 s after the first window the receive-time estimate is used (method "rtt": median
of last-sample watch time minus server receive time, i.e. the offset minus the one-way relay delay). The offset
(`watch_clock − server_clock`, ms) is written to sessions.watch_clock_offset_ms and offered to the wrist recorder.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from server.db import writes
from server.logging_setup import get_logger
from server.watch_prefs import watch_arm

log = get_logger("clock_sync")

ARM_WRIST = {"left": 15, "right": 16}  # BlazePose wrist per side
MAX_LAG_MS = 1000
LAG_STEP_MS = 20
LOCK_CORR = 0.7
LOCK_WINDOWS = 2
LOCK_AGREE_MS = 100
FALLBACK_S = 8.0
WATCH_BUFFER = 150   # samples: 3 s at 50 Hz
CAMERA_BUFFER = 180  # frames: 6 s at 30 Hz
MIN_OVERLAP = 25
SMOOTH_FRAMES = 5
MAX_STATES = 4


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def camera_accel_series(samples: list[tuple[float, list[float]]]) -> tuple[np.ndarray, np.ndarray] | None:
    """(t, |acceleration|) of a wrist track: a centred moving average over SMOOTH_FRAMES, then two central
    differences. None when there are too few frames with increasing times."""
    clean: list[tuple[float, list[float]]] = []
    for t, p in samples:
        if not clean or t > clean[-1][0]:
            clean.append((t, p))
    if len(clean) < SMOOTH_FRAMES + 4:
        return None
    t = np.array([s[0] for s in clean], dtype=float)
    pos = np.array([s[1] for s in clean], dtype=float)
    kernel = np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES
    smooth = np.column_stack([np.convolve(pos[:, i], kernel, mode="same") for i in range(3)])
    h = SMOOTH_FRAMES // 2
    t, smooth = t[h:-h], smooth[h:-h]  # the edges of a "same" convolution are biased
    vel = np.gradient(smooth, t, axis=0)
    acc = np.gradient(vel, t, axis=0)
    return t, np.linalg.norm(acc, axis=1)


def estimate_offset(watch_t: np.ndarray, watch_mag: np.ndarray, cam_t: np.ndarray, cam_mag: np.ndarray, *,
                    max_lag_ms: int = MAX_LAG_MS, step_ms: int = LAG_STEP_MS) -> tuple[int, float] | None:
    """(offset_ms, r) maximising the Pearson correlation of the watch magnitudes with the camera series sampled at
    `watch_t - offset` (offset = watch clock − server clock). None when no lag has enough overlap or variance."""
    best: tuple[int, float] | None = None
    for lag_ms in range(-max_lag_ms, max_lag_ms + 1, step_ms):
        server_t = watch_t - lag_ms / 1000.0
        mask = (server_t >= cam_t[0]) & (server_t <= cam_t[-1])
        if int(mask.sum()) < MIN_OVERLAP:
            continue
        cam = np.interp(server_t[mask], cam_t, cam_mag)
        watch = watch_mag[mask]
        if cam.std() < 1e-9 or watch.std() < 1e-9:
            continue
        r = float(np.corrcoef(watch, cam)[0, 1])
        if best is None or r > best[1]:
            best = (lag_ms, r)
    return best


@dataclass
class _SyncState:
    arm: str
    frames: deque[tuple[float, list[float]]] = field(default_factory=lambda: deque(maxlen=CAMERA_BUFFER))
    watch: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=WATCH_BUFFER))
    rtt_offsets_ms: list[float] = field(default_factory=list)
    good: list[int] = field(default_factory=list)
    started: float | None = None
    offset_ms: int | None = None
    method: str | None = None


class ClockSync:
    def __init__(self, hub: Any, sessions: Any, db_provider: Callable[[], Any | None] = _default_db, *,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.hub = hub
        self.sessions = sessions
        self.db_provider = db_provider
        self.clock = clock
        self.wall = wall
        self._states: dict[str, _SyncState] = {}

    def _state(self, session_id: str) -> _SyncState:
        st = self._states.get(session_id)
        if st is None:
            while len(self._states) >= MAX_STATES:
                self._states.pop(next(iter(self._states)))
            session = self.sessions.get(session_id)
            st = self._states[session_id] = _SyncState(watch_arm(session.profile.device_prefs if session else None))
        return st

    def offset_s(self, session_id: str) -> float | None:
        st = self._states.get(session_id)
        return st.offset_ms / 1000.0 if st is not None and st.offset_ms is not None else None

    # ---- camera side (bridge.subscribe_frames; sync, cheap) ---------------------------------------------------

    def on_frame(self, frame: dict[str, Any]) -> None:
        current = self.sessions.current
        sid = frame.get("session_id")
        if current is None or sid != current.session_id:
            return
        st = self._state(sid)
        if st.offset_ms is not None:
            return
        try:
            p = frame["joints"][ARM_WRIST.get(st.arm, 16)]
            st.frames.append((float(frame["t"]), [float(p[0]), float(p[1]), float(p[2])]))
        except (KeyError, IndexError, TypeError, ValueError):
            return

    # ---- watch side (main._on_watch_sync) -----------------------------------------------------------------------

    def on_window(self, msg: dict[str, Any], session_id: str) -> None:
        if not session_id:
            return
        st = self._state(session_id)
        if st.offset_ms is not None:  # already locked: say so again (a late or re-opened watch screen)
            self._emit_locked(session_id, st)
            return
        now = self.clock()
        if st.started is None:
            st.started = now
        t0, dt = float(msg["t0"]), float(msg["dt_ms"]) / 1000.0
        mags = [float(m) for m in msg["accel_mag"]]
        for i, m in enumerate(mags):
            st.watch.append((t0 + i * dt, m))
        st.rtt_offsets_ms.append((t0 + (len(mags) - 1) * dt - self.wall()) * 1000.0)

        corr = 0.0
        est = None
        cam = camera_accel_series(list(st.frames))
        if cam is not None and len(st.watch) >= MIN_OVERLAP:
            watch_t = np.array([w[0] for w in st.watch])
            watch_mag = np.array([w[1] for w in st.watch])
            est = estimate_offset(watch_t, watch_mag, *cam)
        if est is not None and est[1] >= LOCK_CORR:
            corr = est[1]
            st.good.append(est[0])
        else:
            corr = max(0.0, est[1]) if est is not None else 0.0
            st.good.clear()
        if len(st.good) >= LOCK_WINDOWS and abs(st.good[-1] - st.good[-2]) <= LOCK_AGREE_MS:
            self._lock(session_id, st, round((st.good[-1] + st.good[-2]) / 2), "xcorr")
            return
        if now - st.started >= FALLBACK_S:
            self._lock(session_id, st, round(statistics.median(st.rtt_offsets_ms)), "rtt")
            return
        self.hub.emit("sync_progress", {"progress": round(min(0.95, corr / LOCK_CORR), 2), "locked": False,
                                        "session_id": session_id, "t": round(self.wall(), 3)})

    def _lock(self, session_id: str, st: _SyncState, offset_ms: int, method: str) -> None:
        st.offset_ms, st.method = int(offset_ms), method
        log.info("watch clock locked", extra={"session_id": session_id, "offset_ms": st.offset_ms, "method": method})
        self._emit_locked(session_id, st)
        db = self.db_provider()
        if db is not None:
            writes.fire(writes.set_watch_clock_offset(db, session_id, st.offset_ms))

    def _emit_locked(self, session_id: str, st: _SyncState) -> None:
        self.hub.emit("sync_progress", {"progress": 1.0, "locked": True, "offset_ms": st.offset_ms,
                                        "method": st.method, "session_id": session_id, "t": round(self.wall(), 3)})
```

Note for `test_rtt_fallback_after_8_seconds`: windows at clock 0..7 emit 8 progress events (clock − started < 8);
the window at clock 8 locks with method `rtt` and offset `round(median([-150.0, …]))` = −150.

- [ ] **Step 4: Wire it in `server/main.py`**

Import `from server.agents.clock_sync import ClockSync`. After the RotationCoach lines (Task 6) add:

```python
    clock_sync = ClockSync(hub, sessions)
    state.clock_sync = clock_sync  # main._on_watch_sync hands it the sync-wave windows
    bridge.subscribe_frames(clock_sync.on_frame)
    if wrist_recorder is not None:
        wrist_recorder.offset_s = clock_sync.offset_s  # wrist rows on the server clock once locked
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_clock_sync.py server/tests/test_watch_ws.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/agents/clock_sync.py server/main.py server/tests/test_clock_sync.py
git commit -F - <<'EOF'
Server: ClockSync (watch accel vs camera wrist acceleration, xcorr lock or rtt fallback, sync_progress)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 8: AI agents — analyst context, Insight tremor-trend rule, Copilot prompt

**Files:**
- Modify: `server/agents/session_analyst.py` (`_gather`), `server/ai/prompts/analysis.md`, `server/agents/insight.py`,
  `server/ai/prompts/copilot.md`
- Create test: `server/tests/test_watch_ai.py`

**Interfaces:**
- Consumes (Task 1): `queries.session_hr_recovery`, `queries.session_rotation_sets`, `queries.patient_tremor_days`;
  `tremor_daily` readable by copilot_ro.
- Produces: analyst `data` keys `hr_recovery: [{set, peak_bpm, bpm_60s, drop_bpm}]`, `rotation_sets: [{exercise_id, set,
  reps, best_deg, avg_deg, goal_deg}]`, `tremor_7d: [{day, none_min, slight_min, mild_min, strong_min, unknown_min,
  dyskinesia, source}]`; Insight `TREMOR_TREND_SQL`, rule kind `tremor_trend` (high severity → therapist notification),
  constants `TREMOR_RISE = 0.30`, `TREMOR_MIN_DAYS = 3`.
- Not changed: `SessionVoice` (watch captions come from existing `cue.text`/`greeting.text`/`live.caption`, per the
  contract); Gemini fake fixtures — there are no prompt-hash replay fixtures in git (`data/ai_cache/` is untracked, a replay
  miss falls back to the fake; `data/copilot_demo.json` is keyed by question, not prompt), so nothing to regenerate.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_watch_ai.py`:

```python
"""AI context for the watch: the analyst sees hr_recovery, rotation_sets and 7 days of tremor; Insight's
tremor-trend rule (mild+strong minutes up ≥ 30 % week over week) writes a memory event and notifies the therapist;
the Copilot prompt and the guard know the new tables."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest

from server.agents.insight import InsightAgent, iso_week
from server.agents.session_analyst import SessionAnalyst
from server.agents.sql_guard import check_sql
from server.ai.prompts import load_prompt
from server.db.pool import Database
from server.state import SessionRegistry
from server.tests.dbutil import fresh_database, prepare, ro_dsn


def _tremor(conn: psycopg.Connection[Any], pid: str, ago: int, mild: int, strong: int = 0,
            source: str = "device") -> None:
    # Days relative to the database's current_date (the rule's clock), not the test machine's local date.
    conn.execute("INSERT INTO tremor_daily (patient_id, day, none_min, slight_min, mild_min, strong_min, unknown_min, "
                 "dyskinesia, source) VALUES (%s, current_date - %s::int, 600, 100, %s, %s, 30, 'few', %s)",
                 (pid, ago, mild, strong, source))


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    with fresh_database() as url:
        prepare(url)
        with psycopg.connect(url, autocommit=True) as conn:
            for pid in ("t_trem", "t_flat", "t_sparse"):
                conn.execute("INSERT INTO patients (id, name, therapist_id, simulated) "
                             "VALUES (%s, %s, 'okafor', true)", (pid, f"Test {pid}"))
            for ago in range(14):  # this week 50+10 a day, last week 30+10: +50 %
                _tremor(conn, "t_trem", ago, 50 if ago < 7 else 30, 10, "simulated")
                _tremor(conn, "t_flat", ago, 30)
            for ago in (1, 2, 8, 9):  # only 2 days a week: not enough
                _tremor(conn, "t_sparse", ago, 90 if ago < 7 else 10)
            for ago in range(7):
                _tremor(conn, "maya", ago, 20)
        yield url


@pytest.fixture
async def db(dsn: str) -> AsyncIterator[Database]:
    database = Database(dsn, ro_dsn(dsn))
    await database.open()
    try:
        yield database
    finally:
        await database.close()


class FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, **payload})


async def test_analyst_context_has_the_watch_data(dsn: str, db: Database) -> None:
    registry = SessionRegistry()
    session = await registry.start("maya", "forearm_rotation")
    sid = session.session_id
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id, patient_id, exercise_id, started_at, ended_at) "
                     "VALUES (%s, 'maya', 'forearm_rotation', now() - interval '10 minutes', now())", (sid,))
        conn.execute("INSERT INTO reps (session_id, rep, set_no, quality, rom) VALUES (%s, 1, 1, 1.0, 82), "
                     "(%s, 2, 1, 0.5, 62)", (sid, sid))
        conn.execute("INSERT INTO hr_recovery (session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm) "
                     "VALUES (%s, 'maya', 1, 126, 104, 22)", (sid,))
        conn.execute("INSERT INTO rotation_sets (session_id, patient_id, exercise_id, set_index, angles, best_deg, "
                     "avg_deg, goal_deg) VALUES (%s, 'maya', 'forearm_rotation', 1, '{82,62}', 82, 72, 80)", (sid,))
    analyst = SessionAnalyst(FakeHub(), registry, db_provider=lambda: db, notifications_provider=lambda: None,
                             delay_s=0)
    data, _, _ = await analyst._gather(db, sid, "maya", "forearm_rotation")
    assert data["hr_recovery"] == [{"set": 1, "peak_bpm": 126, "bpm_60s": 104, "drop_bpm": 22}]
    assert data["rotation_sets"] == [{"exercise_id": "forearm_rotation", "set": 1, "reps": 2, "best_deg": 82.0,
                                      "avg_deg": 72.0, "goal_deg": 80.0}]
    assert len(data["tremor_7d"]) == 7 and data["tremor_7d"][0]["mild_min"] == 20
    assert set(data["tremor_7d"][0]) == {"day", "none_min", "slight_min", "mild_min", "strong_min", "unknown_min",
                                         "dyskinesia", "source"}
    payload = await analyst.analyze(sid, "maya", "forearm_rotation")
    assert payload is not None and payload["reps_total"] == 2  # fake mode still works with the bigger context


async def test_tremor_trend_rule(db: Database) -> None:
    cands = [c for c in await InsightAgent(db, use_ai=False).find_candidates() if c.kind == "tremor_trend"]
    assert [c.patient_id for c in cands] == ["t_trem"]
    ev = cands[0].evidence
    assert (ev["this_week_min"], ev["last_week_min"], ev["rise_pct"]) == (420, 280, 50)
    assert ev["days_this_week"] == 7 and ev["days_last_week"] == 7 and ev["simulated_days"] == 14
    assert cands[0].note == ("Mild or strong tremor minutes rose 50% week over week (280 → 420 min). "
                             "Includes simulated watch data.")
    assert cands[0].severity == "high" and cands[0].dedupe_key == f"t_trem:tremor_trend:{iso_week()}"


async def test_tremor_trend_writes_memory_and_notifies(db: Database, dsn: str,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def create(kind: str, severity: str, therapist_id: str, patient_id: str | None, title: str,
                     body: str | None, link: str | None, source_table: str | None, source_id: str | None,
                     dedupe_key: str | None) -> int:
        calls.append({"kind": kind, "severity": severity, "patient_id": patient_id, "title": title,
                      "dedupe_key": dedupe_key})
        return len(calls)

    module = types.ModuleType("server.notify.notifications")
    module.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.notify.notifications", module)
    import server.notify

    monkeypatch.setattr(server.notify, "notifications", module, raising=False)
    result = await InsightAgent(db, use_ai=False).run()
    assert result["errors"] == []
    with psycopg.connect(dsn) as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM memory_events WHERE patient_id = 't_trem'").fetchall()]
    assert kinds == ["tremor_trend"]
    [note] = [c for c in calls if c["patient_id"] == "t_trem"]
    assert note["kind"] == "insight" and note["severity"] == "high"
    assert note["dedupe_key"] == f"insight:t_trem:tremor_trend:{iso_week()}" and "tremor rising" in note["title"]


def test_copilot_prompt_and_guard_know_the_watch_tables() -> None:
    prompt = load_prompt("copilot")
    for table in ("hr_recovery", "rotation_sets", "tremor_daily"):
        assert f"- {table}:" in prompt
        assert table in check_sql(f"SELECT * FROM {table} LIMIT 5")
    assert "simulated" in load_prompt("analysis") and "tremor_7d" in load_prompt("analysis")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest server/tests/test_watch_ai.py -q`
Expected: FAIL (`KeyError: 'hr_recovery'`, no `tremor_trend` candidates, prompt lines missing).

- [ ] **Step 3: SessionAnalyst context (`server/agents/session_analyst.py`)**

Add a module constant near the other constants: `TREMOR_CONTEXT_DAYS = 7`.

In `_gather`, after the `plans = await self._read("prescriptions", …)` line add:

```python
        hr_recovery = await self._read("hr_recovery", sid, queries.session_hr_recovery(db, sid), [])
        rotation = await self._read("rotation_sets", sid, queries.session_rotation_sets(db, sid), [])
        tremor = await self._read("tremor_daily", sid,
                                  queries.patient_tremor_days(db, patient_id, TREMOR_CONTEXT_DAYS), [])
```

and add these keys to the `data` dict (after `"targets": targets,`):

```python
            "hr_recovery": [{"set": r["set_index"], "peak_bpm": r["peak_bpm"], "bpm_60s": r["bpm_60s"],
                             "drop_bpm": r["drop_bpm"]} for r in hr_recovery],
            "rotation_sets": [{"exercise_id": r["exercise_id"], "set": r["set_index"], "reps": len(r["angles"] or []),
                               "best_deg": _r(r["best_deg"]), "avg_deg": _r(r["avg_deg"]),
                               "goal_deg": _r(r["goal_deg"])} for r in rotation],
            "tremor_7d": [{"day": str(r["day"]), "none_min": r["none_min"], "slight_min": r["slight_min"],
                           "mild_min": r["mild_min"], "strong_min": r["strong_min"],
                           "unknown_min": r["unknown_min"], "dyskinesia": r["dyskinesia"], "source": r["source"]}
                          for r in tremor],
```

In `server/ai/prompts/analysis.md`, insert this paragraph directly before `Return only the JSON object.`:

```markdown
DATA may also include `hr_recovery` (per set: the peak heart rate and how far it fell one minute into the rest; a
bigger drop_bpm is a faster recovery), `rotation_sets` (Apple Watch turn angles per set: best_deg and avg_deg against
goal_deg) and `tremor_7d` (minutes per tremor level per day from the watch). Rows with `"source": "simulated"` are demo
data: if you mention them, call them simulated. Mention these only when present and relevant.
```

- [ ] **Step 4: Insight tremor-trend rule (`server/agents/insight.py`)**

(a) Constants (after `MILESTONE_RECENT_DAYS`):

```python
TREMOR_RISE = 0.30            # tremor trend: mild+strong minutes this week >= last week × 1.3 ...
TREMOR_MIN_DAYS = 3           # ... with at least 3 days of data in each week
```

(b) `HIGH_SEVERITY_KINDS = frozenset({"compensation", "pain", "tremor_trend"})` and add
`"tremor_trend": "tremor rising",` to `KIND_TITLES`.

(c) After `MISSED_SQL` add:

```python
# Apple Watch tremor summaries: mild + strong minutes, this week (last 7 days incl. today) vs the 7 days before.
TREMOR_TREND_SQL = """
WITH w AS (
  SELECT patient_id,
         sum(mild_min + strong_min) FILTER (WHERE day > current_date - 7)  AS this_week,
         sum(mild_min + strong_min) FILTER (WHERE day <= current_date - 7) AS last_week,
         count(*) FILTER (WHERE day > current_date - 7)  AS days_this,
         count(*) FILTER (WHERE day <= current_date - 7) AS days_last,
         count(*) FILTER (WHERE source = 'simulated')    AS simulated_days
  FROM tremor_daily WHERE day > current_date - 14
  GROUP BY patient_id)
SELECT patient_id, this_week, last_week, days_this, days_last, simulated_days,
       round(100.0 * (this_week - last_week) / last_week, 1) AS rise_pct
FROM w
WHERE last_week > 0 AND days_this >= %(tremor_min_days)s AND days_last >= %(tremor_min_days)s
  AND this_week >= last_week * (1 + %(tremor_rise)s)
ORDER BY patient_id"""
```

(d) Builder after `_missed`:

```python
def _tremor_trend(row: dict[str, Any]) -> Candidate:
    ev = plain({"this_week_min": row["this_week"], "last_week_min": row["last_week"],
                "days_this_week": row["days_this"], "days_last_week": row["days_last"], "rise_pct": row["rise_pct"],
                "levels": ["mild", "strong"], "simulated_days": row["simulated_days"]})
    note = (f"Mild or strong tremor minutes rose {ev['rise_pct']}% week over week "
            f"({ev['last_week_min']} → {ev['this_week_min']} min).")
    if ev["simulated_days"]:
        note += " Includes simulated watch data."
    return Candidate(row["patient_id"], "tremor_trend", None, ev, note)
```

(e) Append `("tremor_trend", TREMOR_TREND_SQL, _tremor_trend),` to `RULES` and add
`"tremor_rise": TREMOR_RISE, "tremor_min_days": TREMOR_MIN_DAYS` to `RULE_PARAMS`.

(f) `FREE_SQL_TOOL["description"]`: replace the table list with
`"…over progress_v, sessions, reps, form_events, memory_events, patients, prescriptions, hr_recovery, rotation_sets, tremor_daily (max 200 rows)."`

- [ ] **Step 5: Copilot prompt (`server/ai/prompts/copilot.md`)**

In "## Data you can read", directly after the `prescriptions:` bullet (the two lines ending `(only the active version is
the current plan).`) and before `Nothing else is readable.`, insert:

```markdown
- hr_recovery: session_id, patient_id, set_index, peak_bpm, bpm_60s, drop_bpm (heart-rate drop one minute into the
  rest after a set, from the Apple Watch; bigger = faster recovery), time.
- rotation_sets: session_id, patient_id, exercise_id ('forearm_rotation', 'shoulder_rotation', 'wrist_flexion'),
  set_index, angles (real[], one turn per rep, degrees measured by the watch), best_deg, avg_deg, goal_deg, time.
- tremor_daily: patient_id, day (date), none_min, slight_min, mild_min, strong_min, unknown_min (minutes per tremor
  level that day), dyskinesia ('none'|'few'|'some'|'many'), source ('device'|'simulated'; always say simulated).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest server/tests/test_watch_ai.py server/tests/test_insight.py server/tests/test_session_analyst.py -q`
Expected: PASS (`test_high_severity_kinds_notify_and_milestones` still sees only compensation/pain: its database has no
tremor rows).

- [ ] **Step 7: Commit**

```bash
git add server/agents/session_analyst.py server/ai/prompts/analysis.md server/agents/insight.py \
  server/ai/prompts/copilot.md server/tests/test_watch_ai.py
git commit -F - <<'EOF'
Server: watch data in AI context (analyst, Copilot prompt) and Insight tremor-trend rule

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 9: Docs and the full test run

**Files:**
- Modify: `README.md` (new subsection at the end of "## Apple Watch companion", before "## Dashboard notifications"),
  `device/ios/NETWORKING.md` (new subsection at the end of "## Apple Watch companion (WatchConnectivity relay)", before
  "## Open items"), `server/samples/README.md` (verify the Task 2/3 paragraphs; fix any file name that drifted)

**Interfaces:**
- Consumes: everything above (documents exact names). Produces: docs only.

- [ ] **Step 1: README section**

Insert into `README.md` before `## Dashboard notifications`:

```markdown
### Apple Watch app (tri-watch): server side

Wire contract: `docs/superpowers/plans/2026-10-11-watch-contract.md` (samples in `server/samples/`). The watch still
talks only to the iPhone; everything below rides the phone's `/ws/events` socket and REST token.

- **Tables** (`server/db/migrations/007_watch.sql`, mirrored in `schema.sql`, readable by `copilot_ro`):
  `hr_recovery`, `rotation_sets`, `tremor_daily`; `patients.tremor_monitoring`, `patients.hr_limit_set_by`,
  `sessions.watch_clock_offset_ms`; `wrist_motion` gains `patient_id` and `accel`.
- **REST:** `GET /me/watch` (patient token; the watch's setup: first name, heart limit and who set it, therapist,
  tremor monitoring, `watch_*` prefs with defaults, today's plan with `done_sets` and `goal_deg` for watch exercises);
  `POST /me/tremor` (≤ 14 days, 403 while monitoring is off, rate-limited); therapist (full token):
  `PATCH /patients/{id}/limits` (therapist-page contract + `limits_changed` push + live-session refresh — when
  `therapist-page` merges, keep this route and delete the copy in `server/api/dashboard.py`),
  `PATCH /patients/{id}/watch-features` (`watch_features_changed`), `GET /patients/{id}/watch`.
  Watch settings are `device_prefs` keys `watch_taps`, `watch_arm`, `watch_double_tap`, `watch_captions` via `PUT /me`.
- **Client → server:** `watch_button` `ready|stop|resume` (`resume` = `command start`; blocked while the Guardian holds
  the session); `wrist` with `rotation_deg` (sanitised, broadcast, stored ≤ 10 Hz per patient in `wrist_motion`);
  `watch_sync` (never broadcast; goes to ClockSync).
- **Server → clients:** `haptic` (+ `countdown_tick`, `breather`), `breather`, `hr_recovery`, `rotation_rep`,
  `rotation_set`, `sync_progress`, `limits_changed`, `watch_features_changed`. These come from `Hub.emit` without `seq`.
- **Breather** (`SafetyGuardian`): an HR incident (over the limit for 5 s) pauses the engine, emits `breather start` +
  haptic and a `heart_rate` therapist notification; HR under limit − 5 for 10 s before escalation resolves it
  (`hr_recovered`) and resumes; after escalation it ends only when the incident closes (`escalated`); a stopped session
  ends it (`stopped`).
- **Agents:** `RecoveryAgent` (`server/agents/recovery.py`), `RotationCoach` (`server/agents/rotation.py`: reps from
  `rotation_deg`, elbow drift from pose keypoints → cue "Keep your elbow at your side"), `ClockSync`
  (`server/agents/clock_sync.py`). Insight adds a `tremor_trend` rule; the analyst's context and the Copilot see the
  new tables.
- **Demo without a watch:** `MIRA_SOURCE=mock MOCK_WRIST=1`, then start Maya's `forearm_rotation` (seeded, 3×10, goal
  80°): `curl -X POST localhost:8000/sessions -H "Authorization: Bearer $MIRA_API_TOKEN" -H 'Content-Type: application/json' -d '{"patient_id":"maya","exercise_id":"forearm_rotation"}'`.
  The mock streams wrist rotation at 10 Hz and lifts the elbow on rep 6 of each set.
```

- [ ] **Step 2: NETWORKING.md subsection**

Insert into `device/ios/NETWORKING.md` before `## Open items`:

```markdown
### Server events for the watch (tri-watch)

The authoritative shapes are in `docs/superpowers/plans/2026-10-11-watch-contract.md` and the samples in
`server/samples/ws_events_*.json`. Decode `seq` as optional on all of these (they are emitted with `Hub.emit`).

| Event | When | Phone relays to the watch as |
|---|---|---|
| `haptic {pattern}` | `good_rep`, `fix_form`, `set_done`, `check_in`, `countdown_tick` (every countdown number), `breather` | message `haptic` |
| `breather {state, bpm, limit, resolution?}` | HR incident opens (`start`); recovery / escalation closed / stopped (`end`) | message `breather` |
| `hr_recovery {set_index, peak_bpm, bpm_60s, drop_bpm}` | 60 s after `set_done`, or at the next set's countdown | message `recovery` |
| `rotation_rep {exercise_id, set_index, rep, reps_target, angle_deg, goal_deg, reached}` | each counted watch turn | message `rotationRep` |
| `rotation_set {exercise_id, set_index, angles, best_deg, avg_deg, goal_deg}` | `set_done` of a watch exercise | message `rotationSet` |
| `sync_progress {progress, locked, offset_ms?, method?}` | each `watch_sync` window while a session is current | message `sync` |
| `limits_changed {patient_id, hr_limit, set_by_name}` | therapist changed the heart limit | re-fetch `GET /me/watch`, push `setup` |
| `watch_features_changed {patient_id, tremor_monitoring}` | therapist toggled tremor monitoring | re-fetch `GET /me/watch`, push `setup` |
| `cue {id: "elbow_drift", text}` | elbow off the side > 0.5 s during a rotation set | message `formCue` |

Client → server additions: `watch_button {name: "resume"}`; `wrist {t?, quat?, accel?, rotation_deg?}` (≤ 10 Hz, only
during a rotation set or sync); `watch_sync {t0, dt_ms, accel_mag[]}` (1 Hz during the sync wave; not echoed back).
Watch exercises also produce normal `rep` events with `"source": "watch"` and quality `good`/`partial`.
```

- [ ] **Step 3: Verify `server/samples/README.md`**

Run: `ls server/samples | grep -E 'watch|tremor|limits|breather|recovery|rotation|sync'` and confirm every file named in
the two Apple Watch paragraphs exists (fix the paragraph if not).

- [ ] **Step 4: Full test run (once, for the whole feature)**

Run: `make db-local` (if the Docker database is not up), then
`.venv/bin/python -m pytest -q --deselect server/tests/test_scheduler.py::test_event_loop_lag_while_guard_and_insight_run`
Expected: all PASS, no unexpected skips (the deselected test is a known pre-existing failure). If anything fails, fix it
in the file the failing test covers, re-run only that file, then re-run the full command once.

- [ ] **Step 5: Commit**

```bash
git add README.md device/ios/NETWORKING.md server/samples/README.md
git commit -F - <<'EOF'
Docs: Apple Watch server side (README, NETWORKING event table, samples)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Self-review notes (for the executor)

- Spec coverage: migration/catalog/grants (T1); REST incl. limits contract + refresh + push (T2); client types, resume,
  wrist persistence ≤ 10 Hz, haptic countdown, LCD `breather` (T3); breather start/resume/escalation/stop (T4);
  recovery (T5); rotation counting + elbow drift + rows + mock (T6); clock sync lock + RTT + offset row (T7); analyst,
  Insight tremor rule, Copilot (T8); docs + full run (T9). The spec's `caption` event is intentionally dropped (contract).
- Names used across tasks: `exercises.is_watch_exercise`, `exercises.rotation_goal_deg`, `watch_prefs.watch_arm`,
  `writes.upsert_hr_recovery/upsert_rotation_set/upsert_tremor_days/set_patient_hr_limit/set_tremor_monitoring/
  set_watch_clock_offset`, `queries.session_hr_recovery/session_rotation_sets/patient_tremor_days`,
  `AppState.clock_sync`, `WristRecorder.offset_s`, `SafetyGuardian.holding/end_breather/on_session_stop`,
  `EngineBridge.notify_session_start(patient_id, exercise_id)`.
- `main.py` is edited by Tasks 2–7; each task names its anchor line. Lifespan order after Task 7:
  … `haptics` → `recovery` → `rotation` → `clock_sync` …; shutdown adds `recovery.close()` and `wrist_writer.stop`.
