# Plan 2 — Session end, end-of-session AI analysis, LCD "Session complete", iOS Live → Summary

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every session ends through one code path, a `SessionAnalyst` agent writes a whole-session AI analysis (Gemini SMART, voiced by ElevenLabs) that reaches the phone and the LCD as a `session_summary` event, and the iPhone's Live screen hands off to a real Summary screen with check-in and sharing.

**Architecture:** `SessionRegistry.stop()` gains `on_stop` hooks (mirroring `on_start`) and becomes idempotent; a small `SessionEnder` event subscriber calls it on the engine's `stopped` event or after the last set. The mock engine now finishes after its 3 sets (and emits `stopped`) and the server restarts a mock session 30 s later so the LCD demo keeps looping. The analyst reads the finished session from the DB, computes the form score deterministically, asks Gemini for `{spoken, note, highlights, next_focus}` with the same validators and template fallback as `SessionVoice`, stores a `session_analyses` row, emits `session_summary`, and notifies the therapist. New REST: `GET /sessions/{id}` gains `analysis`, `POST /sessions/{id}/checkin`, `GET /patients/{id}/sessions`. Kiosk gets a `sessionDone` screen. iOS: pain level bug fix, Stop → REST stop → `SummaryView(sessionID)` polling `GET /sessions/{id}` and listening for `session_summary`.

**Tech Stack:** FastAPI, psycopg/TimescaleDB, Gemini via `server/ai/gemini.py`, ElevenLabs via `server/ai/eleven.py`, vanilla-JS kiosk (`node --test`), SwiftUI + AVFoundation.

Spec: `docs/superpowers/specs/2026-10-11-wire-ios-backend-design.md` §5 (all), §8, §9. Plan 1 (identity) is merged on this branch: `Principal`, `require_principal`, `/pair`, `/me`, patient-scoped `sessions`/`patients` routers, `server/exercises.py`, `server/db/migrate.py`.

## Global Constraints

- Branch `wire-tri`; commit per task with trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Add, never remove: the per-set `summary_ready` event and `sessions.ai_summary/ai_note` keep working exactly as today; admin token behaviour unchanged; the LCD's existing screens unchanged.
- Session end must never block or fail the live stream: `stop()` hooks run as fire-and-forget tasks; the analyst always produces an analysis (template fallback) and never raises into callers.
- Form score is **computed from rep quality**, never by the LLM. The LLM may only use numbers present in DATA (`validate_text` number guard). Never diagnose / change the plan (shared prompt rules).
- All INSERT/UPDATE SQL in `server/db/writes.py` via `_safe`; all SELECTs in `server/db/queries.py`; migrations idempotent in `server/db/migrations/`.
- Kiosk: `textContent` only (AI text), audio only from same-origin `/audio/*.mp3` (existing `isSafeAudioUrl`).
- iOS: `WireJSON` snake_case models, `Codable, Sendable`; keep every `preview` branch working; contract fixtures byte-identical to `server/samples`.
- Tests: run focused test files while developing; the full suites (`make test`, `swift test`, `xcodebuild`, `node --test device/kiosk/tests/*.test.mjs`) once at the end (Task 9).
- Python via `.venv/bin/python`; local Docker TimescaleDB on :55432.

---

## File structure

| File | Responsibility |
|---|---|
| `server/state.py` | `SessionRegistry.on_stop`, idempotent `stop()`, `ended` set |
| `server/session_end.py` (new) | `SessionEnder`: engine `stopped` / last `set_done` → `registry.stop()`; mock auto-restart |
| `server/engine_bridge.py` | `MockSource` finishes after `_SETS` (emits `stopped`) unless `MOCK_LOOP` |
| `server/config.py` | `MOCK_LOOP`, `MOCK_AUTO_RESTART_S`, `AUTO_STOP_AFTER_LAST_SET`, `ANALYST_DELAY_S` |
| `server/db/migrations/002_session_analysis.sql` (+ `schema.sql`) | `session_analyses` table |
| `server/db/writes.py` | `upsert_session_analysis` |
| `server/db/queries.py` | `session_analysis`, `previous_sessions_stats`, `patient_sessions` |
| `server/agents/session_analyst.py` (new), `server/ai/prompts/analysis.md` (new), `server/ai/validators.py` | the agent, its prompt, the `highlight` length limit |
| `server/api/sessions.py`, `server/api/patients.py`, `server/devices.py` | `analysis` in GET, `/checkin`, `/patients/{id}/sessions`, `session_summary` in the device allow-list |
| `server/main.py` | wire `SessionEnder`, analyst hook, mock auto-restart |
| `server/samples/*.json`, `device/ios/Tests/Fixtures/*.json`, `server/tests/test_samples.py` | contract |
| `device/kiosk/js/state.js`, `view.js`, `main.js`, `index.html`, `css/kiosk.css`, `tests/state.test.mjs` | `sessionDone` screen |
| `device/ios/Mira/Net/Models.swift`, `APIClient.swift`, `LiveSocket.swift` | `session_summary`, `device_status`, analysis models, `sessions`/`checkin` calls, `currentSessionID` |
| `device/ios/Mira/Features/Live/LiveView.swift`, `Home/HomeView.swift`, `Summary/SummaryView.swift` | pain level, Start/Stop via REST, Summary wired |
| tests: `server/tests/test_session_end.py`, `test_session_analyst.py`, `test_session_routes_p2.py`; `device/ios/Tests/SessionFlowTests.swift` | |

