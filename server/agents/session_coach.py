"""Between-Set Coach (TRI §7.3): adjusts the workout, only within the therapist's limits.

Two layers:
- Live rules, mid-set (no AI, instant): fatigue ends the set early (never below `limits.min_reps`), a
  heart rate over `hr_limit - 10` extends the next rest (never above `limits.max_rest_s`), and a pain
  report of 5-6 skips the next exercise for that body part and notifies the therapist. Pain >= 7 is the
  Safety Guardian's job; this agent ignores it.
- Between-set agent (Gemini FAST, during the rest, <= 5 tool calls, 4 s per call): reads this set's stats
  and the last 3 sessions, and may adjust the next set (clamped server-side to `limits`) or choose a
  pre-recorded focus cue. It can never change the prescription or say new words live. If Gemini is slow,
  disabled or unavailable, or its result arrives after the rest window, the plain rules are what already
  ran and nothing further is applied.

While the Safety Guardian holds the session (an open incident, a `paused` event, a confirmed fall or a
pain report >= 7) the Coach makes no adaptation at all and never resumes anything.

Adaptations that change the workout are sent to the engine on the reverse channel
(`bridge.send_to_engine`): `end_set_early`, `extend_rest`, `adjust_next_set`, `skip_next_exercise`.
History and prescription limits are read as copilot_ro (`Database.ro_fetch`; defaults without COPILOT_DSN).
Every adaptation is written through `server.db.writes.insert_agent_action` (agent="coach", fire-and-forget)
and announced on the hub as `coach_adapt {action, detail}`. A shortened set or a skipped exercise also
raises a `kind="coach"` notification through the F7 notifications module when it is present.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from server.ai import cue_library, gemini
from server.ai.prompts import load_prompt
from server.db import writes
from server.logging_setup import get_logger
from server.state import SessionRegistry

log = get_logger("coach")

AI_TIMEOUT_S = 4.0
MAX_TOOL_STEPS = 5
PROFILE_TIMEOUT_S = 1.5
HISTORY_SESSIONS = 3

HR_MARGIN_BPM = 10          # live rule fires when bpm > hr_limit - HR_MARGIN_BPM
HR_REST_EXTENSION_S = 15.0  # how much one HR breach adds to the next rest, before clamping
PAIN_SKIP_MIN, PAIN_SKIP_MAX = 5, 6  # pain >= 7 belongs to the Safety Guardian
GUARDIAN_PAIN_LEVEL = 7

DEFAULT_LIMITS: dict[str, int] = {"min_reps": 8, "max_reps": 15, "min_rest_s": 30, "max_rest_s": 120}
_LIMIT_RANGE = {"min_reps": (1, 100), "max_reps": (1, 100), "min_rest_s": (0, 900), "max_rest_s": (0, 900)}

# Cues the model may pick: form and encouragement only. Safety and flow cues (check_in, pause, resume,
# im_ok_thanks, countdowns, set_start, ...) belong to the engine and the Guardian.
# Body part a pain report applies to when the client does not say (payload.body_part wins).
EXERCISE_BODY_PART: dict[str, str] = {"squat": "knee", "glute_bridge": "hip"}
_BODY_PART_RE = re.compile(r"^[a-z][a-z_]{0,31}$")

FOCUS_CUES: tuple[str, ...] = ("knees_out", "knee_cave_l", "knee_cave_r", "trunk_lean", "depth_more",
                               "slow_down", "good_rep", "encouragement")

TOOLS: list[dict[str, Any]] = [
    {"name": "get_set_stats", "description": "Reps, good-form reps and form flags for the set just finished.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_history", "description": "Summary of the patient's last 3 sessions.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "adjust_next_set", "description": "Propose reps and/or rest seconds for the next set. "
     "Clamped server-side to the therapist's limits.",
     "parameters": {"type": "object", "properties": {
         "reps": {"type": "integer"}, "rest_s": {"type": "number"}}, "additionalProperties": False}},
    {"name": "choose_focus_cue", "description": "Play one pre-recorded focus cue by id.",
     "parameters": {"type": "object", "properties": {"cue_id": {"type": "string", "enum": list(FOCUS_CUES)}},
                    "required": ["cue_id"], "additionalProperties": False}},
    {"name": "finish", "description": "Stop; no further changes this rest.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"],
                    "additionalProperties": False}},
]


class _AdjustArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reps: int | None = Field(None, ge=0, le=100)
    rest_s: float | None = Field(None, ge=0, le=900, allow_inf_nan=False)

    @field_validator("reps", "rest_s", mode="before")
    @classmethod
    def _plain_number(cls, v: Any) -> Any:
        if isinstance(v, (bool, str)):  # no True -> 1, no "6" -> 6
            raise ValueError("must be a number")
        return v


class _CueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cue_id: str = Field(max_length=64)

    @field_validator("cue_id")
    @classmethod
    def _focus_cue(cls, v: str) -> str:
        if v not in FOCUS_CUES:
            raise ValueError("not an allowed focus cue")
        return v


def _arg_error(name: str, exc: ValidationError) -> dict[str, Any]:
    fields = sorted({".".join(str(p) for p in e["loc"]) or "args" for e in exc.errors(include_input=False)})
    return {"error": f"invalid arguments for {name}", "fields": fields}


def _clean_limits(raw: Any) -> dict[str, int]:
    """The prescription's limits, each field checked; anything missing, malformed or inconsistent → defaults."""
    limits = dict(DEFAULT_LIMITS)
    if isinstance(raw, dict):
        for key, (lo, hi) in _LIMIT_RANGE.items():
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
                    and lo <= value <= hi:
                limits[key] = int(value)
    for lo_key, hi_key in (("min_reps", "max_reps"), ("min_rest_s", "max_rest_s")):
        if limits[lo_key] > limits[hi_key]:
            limits[lo_key], limits[hi_key] = DEFAULT_LIMITS[lo_key], DEFAULT_LIMITS[hi_key]
    return limits


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
            name="choose_focus_cue", args={"cue_id": cue_id if cue_id in FOCUS_CUES else "knees_out"}))
    return gemini.Reply(function_call=gemini.FunctionCall(name="finish", args={"reason": "done"}))


