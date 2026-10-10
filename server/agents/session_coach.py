"""Between-Set Coach (TRI §7.3): adjusts the workout, only within the therapist's limits.

Two layers:
- Live rules, mid-set (no AI, instant): fatigue ends the set early (never below `limits.min_reps`), a
  sustained HR over `hr_limit - 10` extends the next rest (never above `limits.max_rest_s`), and a pain
  report of 5-6 skips the next exercise for that body part and notifies the therapist. Pain >= 7 is the
  Safety Guardian's job; this agent ignores it.
- Between-set agent (Gemini FAST, during the 30-90 s rest, <= 5 tool calls, 4 s timeout): reads this set's
  stats and the last 3 sessions, and may adjust the next set (clamped server-side to `limits`) or choose a
  pre-recorded focus cue. It can never change the prescription or say new words live. If Gemini is slow,
  disabled or unavailable, or its result arrives after the rest window, the plain rules are what already
  ran and nothing further is applied.

Every adaptation is written through `server.db.writes.insert_agent_action` (agent="coach", fire-and-forget)
and announced on the hub as `coach_adapt {action, detail}`. A shortened set also raises a low-severity
`kind="coach"` notification through the F7 notifications module when it is present.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from server.ai import cue_library, gemini
from server.ai.prompts import load_prompt
from server.logging_setup import get_logger
from server.state import SessionRegistry

log = get_logger("coach")

AI_TIMEOUT_S = 4.0
MAX_TOOL_STEPS = 5
PROFILE_TIMEOUT_S = 1.5

HR_MARGIN_BPM = 10          # live rule fires when bpm > hr_limit - HR_MARGIN_BPM
HR_REST_EXTENSION_S = 15.0  # how much one HR breach adds to the next rest, before clamping
PAIN_SKIP_MIN, PAIN_SKIP_MAX = 5, 6  # pain >= 7 belongs to the Safety Guardian

DEFAULT_LIMITS: dict[str, int] = {"min_reps": 8, "max_reps": 15, "min_rest_s": 30, "max_rest_s": 120}

TOOLS: list[dict[str, Any]] = [
    {"name": "get_set_stats", "description": "Reps, good-form reps and form flags for the set just finished.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_history", "description": "Summary of the patient's last 3 sessions.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "adjust_next_set", "description": "Propose reps and/or rest seconds for the next set. "
     "Clamped server-side to the therapist's limits.",
     "parameters": {"type": "object", "properties": {
         "reps": {"type": "integer"}, "rest_s": {"type": "number"}}, "additionalProperties": False}},
    {"name": "choose_focus_cue", "description": "Play one pre-recorded focus cue by id (must already exist).",
     "parameters": {"type": "object", "properties": {"cue_id": {"type": "string"}}, "required": ["cue_id"],
                    "additionalProperties": False}},
    {"name": "finish", "description": "Stop; no further changes this rest.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"],
                    "additionalProperties": False}},
]


class _AdjustArgs(BaseModel):
    reps: int | None = None
    rest_s: float | None = None


class _CueArgs(BaseModel):
    cue_id: str


class _FinishArgs(BaseModel):
    reason: str = ""


def _fake_coach(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> gemini.Reply:
    """Default fake-mode behaviour: look at the stats and history, then adjust or finish.

    Exercised in a plain mock run (no live Gemini): asks for the set stats, then the history, then either
    nudges the next set down and picks a focus cue (a knee-cave flag was seen) or finishes with no change.
    """
    calls = sum(1 for m in messages if m.get("role") == "model" and m.get("function_call"))
    if calls == 0:
        return gemini.Reply(function_call=gemini.FunctionCall(name="get_set_stats", args={}))
    if calls == 1:
        return gemini.Reply(function_call=gemini.FunctionCall(name="get_history", args={}))
    stats = next((m["content"] for m in messages if m.get("role") == "tool" and m.get("name") == "get_set_stats"),
                 {}) or {}
    flags = stats.get("flags") or {}
    if calls == 2:
        if flags:
            return gemini.Reply(function_call=gemini.FunctionCall(
                name="adjust_next_set", args={"reps": max(0, int(stats.get("reps", 10)) - 2)}))
        return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "form looked good"}))
    if calls == 3 and flags:
        cue_id = next(iter(flags))
        return gemini.Reply(function_call=gemini.FunctionCall(
            name="choose_focus_cue", args={"cue_id": cue_id if cue_library.pick(cue_id) else "knees_out"}))
    return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "done"}))


gemini.register_fake("coach", _fake_coach)


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


async def _notify(kind: str, severity: str, therapist_id: str | None, patient_id: str | None, title: str,
                  body: str, *, source_id: str | None = None, dedupe_key: str | None = None) -> None:
    """Best-effort call into F7's notifications module. No-ops while that module doesn't exist yet."""
    try:
        from server.notify import notifications  # type: ignore[import-not-found]
    except ImportError:
        return
    create = getattr(notifications, "create", None)
    if create is None:
        return
    try:
        await create(kind, severity, therapist_id, patient_id, title, body, None, "agent_actions", source_id,
                    dedupe_key)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break the coach
        log.warning("coach notification failed", extra={"error": repr(exc)})


@dataclass
class _SetAccum:
    reps: int = 0
    good: int = 0
    flags: Counter[str] = field(default_factory=Counter)


def _data_message(data: dict[str, Any]) -> dict[str, Any]:
    import json

    return {"role": "user", "content": "DATA:\n" + json.dumps(data, ensure_ascii=False)}


class SessionCoach:
    def __init__(self, hub: Any, sessions: SessionRegistry, db_provider: Callable[[], Any | None] = _default_db
                ) -> None:
        self.hub = hub
        self.sessions = sessions
        self.db_provider = db_provider
        self._limits: dict[str, dict[str, int]] = {}
        self._sets: dict[tuple[str, int], _SetAccum] = {}
        self._rep_count: dict[str, int] = {}
        self._hr_rest_bonus: dict[str, float] = {}
        self._last_hr: dict[str, int] = {}
        self._skip_next: dict[str, set[str]] = {}
        self._gen: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- bridge.subscribe_events entry point --------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind, name = ev.get("type"), ev.get("name")
        sid = ev.get("session_id")
        if not isinstance(sid, str):
            return
        try:
            if kind == "rep":
                set_no = int(ev.get("set") or 0)
                accum = self._sets.setdefault((sid, set_no), _SetAccum())
                accum.reps += 1
                accum.good += ev.get("quality") == "good"
                accum.flags.update(f for f in ev.get("flags") or [] if isinstance(f, str))
                self._rep_count[sid] = accum.reps
            elif kind == "hr" and isinstance(ev.get("bpm"), (int, float)):
                await self._on_hr(sid, int(ev["bpm"]))
            elif kind == "fatigue":
                await self._on_fatigue(sid, ev)
            elif kind == "command" and name == "pain":
                await self._on_pain(sid, ev)
            elif kind == "set_done":
                await self._on_set_done(sid, ev)
        except Exception:  # noqa: BLE001 - one bad event must never stop the coach
            log.exception("coach failed on event", extra={"event_type": kind, "session_id": sid})

    async def drain(self) -> None:
        """Wait for every background task (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("coach task failed", extra={"error": repr(task.exception())})

    # ---- limits (reloaded per session, from the active prescription) --------------------------

    async def _limits_for(self, sid: str, exercise_id: str | None) -> dict[str, int]:
        cached = self._limits.get(sid)
        if cached is not None:
            return cached
        limits = dict(DEFAULT_LIMITS)
        session = self.sessions.get(sid)
        db = self.db_provider()
        if db is not None and session is not None:
            try:
                from server.db import queries

                rows = await asyncio.wait_for(queries.prescriptions(db, session.patient_id), PROFILE_TIMEOUT_S)
                row = next((r for r in rows if r.get("exercise_id") == (exercise_id or session.exercise_id)), None)
                if row and isinstance(row.get("limits"), dict):
                    limits.update({k: v for k, v in row["limits"].items() if k in DEFAULT_LIMITS})
            except Exception as exc:  # noqa: BLE001 - missing/slow prescription -> sane defaults
                log.info("coach: using default limits", extra={"session_id": sid, "error": repr(exc)})
        self._limits[sid] = limits
        return limits

    # ---- live rules -----------------------------------------------------------------------------

    async def _on_hr(self, sid: str, bpm: int) -> None:
        self._last_hr[sid] = bpm
        session = self.sessions.get(sid)
        hr_limit = session.profile.hr_limit if session else 130
        if bpm <= hr_limit - HR_MARGIN_BPM:
            return
        limits = await self._limits_for(sid, session.exercise_id if session else None)
        requested = limits["min_rest_s"] + self._hr_rest_bonus.get(sid, 0.0) + HR_REST_EXTENSION_S
        applied = min(requested, limits["max_rest_s"])
        if applied <= self._hr_rest_bonus.get(sid, 0.0) + limits["min_rest_s"]:
            return  # already at the ceiling: nothing new to log
        self._hr_rest_bonus[sid] = applied - limits["min_rest_s"]
        await self._adapt(sid, "extend_rest", {"bpm": bpm, "hr_limit": hr_limit, "requested_rest_s": requested,
                                                "applied_rest_s": applied})

    async def _on_fatigue(self, sid: str, ev: dict[str, Any]) -> None:
        session = self.sessions.get(sid)
        limits = await self._limits_for(sid, session.exercise_id if session else None)
        reps_done = self._rep_count.get(sid, 0)
        cue = cue_library.pick("fatigue")
        if reps_done < limits["min_reps"]:
            log.info("coach: fatigue noted, below min_reps, not ending the set", extra={
                "session_id": sid, "reps_done": reps_done, "min_reps": limits["min_reps"]})
            return
        if cue is not None:
            self.hub.emit("cue", {"session_id": sid, "t": round(time.time(), 3), **cue})
        await self._adapt(sid, "end_set_early", {"reps_done": reps_done, "min_reps": limits["min_reps"],
                                                  "reason": "fatigue"})
        await self._notify_shortened_set(sid, session, reps_done)

    async def _on_pain(self, sid: str, ev: dict[str, Any]) -> None:
        level = ev.get("payload", {}).get("level") if isinstance(ev.get("payload"), dict) else None
        if not isinstance(level, (int, float)) or not (PAIN_SKIP_MIN <= level <= PAIN_SKIP_MAX):
            return  # pain >= 7 is the Guardian's; anything below 5 needs no adaptation here
        session = self.sessions.get(sid)
        exercise_id = session.exercise_id if session else ev.get("exercise_id")
        if exercise_id:
            self._skip_next.setdefault(sid, set()).add(exercise_id)
        await self._adapt(sid, "skip_next_exercise", {"exercise_id": exercise_id, "pain_level": level})
        severity = "low" if level == PAIN_SKIP_MIN else "normal"
        therapist_id = session.profile.therapist_id if session else None
        patient_id = session.patient_id if session else None
        await _notify("coach", severity, therapist_id, patient_id, "Pain reported during exercise",
                     f"Pain level {level}/10 reported; the next {exercise_id or 'exercise'} set was skipped.",
                     source_id=sid, dedupe_key=f"coach:pain:{sid}:{ev.get('t')}")

    def skip_next(self, session_id: str) -> set[str]:
        """Exercise ids this session should skip on the next pass (read-only; tests and future wiring)."""
        return set(self._skip_next.get(session_id, set()))

    # ---- between-set agent ------------------------------------------------------------------

    async def _on_set_done(self, sid: str, ev: dict[str, Any]) -> None:
        set_no = int(ev.get("set") or 0)
        accum = self._sets.pop((sid, set_no), _SetAccum())
        self._rep_count.pop(sid, None)
        self._gen[sid] = self._gen.get(sid, 0) + 1
        gen = self._gen[sid]
        self._spawn(self._run_agent(sid, dict(ev), accum, gen))

    async def _run_agent(self, sid: str, ev: dict[str, Any], accum: _SetAccum, gen: int) -> None:
        session = self.sessions.get(sid)
        exercise_id = ev.get("exercise_id") or (session.exercise_id if session else None)
        limits = await self._limits_for(sid, exercise_id)
        reps = ev.get("reps") if isinstance(ev.get("reps"), int) else accum.reps
        stats = {"reps": reps, "good_form_reps": min(accum.good, reps), "flags": dict(accum.flags)}
        data = {"exercise": exercise_id, "set": int(ev.get("set") or 0), "stats": stats, "limits": limits}
        messages = [_data_message(data)]
        system = load_prompt("coach")
        for _ in range(MAX_TOOL_STEPS):
            try:
                reply = await gemini.ask("coach", gemini.models.fast(), system, messages, tools=TOOLS,
                                         timeout_s=AI_TIMEOUT_S)
            except gemini.AIUnavailable as exc:
                log.info("coach fallback to plain rules", extra={"session_id": sid, "reason": str(exc)})
                return
            messages.append(reply.as_message())
            if reply.function_call is None:
                return
            if reply.function_call.name == "finish":
                return
            result = await self._call_tool(sid, gen, session, stats, reply.function_call, limits)
            messages.append({"role": "tool", "name": reply.function_call.name, "content": result})

    async def _call_tool(self, sid: str, gen: int, session: Any, stats: dict[str, Any],
                         call: gemini.FunctionCall, limits: dict[str, int]) -> dict[str, Any]:
        name, args = call.name, call.args or {}
        if name == "get_set_stats":
            return stats
        if name == "get_history":
            return {"sessions": await self._history(session)}
        if name == "adjust_next_set":
            try:
                parsed = _AdjustArgs.model_validate(args)
            except ValidationError as exc:
                return {"error": f"invalid arguments: {exc}"}
            return await self._adjust_next_set(sid, gen, limits, parsed)
        if name == "choose_focus_cue":
            try:
                parsed = _CueArgs.model_validate(args)
            except ValidationError as exc:
                return {"error": f"invalid arguments: {exc}"}
            return await self._choose_focus_cue(sid, gen, parsed.cue_id)
        return {"error": f"unknown tool {name!r}"}

    async def _history(self, session: Any) -> list[dict[str, Any]]:
        db = self.db_provider()
        if db is None or session is None:
            return []
        try:
            from server.db import queries

            rows = await asyncio.wait_for(queries.recent_sessions(db, session.patient_id, limit=3),
                                          PROFILE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - history is best-effort
            log.info("coach: history unavailable", extra={"session_id": session.session_id, "error": repr(exc)})
            return []
        return [{"reps_done": r.get("reps_done"), "pain_pre": r.get("pain_pre"), "pain_post": r.get("pain_post"),
                "effort": r.get("effort")} for r in rows]

    async def _adjust_next_set(self, sid: str, gen: int, limits: dict[str, int], args: _AdjustArgs
                               ) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        applied: dict[str, Any] = {}
        if args.reps is not None:
            applied_reps = max(limits["min_reps"], min(limits["max_reps"], args.reps))
            detail["reps_requested"], detail["reps_applied"] = args.reps, applied_reps
            applied["reps"] = applied_reps
        if args.rest_s is not None:
            applied_rest = max(limits["min_rest_s"], min(limits["max_rest_s"], args.rest_s))
            detail["rest_s_requested"], detail["rest_s_applied"] = args.rest_s, applied_rest
            applied["rest_s"] = applied_rest
        if not detail:
            return {"error": "no reps or rest_s given"}
        if self._gen.get(sid) != gen:
            log.info("coach: AI result missed the rest window, dropped", extra={"session_id": sid})
            return applied
        await self._adapt(sid, "adjust_next_set", detail)
        return applied

    async def _choose_focus_cue(self, sid: str, gen: int, cue_id: str) -> dict[str, Any]:
        cue = cue_library.pick(cue_id)
        if cue is None:
            return {"error": f"unknown cue id {cue_id!r}"}
        if self._gen.get(sid) != gen:
            log.info("coach: AI result missed the rest window, dropped", extra={"session_id": sid})
            return {"cue_id": cue_id}
        self.hub.emit("cue", {"session_id": sid, "t": round(time.time(), 3), **cue})
        await self._adapt(sid, "choose_focus_cue", {"cue_id": cue_id})
        return {"cue_id": cue_id}

    # ---- logging + the coach_adapt event -----------------------------------------------------

    async def _adapt(self, sid: str, action: str, detail: dict[str, Any]) -> None:
        session = self.sessions.get(sid)
        patient_id = session.patient_id if session else None
        log.info("coach adapted", extra={"session_id": sid, "action": action, "detail": detail})
        self.hub.emit("coach_adapt", {"session_id": sid, "t": round(time.time(), 3), "action": action,
                                      "detail": detail})
        try:
            from server.db import writes
        except ImportError:
            return
        writes.fire(writes.insert_agent_action(self.db_provider(), "coach", action, patient_id=patient_id,
                                               session_id=sid, detail=detail))

    async def _notify_shortened_set(self, sid: str, session: Any, reps_done: int) -> None:
        therapist_id = session.profile.therapist_id if session else None
        patient_id = session.patient_id if session else None
        await _notify("coach", "low", therapist_id, patient_id, "Set shortened for fatigue",
                     f"The set was ended early after {reps_done} reps because the patient seemed tired.",
                     source_id=sid, dedupe_key=f"coach:fatigue:{sid}:{reps_done}")