---

### Task 1: `SessionRegistry.on_stop`, idempotent stop, `SessionEnder`, mock finishes

**Files:**
- Modify: `server/state.py:111-175`, `server/engine_bridge.py` (MockSource), `server/config.py`, `server/main.py` (lifespan)
- Create: `server/session_end.py`
- Test: `server/tests/test_session_end.py`

**Interfaces:**
- Produces: `SessionRegistry.on_stop: list[Callable[[LiveSession], Any]]` (sync or async; async results are scheduled with `asyncio.create_task`, never awaited by `stop()`); `SessionRegistry.stop(session_id)` is idempotent (second call is a no-op; `self.ended: set[str]`); `SessionRegistry.is_ended(session_id) -> bool`.
- Produces: `server.session_end.SessionEnder(registry, live, *, auto_stop_after_last_set: bool)` with `async def on_event(ev: dict) -> None` (an `EngineBridge` event subscriber) and `server.session_end.MockAutoRestart(registry, patient_id, exercise_id, delay_s)` with `def on_stop(session: LiveSession) -> None` (schedules a restart unless a new session started meanwhile; `delay_s <= 0` disables).
- Config: `MOCK_LOOP: bool = False` (True restores today's never-ending program), `MOCK_AUTO_RESTART_S: float = 30.0`, `AUTO_STOP_AFTER_LAST_SET: bool = True`.
- Mock: after the last set's `set_done` + `_REST_S`, `MockSource` emits `{"type": "stopped", "reason": "program_complete"}` once and freezes (same as a user stop) unless `cfg.MOCK_LOOP`.

- [ ] **Step 1: Failing tests**

`server/tests/test_session_end.py`:
```python
"""One code path ends a session: REST stop, the engine's `stopped`, or the last set. Hooks fire exactly once."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server import session_end
from server.state import LiveSession, SessionRegistry


class Live:
    sets_target: int | None = 3


async def _registry_with_hook() -> tuple[SessionRegistry, list[str], list[str]]:
    reg = SessionRegistry()
    sync_calls: list[str] = []
    async_calls: list[str] = []

    async def async_hook(session: LiveSession) -> None:
        async_calls.append(session.session_id)

    reg.on_stop.append(lambda s: sync_calls.append(s.session_id))
    reg.on_stop.append(async_hook)
    return reg, sync_calls, async_calls


async def test_stop_runs_hooks_once_and_is_idempotent() -> None:
    reg, sync_calls, async_calls = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    await reg.stop(session.session_id)
    await reg.stop(session.session_id)  # no-op
    await asyncio.sleep(0)  # let the async hook task run
    assert sync_calls == [session.session_id] and async_calls == [session.session_id]
    assert reg.current is None and reg.is_ended(session.session_id)


async def test_hook_failure_does_not_break_stop() -> None:
    reg = SessionRegistry()
    reg.on_stop.append(lambda s: 1 / 0)
    session = await reg.start("maya", "squat")
    await reg.stop(session.session_id)  # must not raise
    assert reg.current is None


async def test_engine_stopped_event_ends_the_session() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "stopped", "session_id": session.session_id})
    assert sync_calls == [session.session_id] and reg.current is None


async def test_stopped_for_an_old_session_is_ignored() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    old = await reg.start("maya", "squat")
    await reg.stop(old.session_id)
    new = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "stopped", "session_id": old.session_id})
    assert reg.current is new and sync_calls == [old.session_id]


async def test_last_set_done_ends_the_session_when_enabled() -> None:
    reg, sync_calls, _ = await _registry_with_hook()
    session = await reg.start("maya", "squat")
    ender = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=True)
    await ender.on_event({"type": "set_done", "set": 2, "reps": 10, "session_id": session.session_id})
    assert reg.current is session  # not the last set
    await ender.on_event({"type": "set_done", "set": 3, "reps": 10, "session_id": session.session_id})
    assert sync_calls == [session.session_id]
    disabled = session_end.SessionEnder(reg, Live(), auto_stop_after_last_set=False)
    again = await reg.start("maya", "squat")
    await disabled.on_event({"type": "set_done", "set": 3, "reps": 10, "session_id": again.session_id})
    assert reg.current is again


async def test_mock_auto_restart_starts_a_new_session_after_delay() -> None:
    reg = SessionRegistry()
    restart = session_end.MockAutoRestart(reg, "maya", "squat", delay_s=0.05)
    reg.on_stop.append(restart.on_stop)
    first = await reg.start("maya", "squat")
    await reg.stop(first.session_id)
    await asyncio.sleep(0.2)
    assert reg.current is not None and reg.current.session_id != first.session_id
    # a session started by someone else in the meantime is left alone
    second = reg.current
    await reg.stop(second.session_id)
    manual = await reg.start("bob", "squat")
    await asyncio.sleep(0.2)
    assert reg.current is manual
    await restart.close()


def test_mock_program_emits_stopped_after_last_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import engine_bridge
    from server.config import cfg

    monkeypatch.setattr(cfg, "MOCK_LOOP", False)
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

    src = engine_bridge.MockSource(speed=1e9)
    src._arrive_due_s = 0.0  # arrive immediately
    monkeypatch.setattr(engine_bridge, "MOCK_GREETING_HOLD_S", 0.0)
    monkeypatch.setattr(engine_bridge, "MOCK_GESTURE_AFTER_S", 0.0)
    src.run(emitted.append, Inbox(), Stop())
    kinds = [e["type"] for e in emitted if e["type"] != "frame" and e["type"] != "hr"]
    assert kinds.count("set_done") == 3
    assert kinds.count("stopped") == 1 and kinds.index("stopped") > kinds.index("set_done")
    assert emitted[[e["type"] for e in emitted].index("stopped")]["reason"] == "program_complete"
```
(If `MOCK_GREETING_HOLD_S`/`MOCK_GESTURE_AFTER_S` are module constants with different names, adapt the monkeypatch to the real names found in `engine_bridge.py`.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest server/tests/test_session_end.py -q` → ImportError / AttributeError.

- [ ] **Step 3: `state.py`**

```python
class SessionRegistry:
    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store
        self.current: LiveSession | None = None
        self._sessions: dict[str, LiveSession] = {}
        self.ended: set[str] = set()
        self.on_start: list[Callable[[LiveSession], None]] = []
        # Called once per session after it ends (REST stop, engine `stopped`, last set, shutdown). Sync hooks run
        # inline; a coroutine result is scheduled as a task — stop() never waits on the analyst.
        self.on_stop: list[Callable[[LiveSession], Any]] = []

    def is_ended(self, session_id: str) -> bool:
        return session_id in self.ended

    async def stop(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or session_id in self.ended:
            return
        self.ended.add(session_id)
        if self.current is session:
            self.current = None
        await self._store_call("session_stopped", session)
        for hook in self.on_stop:
            try:
                result = hook(session)
                if asyncio.iscoroutine(result):
                    task = asyncio.get_running_loop().create_task(result)
                    task.add_done_callback(_log_hook_failure)
            except Exception as exc:  # noqa: BLE001 - a hook bug must not break session stop
                log.warning("session stop hook failed", extra={"session_id": session_id, "error": repr(exc)})
        log.info("session stopped", extra={"session_id": session_id})


def _log_hook_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("async session stop hook failed", extra={"error": repr(task.exception())})
```
Keep `start()` as is (it already sets `current`); add `self.ended.discard(session.session_id)` is unnecessary (new uuid).

- [ ] **Step 4: `server/session_end.py`**

```python
"""Ends a session from engine events (`stopped`, or the last `set_done`) and, in mock mode, starts the next demo
session after a pause so the LCD keeps looping — every loop is now a real session with a real analysis."""

from __future__ import annotations

import asyncio
from typing import Any

from server.logging_setup import get_logger
from server.state import LiveSession, SessionRegistry

log = get_logger("session_end")


class SessionEnder:
    """EngineBridge event subscriber. `live` is the bridge's LiveState (for sets_target)."""

    def __init__(self, registry: SessionRegistry, live: Any, *, auto_stop_after_last_set: bool = True) -> None:
        self.registry = registry
        self.live = live
        self.auto_stop_after_last_set = auto_stop_after_last_set

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        sid = ev.get("session_id")
        current = self.registry.current
        if current is None or not isinstance(sid, str) or sid != current.session_id:
            return
        if kind == "stopped":
            log.info("engine stopped the session", extra={"session_id": sid, "reason": ev.get("reason")})
            await self.registry.stop(sid)
        elif kind == "set_done" and self.auto_stop_after_last_set:
            target = getattr(self.live, "sets_target", None)
            done = ev.get("set")
            if isinstance(target, int) and isinstance(done, int) and done >= target:
                log.info("last set done; ending the session", extra={"session_id": sid, "set": done})
                await self.registry.stop(sid)


class MockAutoRestart:
    """on_stop hook for mock mode: start a fresh demo session `delay_s` after one ends, unless something else
    (REST, boot) already started one."""

    def __init__(self, registry: SessionRegistry, patient_id: str, exercise_id: str, delay_s: float) -> None:
        self.registry = registry
        self.patient_id = patient_id
        self.exercise_id = exercise_id
        self.delay_s = delay_s
        self._task: asyncio.Task[None] | None = None

    def on_stop(self, session: LiveSession) -> None:
        if self.delay_s <= 0:
            return
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.get_running_loop().create_task(self._restart())

    async def _restart(self) -> None:
        await asyncio.sleep(self.delay_s)
        if self.registry.current is not None:
            return
        log.info("mock auto-restart", extra={"patient_id": self.patient_id})
        await self.registry.start(self.patient_id, self.exercise_id)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
```

- [ ] **Step 5: Mock finishes** — in `MockSource.run`, inside the `else:` branch that advances the program (the one computing `events`), after `events = list(...)`: 
```python
                if (self._ready and not cfg.MOCK_LOOP and self._program_s >= _PROGRAM_S):
                    # The program is over: one `stopped`, then stand still (like a user stop) until the next session.
                    self._stopped = True
                    events.append({"type": "stopped", "reason": "program_complete"})
```
Because `_events_between` already yielded the final `set_done` on the tick that crossed `_SETS * _SET_S - _REST_S`, the `stopped` fires `_REST_S` later when `_program_s` reaches `_PROGRAM_S`. `_restart_session` already resets `_stopped`/`_program_s`. Run the existing `server/tests/test_engine_bridge.py` to confirm nothing depended on looping.

- [ ] **Step 6: config + main wiring**

`config.py`: add after `DEMO_TOOLS_ENABLED`:
```python
    MOCK_LOOP: bool = False  # True: the mock squat program repeats forever (pre-Plan-2 behaviour)
    MOCK_AUTO_RESTART_S: float = 30.0  # mock mode: start the next demo session this long after one ends (0 = off)
    AUTO_STOP_AFTER_LAST_SET: bool = True  # end the session when set == sets_target (engines without `stopped`)
```
`main.py` lifespan, after `bridge.subscribe_events(coach.on_event)`:
```python
    ender = SessionEnder(sessions, bridge.live, auto_stop_after_last_set=cfg.AUTO_STOP_AFTER_LAST_SET)
    bridge.subscribe_events(ender.on_event)
    auto_restart: MockAutoRestart | None = None
    if cfg.MIRA_SOURCE.startswith("mock"):
        auto_restart = MockAutoRestart(sessions, cfg.MOCK_PATIENT_ID, MOCK_EXERCISE_ID, cfg.MOCK_AUTO_RESTART_S)
        sessions.on_stop.append(auto_restart.on_stop)
```
and in `finally:` before `sessions.stop(...)`: `if auto_restart is not None: await auto_restart.close()`. Import `from server.session_end import MockAutoRestart, SessionEnder`.

Note: `MockAutoRestart` must be appended **before** the shutdown path: at shutdown, `sessions.stop()` fires `on_stop` → `auto_restart.on_stop` schedules a task → `auto_restart.close()` must run **after** that stop. Order the `finally:` as: stop session → `await auto_restart.close()`.

- [ ] **Step 7: Run** `.venv/bin/python -m pytest server/tests/test_session_end.py server/tests/test_engine_bridge.py server/tests/test_api_db.py server/tests/test_live_state.py -q` → PASS.
- [ ] **Step 8: Commit** — `git add server/state.py server/session_end.py server/engine_bridge.py server/config.py server/main.py server/tests/test_session_end.py` / `"Sessions end through one path: REST stop, engine stopped, last set; mock finishes and auto-restarts"`.

---

### Task 2: `session_analyses` table, writes and queries

**Files:**
- Create: `server/db/migrations/002_session_analysis.sql`; Modify: `server/db/schema.sql`, `server/db/writes.py`, `server/db/queries.py`
- Test: `server/tests/test_session_analysis_db.py`

**Interfaces:**
- Table per spec §5.2 (columns exactly: `session_id uuid PK REFERENCES sessions(id) ON DELETE CASCADE, created_at, form_score smallint, reps_total int, good_reps int, sets_done int, spoken text NOT NULL, note text NOT NULL, highlights jsonb NOT NULL, next_focus text, flagged_moments jsonb NOT NULL, comparison jsonb NOT NULL, audio_url text, source text NOT NULL, model text`).
- `writes.upsert_session_analysis(db, session_id, *, form_score, reps_total, good_reps, sets_done, spoken, note, highlights: list, next_focus, flagged_moments: list, comparison: dict, audio_url, source, model) -> bool`.
- `queries.session_analysis(db, session_id) -> dict | None`.
- `queries.previous_sessions_stats(db, patient_id, exercise_id, before_session_id, limit=5) -> dict` → `{"count": n, "avg_good_rep_rate": float|None, "avg_rom_deg": float|None, "avg_asym_pct": float|None, "last_form_score": int|None}` from `sessions` ⨝ `reps` (quality, rom, asym_avg) and `session_analyses.form_score`, ended sessions only, same patient+exercise, started before the given session.
- `queries.patient_sessions(db, patient_id, limit=30, before: datetime|None) -> list[dict]` → rows `{id, exercise_id, started_at, ended_at, reps_done, pain_post, effort, shared, simulated, form_score, good_reps, reps_total, spoken_preview (first 140 chars of spoken)}` LEFT JOIN `session_analyses`, newest first, ended sessions only, `NOT simulated`.

- [ ] Steps: failing DB test (insert a session + reps rows, upsert analysis twice → one row, latest values; `patient_sessions` returns it with `form_score`; `previous_sessions_stats` averages two older sessions and ignores the current one), migration SQL (append to schema.sql too), writes/queries per the signatures above, run `test_session_analysis_db.py test_migrate.py`, commit `"Add session_analyses: whole-session analysis storage and reads"`.

---

### Task 3: `SessionAnalyst` agent

**Files:**
- Create: `server/agents/session_analyst.py`, `server/ai/prompts/analysis.md`; Modify: `server/ai/validators.py` (`LENGTH_LIMITS["highlight"] = (20, 1)`), `server/main.py`
- Test: `server/tests/test_session_analyst.py`

**Interfaces:**
- `class SessionAnalyst(hub, registry, db_provider=_default_db, notifications_provider=..., delay_s=cfg.ANALYST_DELAY_S)`; `def on_stop(self, session: LiveSession) -> Coroutine` (registered in `registry.on_stop`); `async def analyze(session_id, patient_id, exercise_id) -> dict | None` (returns the stored payload; None when skipped); `async def drain()` for tests; `register_fake("analyst", _fake_analysis)`.
- Config: `ANALYST_DELAY_S: float = 2.0` (wait for `reps`/`form_events` writes to land), `ANALYST_TIMEOUT_S = 12.0`.
- Event `session_summary` payload: `{session_id, t, exercise_id, exercise_name, form_score, reps_total, good_reps, sets_done, spoken, note (labeled "AI draft — review before use: …"), highlights: [{kind, text}], next_focus, flagged_moments: [{set, rep, rule_id, label, severity, t}], comparison: {prev_sessions, good_rep_rate_delta, rom_delta_deg, asym_delta_pct, form_score_delta}, audio_url, source: ai|template, model}`.
- Form score: `round(100 * mean(reps.quality))` when reps exist (quality is 0..1 per `persist.QUALITY_SCORES`), clamped 0..100; `good_reps = count(quality >= 1.0)`; `sets_done = count(distinct set_no)`.
- Flagged moments: `form_events` joined to `reps` by `rep` → `label = cue_library.pick(rule_id)["text"]` if a cue exists for that id, else `FLAG_REMINDERS` from `session_voice` (import), else the rule id humanised. Cap at 12 (most severe/earliest first).
- Skip rule: `reps_total == 0 and duration_s < 20` → log `analysis_skipped`, return None (no event). An otherwise empty session (0 reps, > 20 s) **does** get a template analysis ("No reps were counted…").
- Gemini: `ask("analyst", models.smart(), load_prompt("analysis"), [DATA], json_mode=True, timeout_s=ANALYST_TIMEOUT_S)` → `AnalysisOut(BaseModel): spoken: str; note: str; highlights: list[str] (1..3); next_focus: str | None`; `validate_json` with one retry (same pattern as `SessionVoice._ask_summary`); `validate_text(spoken, "spoken", allowed)`, `validate_text(note, "note", allowed)`, each highlight `validate_text(h, "highlight", allowed)` (drop invalid ones; if none left → template highlights); on any failure `source="template"` with `template_analysis(data)`.
- DATA dict: `{first_name, exercise, exercise_name, sets_done, reps_total, good_reps, form_score, flags: {rule_id: count}, duration_s, pain_pre, pain_post, effort, tw_load_left_pct, per_set: [{set, reps, good_reps, tw_load_left_pct}], previous: previous_sessions_stats, targets: prescription targets}`.
- After storing: `hub.emit("session_summary", payload)`; `notifications.create(kind="session_summary", severity="info", therapist_id=profile.therapist_id or "okafor", patient_id, title=f"{first_name} finished {exercise_name}", body=f"Form {form_score}/100 · {good_reps}/{reps_total} good reps", link=f"/sessions/{sid}", source_table="session_analyses", source_id=sid, dedupe_key=f"session_summary:{sid}")` (skip when `state.notifications is None`).
- `prompts/analysis.md`: same structure as `summary.md` but for the whole session: JSON with `spoken` (2–3 sentences, warm, second person, one thing that went well + one focus, compare to `previous` only with numbers in DATA), `note` (2–4 factual sentences for the therapist: reps, good-form reps, form score, flags, pain/effort, vs targets and previous), `highlights` (1–3 items, each ≤ 20 words, patient-facing, e.g. "Knee bend reached 84°, up 6° from last time"), `next_focus` (one short phrase or null). End with the shared `## Rules (always)` block copied verbatim from `summary.md`.

- [ ] Steps: failing tests (fake hub + `SessionRegistry` + a throwaway DB prepared with a session, 12 reps (2 `fix_form` with `knee_cave_l` form_events), 2 older ended sessions; `on_stop` → one `session_summary` event with `form_score == 92` (10×1.0 + 2×0.5 → 11/12 → 92), `good_reps == 10`, `flagged_moments` has 2 entries with labels, `comparison.prev_sessions == 2`, row in `session_analyses`, notification created; template fallback when `gemini` raises `AIUnavailable` → `source == "template"`; number guard: fake returning "You did 99 reps" → spoken falls back to template; skip rule), implement, run `test_session_analyst.py test_session_voice.py test_validators.py`, wire in `main.py` (`analyst = SessionAnalyst(hub, sessions)`; `sessions.on_stop.append(analyst.on_stop)`; `state.analyst = analyst`; `await analyst.close()` in finally), commit `"SessionAnalyst: whole-session AI analysis on session end → session_summary event"`.

---

### Task 4: Routes, device allow-list, samples

**Files:**
- Modify: `server/api/sessions.py`, `server/api/patients.py`, `server/devices.py:26`, `server/tests/test_samples.py`, `server/samples/README.md`
- Create: `server/samples/ws_events_session_summary.json`, `rest_get_patient_sessions.json`, `rest_post_session_checkin.json`; regenerate `rest_get_session.json` (now has `analysis` and `exercise_name`); copy all to `device/ios/Tests/Fixtures/`
- Test: `server/tests/test_session_routes_p2.py`

**Interfaces:**
- `GET /sessions/{id}`: adds `"exercise_name": exercises.display_name(...)` and `"analysis": <session_analyses row as dict with labeled note> | null`.
- `POST /sessions/{id}/checkin` (owner or full): `CheckinIn{pain_post: int 0..10 | None, effort: int 1..10 | None, note: str ≤ 1000 | None}` → `writes.record_session_feedback(db, sid, pain_post=, effort=, note=)` → `{session_id, pain_post, effort, note_saved: bool}`; if `pain_post >= 7` also `state.bridge.inject({"type": "command", "name": "pain", "payload": {"level": pain_post, "when": "post"}, "session_id": sid})` so the Guardian reacts exactly as for a live pain command.
- `GET /patients/{id}/sessions?limit=30&before=` (owner or full) → `{patient_id, sessions: [...]}` from `queries.patient_sessions`.
- `DEVICE_EVENT_TYPES` += `"session_summary"`.
- Samples generated from a running test server (DB-backed, fake Gemini) with the same script style as Plan 1 Task 8; the `session_summary` sample comes from the analyst running on the mock session after `POST /sessions/{id}/stop` (wait for the event on a `/ws/events` client, ≤ 15 s).

- [ ] Steps: failing route tests (patient token via `/pair` K7P4M2; stop the live mock session → within 15 s `GET /sessions/{id}` has `analysis.form_score`; `/checkin` persists and 7+ opens a Guardian incident (an `alert` event with kind `pain` appears on `/ws/events`); `/patients/maya/sessions` lists it with `form_score`; Bob's are 403), implement, samples + `test_samples.py` entries + fixtures copy, run `test_session_routes_p2.py test_api_db.py test_patient_scope.py test_samples.py test_device_token.py`, commit `"Session end REST: analysis in GET, checkin, patient session list; session_summary reaches the LCD"`.

---

### Task 5: Kiosk "Session complete" screen

**Files:**
- Modify: `device/kiosk/js/state.js`, `view.js`, `main.js`, `index.html`, `css/kiosk.css`, `tests/state.test.mjs`

**Interfaces:**
- `state.summary: {msg, at} | null`; reducer: `session_summary` → `{...state, summary: {msg, at: now}}`; `arrived` clears it (session boundary — add to the existing `arrived` reset).
- `view`: `export const SESSION_DONE_MS = 60000`; after the `rejected`/incident/paused checks and **before** the `rest|done` check: `if (state.summary && now - state.summary.at <= SESSION_DONE_MS && (phase === "done" || phase === "idle" || phase === "rest")) return {...base, screen: "sessionDone", mood: "celebrating", sessionSummary: {formScore, goodReps, repsTotal, spoken}}`. `base.sessionSummary = null` otherwise.
- `view.js`: `#session-score`, `#session-good`, `#session-spoken` text fields; `index.html`: new `<section class="screen" data-screen="sessionDone" aria-label="Session complete">` mirroring the setDone markup (eyebrow "SESSION COMPLETE", big score with "/100", "N of M good reps" pill, spoken text ≤ 3 lines); `kiosk.css`: add `sessionDone` to the display rules next to `setDone` (lines ~117-135) and reuse `.bloom`; `main.js`: `AUDIO_EVENTS.add("session_summary")` (it carries `audio_url`).
- Tests: `session_summary` → `sessionDone` with the numbers; expires after `SESSION_DONE_MS`; `arrived` clears it; `countdown` phase wins (next session started).

- [ ] Steps: failing node tests → implement → `node --test device/kiosk/tests/*.test.mjs` green → commit `"LCD: Session complete screen from session_summary"`.

---

### Task 6: iOS models, sockets and calls for session end

**Files:**
- Modify: `device/ios/Mira/Net/Models.swift`, `APIClient.swift`, `LiveSocket.swift`; `device/ios/Tests/ContractFixtureTests.swift`
- Create: `device/ios/Tests/SessionFlowTests.swift`

**Interfaces:**
- Models: `SessionSummaryMessage { sessionId: String?, exerciseId, exerciseName, formScore: Int?, repsTotal, goodReps, setsDone, spoken: String, note: String?, highlights: [Highlight], nextFocus: String?, flaggedMoments: [FlaggedMoment], comparison: JSONValue?, audioUrl: String?, source: String? }` with `struct Highlight { kind: String?; text: String }`, `struct FlaggedMoment { set: Int?; rep: Int?; ruleId: String; label: String?; severity: String?; t: Double? }`; `SocketEvent.sessionSummary(SessionSummaryMessage)` (type `"session_summary"`); `DeviceStatusMessage { device: String?; online: Bool }` + `SocketEvent.deviceStatus` (type `"device_status"`); `SessionAnalysis` (same fields as the message minus `t`) and `SessionDetail.analysis: SessionAnalysis?`, `SessionDetail.exerciseName: String?`; `SessionListItem { id, exerciseId, exerciseName, startedAt, endedAt, repsDone, painPost, effort, shared, formScore, goodReps, repsTotal, spokenPreview }`, `SessionList { patientId, sessions }`; `CheckinRequest { painPost: Int?, effort: Int?, note: String? }`, `CheckinResponse { sessionId, painPost, effort, noteSaved }`.
- APIClient: `sessions(patientID:limit:) -> SessionList`, `checkin(sessionID:_:) -> CheckinResponse`.
- `LiveSessionStore`: `private(set) var currentSessionID: String?` — set from any `/ws/events` message carrying `session_id` (decode a tiny `struct SessionStamp: Codable { let sessionId: String? }` alongside the event) and cleared on `reset`; `private(set) var sessionSummary: SessionSummaryMessage?` set on `.sessionSummary` (and `cue = spoken`), cleared on `reset` and on `.arrived`; `private(set) var lcdOnline: Bool?` from `.deviceStatus`.
- Tests: decode `ws_events_session_summary`, `ws_events_device_status`, `rest_get_patient_sessions`, `rest_post_session_checkin`, `rest_get_session` (has `analysis`); `LiveSessionStore` sets `currentSessionID`/`sessionSummary` when fed the fixture bytes through `eventSocket.onMessage`.

- [ ] Steps: failing tests → implement → `swift test` green → commit `"iOS: session_summary, device_status, analysis, session list and check-in models"`.

---

### Task 7: iOS Live → Summary flow, Home Start, pain level

**Files:**
- Modify: `device/ios/Mira/Features/Live/LiveView.swift`, `Home/HomeView.swift`, `Summary/SummaryView.swift`, `App/AppState.swift` (small helpers)

**Interfaces / behaviour:**
- `AppState`: `func startSession(exerciseID: String?) async throws -> SessionStarted` (POST /sessions; exercise defaults to `me?.prescriptions.first?.exerciseId ?? "squat"`); `func stopSession(_ id: String) async throws`; `func loadSession(_ id: String) async throws -> SessionDetail`; `func checkin(_ id: String, pain: Int, effort: Int, note: String) async throws`; `func share(_ id: String) async throws`.
- **HomeView**: `FullScreenFlow` becomes `enum { case live, summary(sessionID: String) }` (Identifiable via a string id). Start button (`"Start today's session"` when linked, else current text): `Task { let s = try await state.startSession(exerciseID: nil); flow = .live }`; on error show the inline message. The "Session summary" button opens `.summary(sessionID:)` for `state.liveSession.currentSessionID` or the latest ended session from `GET /patients/{id}/sessions` (fetch on tap), else an inline "No sessions yet".
- **LiveView**: `reportPain()` → shows the pain screen first; its "Keep going gently" button sends `CommandMessage.pain(level: Int(pain))` then returns to `.live`; "Stop for today" sends the pain level then goes to `.stop` — replace the copy "Your pain report was requested…" with "Mira tells your care team how you're feeling." Stop screen "End session": `Task { if let sid = state.liveSession.currentSessionID { try await state.stopSession(sid); onEnded(sid) } else { try await state.liveSession.send(.stop) } }` where `onEnded: (String) -> Void` is a new init parameter HomeView passes to switch `flow = .summary(sessionID:)`. Header: `state.liveSession.live?.exerciseName ?? exerciseId` and `"Set \(set) of \(setsTarget)"` when both present (`LiveMessage` needs `exerciseName: String?` — add it; the server already sends `exercise_name`). Movement view placeholder card: title "3D movement view", body "Coming soon — Mira will show your skeleton here once the camera engine is connected.", with a subtle `.opacity` pulse animation and the red border when `flags` is non-empty.
- **SummaryView(sessionID:)**: new init `init(sessionID: String, initialScreen: Screen = .results)`; keep the old `init(initialScreen:)` for the design preview (sessionID nil → preview-only behaviour unchanged). `@State detail: SessionDetail?`, `@State loadError: String?`. `.task`: load via `state.loadSession`; if `detail?.analysis == nil`, re-fetch every 3 s up to 60 s; also `.onChange(of: state.liveSession.sessionSummary)` → when its `sessionId == sessionID` re-fetch immediately. Results: form score ring = `analysis.formScore` (draw with `Circle().trim(to: score/100)` in `MiraUI.accent`), good reps `goodReps of repsTotal`, "Mira is reviewing your session…" + `ProgressView` while `analysis == nil` (after 60 s show `summary.aiSummary` per-set text with the caption "Mira's last set summary"). Highlights list under the score. **Hear Mira's summary**: `AVPlayer(url: serverAddress + audioUrl)` when `audioUrl` present, else `AVSpeechSynthesizer` on `spoken`. Flags screen: list `analysis.flaggedMoments` ("\(label)" / "Set \(set) · Rep \(rep)"); Replay/Slow stay disabled with caption "3D replay coming soon"; toolbar "Moment N of M". Check-in: Send → `state.checkin(...)` then if `sharing` → `state.share(...)`; success → `dismiss()`; the share toggle defaults to `state.me?.patient.shareByDefault ?? false`; remove the "Sharing isn't connected yet" line; errors inline.
- Preview mode (`miraDesignPreview`) keeps showing the Figma sample copy exactly as today.

- [ ] Steps: implement; `xcodebuild … build` → BUILD SUCCEEDED; `swift test` green; manual simulator check against the local mock server (pair K7P4M2 → Start → End session → Summary fills within ~5 s with a form score and Mira's spoken summary; audio plays if ElevenLabs is configured, else on-device TTS); commit `"iOS: Live ends through REST and opens the real Summary; pain level sent; Home starts sessions"`.

---

### Task 8: Docs

- README: "Session end and analysis" section (one path to end a session, `MOCK_LOOP`/`MOCK_AUTO_RESTART_S`/`AUTO_STOP_AFTER_LAST_SET`/`ANALYST_DELAY_S`, the `session_summary` event, `session_analyses`, new routes, LCD screen); `device/kiosk/README.md` (+ Session complete screen); `device/ios/NETWORKING.md` (session flow); `.env.example`. Commit `"Docs: session end, SessionAnalyst, LCD session screen"`.

### Task 9: Plan-level verification (once)

- [ ] `make test` green; `cd device/ios && swift test` green; `xcodebuild … build` BUILD SUCCEEDED; `node --test device/kiosk/tests/*.test.mjs` green.
- [ ] Local end-to-end with `MIRA_SOURCE=mock make run`: after ~2.5 min the mock session ends on its own → `session_summary` on `/ws/events`, LCD at `/device/` shows "Session complete", `GET /patients/maya/sessions` shows the row with a form score, and 30 s later a new session arrives.
- [ ] Do not deploy yet (deployment is the last step of the overall effort, after Plan 4, with `GEMINI_MODE=live`).

## Self-review notes

- Spec §5.1 ✔ T1; §5.2 ✔ T2; §5.3 ✔ T3 (notification + event + storage + fallback + skip); §5.4 ✔ T4 (`/checkin` pain ≥ 7 routes to the Guardian via `bridge.inject`); §5.5 ✔ T4 + T6 (`device_status` decoded); §5.6 ✔ T5; §5.7 ✔ T7 (Start via POST, pain level, stop → summary, header, placeholder, polling, audio, check-in, share default).
- Deviation to record in the spec after the plan lands: the analyst also runs for 0-rep sessions longer than 20 s (template text), and the LCD "Session complete" screen also shows while `phase == "rest"` so it is visible immediately after the last `set_done` → `stopped`.