gemini.register_fake("coach", _fake_coach)


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def _default_incident_open(session_id: str) -> bool:
    """True while the Safety Guardian has an open incident for this session."""
    try:
        from server.state import get_state

        guardian = get_state().guardian
    except RuntimeError:
        return False
    return guardian is not None and session_id in getattr(guardian, "incidents", {})


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


@dataclass
class _SessionState:
    """Everything the Coach keeps for one live session; dropped as a whole when a new session starts."""
    limits_task: asyncio.Task[dict[str, int]] | None = None
    sets: dict[int, _SetAccum] = field(default_factory=dict)
    current_set: int = 0
    gen: int = 0                # bumped whenever a rest window opens or closes; stale agent results drop
    hr_rest_bonus: float = 0.0  # extra rest (s) over min_rest_s for the coming rest
    fatigue_set: int | None = None
    paused: bool = False
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


def _data_message(data: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": "DATA:\n" + json.dumps(data, ensure_ascii=False)}


class SessionCoach:
    def __init__(self, hub: Any, sessions: SessionRegistry, bridge: Any | None = None,
                 db_provider: Callable[[], Any | None] = _default_db,
                 incident_open: Callable[[str], bool] = _default_incident_open) -> None:
        self.hub = hub
        self.bridge = bridge
        self.sessions = sessions
        self.db_provider = db_provider
        self.incident_open = incident_open
        self._state: dict[str, _SessionState] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- bridge.subscribe_events entry point --------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind, name = ev.get("type"), ev.get("name")
        sid = ev.get("session_id")
        if not isinstance(sid, str):
            return
        try:
            st = self._session_state(sid, ev)
            if kind == "rep":
                self._on_rep(st, ev)
            elif kind == "countdown":
                self._rest_over(st, sid)
            elif kind == "paused" or (kind == "fall" and ev.get("stage") == "confirmed"):
                self._hold(st)
            elif kind == "resumed":
                st.paused = False
            elif kind == "hr" and isinstance(ev.get("bpm"), (int, float)) and not isinstance(ev["bpm"], bool):
                await self._on_hr(sid, st, int(ev["bpm"]))
            elif kind == "fatigue":
                await self._on_fatigue(sid, st)
            elif kind == "command" and name == "pain":
                await self._on_pain(sid, st, ev)
            elif kind == "set_done":
                self._on_set_done(sid, st, ev)
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

    def _spawn(self, coro: Coroutine[Any, Any, Any], st: _SessionState | None = None) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        if st is not None:
            st.tasks.add(task)
            task.add_done_callback(st.tasks.discard)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("coach task failed", extra={"error": repr(task.exception())})

    # ---- per-session state ----------------------------------------------------------------------

    def _session_state(self, sid: str, ev: dict[str, Any]) -> _SessionState:
        st = self._state.get(sid)
        if st is not None:
            return st
        for old_sid, old in self._state.items():  # one live session at a time: forget the previous one
            for task in old.tasks:
                task.cancel()
            log.info("coach: session state dropped", extra={"session_id": old_sid})
        self._state.clear()
        st = self._state[sid] = _SessionState()
        # Load the limits now, in the background, so the live rules rarely have to wait for them.
        st.limits_task = self._spawn(self._load_limits(sid, ev.get("exercise_id")))
        return st

    async def _limits(self, st: _SessionState) -> dict[str, int]:
        assert st.limits_task is not None
        return await asyncio.shield(st.limits_task)

    async def _load_limits(self, sid: str, exercise_id: str | None) -> dict[str, int]:
        """The active prescription's limits, read once per session (an approved proposal applies next session)."""
        session = self.sessions.get(sid)
        db = self.db_provider()
        if db is None or session is None:
            return dict(DEFAULT_LIMITS)
        try:
            from server.db import queries

            rows = await asyncio.wait_for(queries.ro_prescriptions(db, session.patient_id, PROFILE_TIMEOUT_S),
                                          PROFILE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - missing/slow prescription -> sane defaults
            log.info("coach: using default limits", extra={"session_id": sid, "error": repr(exc)})
            return dict(DEFAULT_LIMITS)
        wanted = session.exercise_id or exercise_id
        row = next((r for r in rows if r.get("exercise_id") == wanted), None)
        return _clean_limits(row.get("limits") if row else None)

    def _held(self, sid: str, st: _SessionState) -> bool:
        """The Guardian owns the session: no adaptation of any kind."""
        return st.paused or self.incident_open(sid)

    def _hold(self, st: _SessionState) -> None:
        st.paused = True
        st.gen += 1  # any in-flight between-set result is now stale

    # ---- live rules -----------------------------------------------------------------------------

    def _on_rep(self, st: _SessionState, ev: dict[str, Any]) -> None:
        set_no = int(ev.get("set") or 0)
        if set_no not in st.sets:
            st.sets[set_no] = _SetAccum()
            if set_no != st.current_set:
                st.current_set = set_no
                st.gen += 1  # a new set started: the previous rest window is closed
        accum = st.sets[set_no]
        accum.reps += 1
        accum.good += ev.get("quality") == "good"
        accum.flags.update(f for f in ev.get("flags") or [] if isinstance(f, str))

    def _rest_over(self, st: _SessionState, sid: str) -> None:
        st.gen += 1
        st.hr_rest_bonus = 0.0  # the extension applied to the rest that just ended
        if st.paused and not self.incident_open(sid):
            st.paused = False  # the engine started the next set: the session is running again

    async def _on_hr(self, sid: str, st: _SessionState, bpm: int) -> None:
        session = self.sessions.get(sid)
        hr_limit = session.profile.hr_limit if session else 130
        if bpm <= hr_limit - HR_MARGIN_BPM or self._held(sid, st):
            return
        limits = await self._limits(st)
        requested = limits["min_rest_s"] + st.hr_rest_bonus + HR_REST_EXTENSION_S
        applied = min(requested, limits["max_rest_s"])
        if applied <= st.hr_rest_bonus + limits["min_rest_s"]:
            return  # already at the ceiling: nothing new to log
        st.hr_rest_bonus = applied - limits["min_rest_s"]
        self._adapt(sid, "extend_rest", {"bpm": bpm, "hr_limit": hr_limit, "requested_rest_s": requested,
                                         "applied_rest_s": applied}, engine={"rest_s": applied})

    async def _on_fatigue(self, sid: str, st: _SessionState) -> None:
        if self._held(sid, st) or st.fatigue_set == st.current_set:
            return  # held by the Guardian, or this set was already ended early
        limits = await self._limits(st)
        accum = st.sets.get(st.current_set)
        reps_done = accum.reps if accum else 0
        if reps_done < limits["min_reps"]:
            log.info("coach: fatigue noted, below min_reps, not ending the set", extra={
                "session_id": sid, "reps_done": reps_done, "min_reps": limits["min_reps"]})
            return
        st.fatigue_set = st.current_set
        cue = cue_library.pick("fatigue")
        if cue is not None:
            self.hub.emit("cue", {"session_id": sid, "t": round(time.time(), 3), **cue})
        self._adapt(sid, "end_set_early", {"set": st.current_set, "reps_done": reps_done,
                                           "min_reps": limits["min_reps"], "reason": "fatigue"},
                    engine={"set": st.current_set, "reason": "fatigue"})
        session = self.sessions.get(sid)
        self._spawn(_notify(
            "coach", "low", session.profile.therapist_id if session else None,
            session.patient_id if session else None, "Set shortened for fatigue",
            f"Set {st.current_set} was ended early after {reps_done} reps because the patient seemed tired.",
            source_id=sid, dedupe_key=f"coach:fatigue:{sid}:{st.current_set}"))

    async def _on_pain(self, sid: str, st: _SessionState, ev: dict[str, Any]) -> None:
        level = ev.get("payload", {}).get("level") if isinstance(ev.get("payload"), dict) else None
        if not isinstance(level, (int, float)) or isinstance(level, bool):
            return
        if level >= GUARDIAN_PAIN_LEVEL:
            self._hold(st)  # the Guardian's incident; the Coach stays out of it
            return
        if not (PAIN_SKIP_MIN <= level <= PAIN_SKIP_MAX) or self._held(sid, st):
            return
        session = self.sessions.get(sid)
        exercise_id = session.exercise_id if session else ev.get("exercise_id")
        body_part = ev["payload"].get("body_part")
        if not (isinstance(body_part, str) and _BODY_PART_RE.match(body_part)):
            body_part = EXERCISE_BODY_PART.get(str(exercise_id), exercise_id)
        self._adapt(sid, "skip_next_exercise", {"body_part": body_part, "exercise_id": exercise_id,
                                                "pain_level": level},
                    engine={"body_part": body_part, "reason": "pain"})
        self._spawn(_notify(
            "coach", "low" if level == PAIN_SKIP_MIN else "normal",
            session.profile.therapist_id if session else None, session.patient_id if session else None,
            "Pain reported during exercise",
            f"Pain level {level}/10 reported; the next {body_part or 'exercise'} exercise was skipped.",
            source_id=sid, dedupe_key=f"coach:pain:{sid}:{ev.get('t')}"))

    # ---- between-set agent ------------------------------------------------------------------

    def _on_set_done(self, sid: str, st: _SessionState, ev: dict[str, Any]) -> None:
        set_no = int(ev.get("set") or 0)
        accum = st.sets.pop(set_no, _SetAccum())
        st.gen += 1
        if self._held(sid, st):
            return
        self._spawn(self._run_agent(sid, st, dict(ev), accum, st.gen), st)

    async def _run_agent(self, sid: str, st: _SessionState, ev: dict[str, Any], accum: _SetAccum,
                         gen: int) -> None:
        session = self.sessions.get(sid)
        limits = await self._limits(st)
        reps = ev.get("reps") if isinstance(ev.get("reps"), int) else accum.reps
        stats = {"reps": reps, "good_form_reps": min(accum.good, reps), "flags": dict(accum.flags)}
        data = {"exercise": ev.get("exercise_id") or (session.exercise_id if session else None),
                "set": int(ev.get("set") or 0), "stats": stats, "limits": limits}
        messages = [_data_message(data)]
        system = load_prompt("coach")
        try:
            # Backstop for the rest window (the next set's countdown or first rep also closes it).
            async with asyncio.timeout(max(limits["min_rest_s"], AI_TIMEOUT_S)):
                for _ in range(MAX_TOOL_STEPS):
                    reply = await gemini.ask("coach", gemini.models.fast(), system, messages, tools=TOOLS,
                                             timeout_s=AI_TIMEOUT_S)
                    if self._stale(sid, st, gen):
                        return
                    messages.append(reply.as_message())
                    if reply.function_call is None or reply.function_call.name == "finish":
                        return
                    result = await self._call_tool(sid, st, gen, session, stats, reply.function_call, limits)
                    if self._stale(sid, st, gen):
                        return
                    messages.append({"role": "tool", "name": reply.function_call.name, "content": result})
        except gemini.AIUnavailable as exc:
            log.info("coach fallback to plain rules", extra={"session_id": sid, "reason": str(exc)})
        except TimeoutError:
            log.info("coach: AI missed the rest window, dropped", extra={"session_id": sid})

    def _stale(self, sid: str, st: _SessionState, gen: int) -> bool:
        if st.gen == gen and not self._held(sid, st) and self._state.get(sid) is st:
            return False
        log.info("coach: AI result missed the rest window, dropped", extra={"session_id": sid})
        return True

    async def _call_tool(self, sid: str, st: _SessionState, gen: int, session: Any, stats: dict[str, Any],
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
                return _arg_error(name, exc)
            return self._adjust_next_set(sid, st, gen, limits, parsed)
        if name == "choose_focus_cue":
            try:
                cue_args = _CueArgs.model_validate(args)
            except ValidationError as exc:
                return _arg_error(name, exc)
            return self._choose_focus_cue(sid, st, gen, cue_args.cue_id)
        return {"error": "unknown tool"}

    async def _history(self, session: Any) -> list[dict[str, Any]]:
        """The patient's last 3 sessions before this one (fixed query, bounded wait)."""
        db = self.db_provider()
        if db is None or session is None:
            return []
        try:
            from server.db import queries

            rows = await asyncio.wait_for(
                queries.ro_recent_sessions(db, session.patient_id, HISTORY_SESSIONS + 1, PROFILE_TIMEOUT_S),
                PROFILE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - history is best-effort
            log.info("coach: history unavailable", extra={"session_id": session.session_id, "error": repr(exc)})
            return []
        past = [r for r in rows if str(r.get("id")) != session.session_id][:HISTORY_SESSIONS]
        return [{"date": r["started_at"].date().isoformat() if isinstance(r.get("started_at"), datetime) else None,
                 "exercise": r.get("exercise_id"), "reps_done": r.get("reps_done"), "pain_pre": r.get("pain_pre"),
                 "pain_post": r.get("pain_post"), "effort": r.get("effort")} for r in past]

    def _adjust_next_set(self, sid: str, st: _SessionState, gen: int, limits: dict[str, int],
                         args: _AdjustArgs) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        applied: dict[str, Any] = {}
        if args.reps is not None:
            applied["reps"] = max(limits["min_reps"], min(limits["max_reps"], args.reps))
            detail["reps_requested"], detail["reps_applied"] = args.reps, applied["reps"]
        if args.rest_s is not None:
            applied["rest_s"] = max(limits["min_rest_s"], min(limits["max_rest_s"], args.rest_s))
            detail["rest_s_requested"], detail["rest_s_applied"] = args.rest_s, applied["rest_s"]
        if not detail:
            return {"error": "no reps or rest_s given"}
        if self._stale(sid, st, gen):
            return {"error": "rest window over"}
        self._adapt(sid, "adjust_next_set", detail, engine=applied)
        return applied

    def _choose_focus_cue(self, sid: str, st: _SessionState, gen: int, cue_id: str) -> dict[str, Any]:
        cue = cue_library.pick(cue_id)
        if cue is None:
            return {"error": "unknown cue id"}
        if self._stale(sid, st, gen):
            return {"error": "rest window over"}
        self.hub.emit("cue", {"session_id": sid, "t": round(time.time(), 3), **cue})
        self._adapt(sid, "choose_focus_cue", {"cue_id": cue_id})
        return {"cue_id": cue_id}

    # ---- logging + the coach_adapt event -----------------------------------------------------

    def _adapt(self, sid: str, action: str, detail: dict[str, Any], engine: dict[str, Any] | None = None) -> None:
        """Log + announce one adaptation; `engine` (if given) also tells the engine to apply it."""
        session = self.sessions.get(sid)
        patient_id = session.patient_id if session else None
        log.info("coach adapted", extra={"session_id": sid, "action": action, "detail": detail})
        self.hub.emit("coach_adapt", {"session_id": sid, "t": round(time.time(), 3), "action": action,
                                      "detail": detail})
        if engine is not None and self.bridge is not None:
            self.bridge.send_to_engine({"type": action, "session_id": sid, **engine})
        writes.fire(writes.insert_agent_action(self.db_provider(), "coach", action, patient_id=patient_id,
                                               session_id=sid, detail=detail))
