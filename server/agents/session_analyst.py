"""SessionAnalyst (spec §5.3): the whole-session analysis, written once a session ends.

On `SessionRegistry.on_stop` it waits `ANALYST_DELAY_S` (so the last rep / form_event writes land), reads the
session from the database, computes the numbers itself (form score from `reps.quality`, never from the LLM),
asks Gemini SMART for the words ({spoken, note, highlights, next_focus}), checks every text with the shared
validators (only numbers from DATA), voices `spoken` with ElevenLabs, stores the row in `session_analyses`,
emits `session_summary` on /ws/events and creates a therapist notification. Any AI failure falls back to a
fixed-English template built from the same numbers, so every ended session gets an analysis.

Skipped (log `analysis_skipped`, no event, returns None): a false start (0 reps and under 20 s), or no database
attached (there is nothing to read the session from). The hook never raises and never blocks `stop()`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from server.ai import cue_library, eleven, gemini
from server.ai.prompts import load_prompt
from server.ai.session_voice import FLAG_REMINDERS, _data_message, _numbers, _read_data
from server.ai.validators import label_note, validate_json, validate_text
from server.config import cfg
from server.db import queries, writes
from server.exercises import display_name
from server.logging_setup import get_logger
from server.state import LiveSession, SessionRegistry

log = get_logger("agents.analyst")

ANALYST_TIMEOUT_S = 12.0
MIN_SESSION_S = 20.0  # 0 reps and shorter than this = a false start: no analysis
MAX_FLAGGED_MOMENTS = 12
DEFAULT_THERAPIST_ID = "okafor"
_SEVERITY_RANK = {"critical": 0, "urgent": 0, "high": 1, "major": 1, "warning": 2, "medium": 2, "minor": 3,
                  "low": 3, "info": 4}


class AnalysisOut(BaseModel):
    spoken: str
    note: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    next_focus: str | None = None


# ---- deterministic numbers ---------------------------------------------------------------------------------

def form_score(qualities: list[float]) -> int | None:
    """round(100 · mean(quality)), half up, clamped to 0..100; None without any rep quality."""
    if not qualities:
        return None
    return max(0, min(100, int(100 * sum(qualities) / len(qualities) + 0.5)))


def flag_label(rule_id: str) -> str:
    """The cue text for this rule, else the FLAG_REMINDERS phrase, else the rule id humanised."""
    cue = cue_library.pick(rule_id)
    if cue and cue.get("text"):
        return str(cue["text"])
    reminder = FLAG_REMINDERS.get(rule_id)
    if reminder:
        return reminder[0].upper() + reminder[1:]
    return rule_id.replace("_", " ").capitalize()


def _r(value: Any, digits: int = 1) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


def _delta(now: Any, before: Any, digits: int = 1) -> float | None:
    if isinstance(now, (int, float)) and isinstance(before, (int, float)):
        return round(float(now) - float(before), digits)
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _seconds(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _top_flag(data: dict[str, Any]) -> str | None:
    flags: dict[str, int] = data.get("flags") or {}
    return max(flags, key=lambda f: flags[f]) if flags else None


def _highlight_kind(text: str) -> str:
    low = text.lower()
    if "pain" in low:
        return "pain"
    if any(w in low for w in ("balance", "weight", "load", "even", "side")):
        return "balance"
    if any(w in low for w in ("bend", "°", "range", "deeper", "depth", "rom")):
        return "rom"
    if any(w in low for w in ("streak", "in a row", "days")):
        return "streak"
    return "consistency"


# ---- template (fixed English from the numbers) ----------------------------------------------------------------

def template_analysis(data: dict[str, Any]) -> dict[str, Any]:
    """{spoken, note, highlights: [{kind, text}], next_focus} built only from DATA numbers."""
    name = data.get("first_name") or "there"
    total, good = int(data.get("reps_total") or 0), int(data.get("good_reps") or 0)
    top = _top_flag(data)
    reminder = FLAG_REMINDERS.get(top) if top else None
    focus = (reminder[0].upper() + reminder[1:]) if reminder else (flag_label(top) if top else None)
    comparison = data.get("comparison") or {}
    if total == 0:
        spoken = (f"No reps were counted this session, {name}. That's okay, your therapist will see it and "
                  "you can try again next time.")
        highlights = [{"kind": "consistency", "text": "You showed up and started your session"}]
    else:
        spoken = f"Nice work, {name}! You finished {total} reps and {good} had good form."
        spoken += f" Next time, try to {reminder}." if reminder else " Keep up the steady work next time."
        highlights = [{"kind": "consistency", "text": f"{good} of {total} reps with good form"}]
        rom, rom_delta = data.get("rom_avg_deg"), comparison.get("rom_delta_deg")
        if isinstance(rom, (int, float)) and isinstance(rom_delta, (int, float)) and rom_delta > 0:
            highlights.append({"kind": "rom", "text": f"Knee bend averaged {rom}°, up {rom_delta}° from recent "
                                                      "sessions"})
    return {"spoken": spoken, "note": template_note(data), "highlights": highlights, "next_focus": focus}


def template_note(data: dict[str, Any]) -> str:
    total = int(data.get("reps_total") or 0)
    if total == 0:
        text = (f"{data.get('exercise_name')}: no reps counted over {data.get('duration_s')} s; "
                "the session ended without a completed rep.")
    else:
        targets = data.get("targets") or {}
        target = (f" (target {targets['sets']} sets × {targets['reps']} reps)"
                  if targets.get("sets") and targets.get("reps") else "")
        score = f"; form score {data['form_score']}" if data.get("form_score") is not None else ""
        text = (f"{data.get('exercise_name')}: {total} reps over {data.get('sets_done')} sets{target}, "
                f"{data.get('good_reps')} with good form{score}.")
    flags = ", ".join(f"{k} ×{v}" for k, v in (data.get("flags") or {}).items()) or "none"
    text += f" Form flags: {flags}."
    pain = [f"{k.removeprefix('pain_')} {data[k]}/10" for k in ("pain_pre", "pain_post")
            if data.get(k) is not None]
    if pain:
        text += f" Pain: {', '.join(pain)}."
    if data.get("effort") is not None:
        text += f" Effort {data['effort']}/10."
    previous = data.get("previous") or {}
    comparison = data.get("comparison") or {}
    if previous.get("count") and comparison.get("form_score_delta") is not None:
        text += f" Form score change vs the last session: {comparison['form_score_delta']:+g}."
    return text


def _fake_analysis(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
    data = _read_data(messages)
    total, good = data.get("reps_total") or 0, data.get("good_reps") or 0
    top = _top_flag(data)
    return gemini.Reply(text=json.dumps({
        "spoken": f"Great session, {data.get('first_name') or 'there'}! {good} of {total} reps had good form.",
        "note": template_note(data),
        "highlights": [f"{good} of {total} reps had good form"],
        "next_focus": flag_label(top) if top else None,
    }))


gemini.register_fake("analyst", _fake_analysis)


# ---- providers ---------------------------------------------------------------------------------------------

def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


def _default_notifications() -> Any | None:
    try:
        from server.state import get_state

        return get_state().notifications
    except RuntimeError:
        return None


class SessionAnalyst:
    def __init__(self, hub: Any, registry: SessionRegistry, db_provider: Callable[[], Any | None] = _default_db,
                 notifications_provider: Callable[[], Any | None] = _default_notifications,
                 delay_s: float | None = None) -> None:
        self.hub = hub
        self.registry = registry
        self.db_provider = db_provider
        self.notifications_provider = notifications_provider
        self.delay_s = cfg.ANALYST_DELAY_S if delay_s is None else delay_s
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- hook / lifecycle ------------------------------------------------------------------------------

    def on_stop(self, session: LiveSession) -> asyncio.Task[Any] | None:
        """registry.on_stop hook: schedule the analysis as a background task (tracked for drain/close) and
        return at once. Never raises."""
        try:
            return self._spawn(self._after_stop(session))
        except Exception as exc:  # noqa: BLE001 - e.g. no running loop; the hook must never break stop()
            log.warning("analysis not scheduled", extra={"session_id": session.session_id, "error": repr(exc)})
            return None

    async def _after_stop(self, session: LiveSession) -> None:
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        await self.analyze(session.session_id, session.patient_id, session.exercise_id)

    async def close(self) -> None:
        """Cancel every pending analysis on shutdown. The session that ended right at shutdown (its
        `on_stop` hook scheduled `_after_stop` only moments ago) is cancelled along with the rest and gets
        no analysis — there is no next request to serve one to, and the process is exiting anyway."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait for every background task (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("analyst task failed", extra={"error": repr(task.exception())})

    # ---- analysis ---------------------------------------------------------------------------------------

    async def analyze(self, session_id: str, patient_id: str, exercise_id: str) -> dict[str, Any] | None:
        """The emitted/stored `session_summary` payload, or None when skipped (false start, no database) or
        on an unexpected error (logged, never raised)."""
        try:
            return await self._analyze(session_id, patient_id, exercise_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the analyst never raises into callers
            log.exception("analysis failed", extra={"session_id": session_id, "error": repr(exc)})
            return None

    async def _analyze(self, sid: str, patient_id: str, exercise_id: str) -> dict[str, Any] | None:
        db = self.db_provider()
        if db is None:
            log.info("analysis_skipped", extra={"session_id": sid, "reason": "no_db"})
            return None
        data, flagged, comparison = await self._gather(db, sid, patient_id, exercise_id)
        if data["reps_total"] == 0 and (data["duration_s"] or 0) < MIN_SESSION_S:
            log.info("analysis_skipped", extra={"session_id": sid, "reason": "false_start",
                                                "duration_s": data["duration_s"]})
            return None

        words, source, model = await self._words(sid, data)
        audio_url = await eleven.tts(words["spoken"], f"analysis:{sid}")
        await writes.upsert_session_analysis(
            db, sid, form_score=data["form_score"], reps_total=data["reps_total"], good_reps=data["good_reps"],
            sets_done=data["sets_done"], spoken=words["spoken"], note=words["note"], highlights=words["highlights"],
            next_focus=words["next_focus"], flagged_moments=flagged, comparison=comparison, audio_url=audio_url,
            source=source, model=model)
        payload = {
            "session_id": sid, "patient_id": patient_id, "t": round(time.time(), 3), "exercise_id": exercise_id,
            "exercise_name": data["exercise_name"], "form_score": data["form_score"],
            "reps_total": data["reps_total"], "good_reps": data["good_reps"], "sets_done": data["sets_done"],
            "spoken": words["spoken"], "note": label_note(words["note"]), "highlights": words["highlights"],
            "next_focus": words["next_focus"], "flagged_moments": flagged, "comparison": comparison,
            "audio_url": audio_url, "source": source, "model": model,
        }
        self.hub.emit("session_summary", payload)
        log.info("analysis ready", extra={"session_id": sid, "source": source, "form_score": data["form_score"]})
        await self._notify(sid, patient_id, data)
        return payload

    async def _read(self, label: str, sid: str, call: Awaitable[Any], default: Any) -> Any:
        try:
            result = await call
        except Exception as exc:  # noqa: BLE001 - a failed read leaves that part of the analysis empty
            log.warning("analysis read failed", extra={"session_id": sid, "read": label, "error": repr(exc)})
            return default
        return default if result is None else result

    async def _gather(self, db: Any, sid: str, patient_id: str,
                      exercise_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        live = self.registry.get(sid)
        row = await self._read("session", sid, queries.get_session(db, sid), {})
        reps = await self._read("reps", sid, queries.session_reps(db, sid), [])
        events = await self._read("form_events", sid, queries.session_form_events(db, sid), [])
        tw_load = await self._read("tw_load", sid, queries.session_tw_load(db, sid), None)
        tw_sets = await self._read("tw_load_per_set", sid, queries.session_tw_load_per_set(db, sid), [])
        previous = await self._read("previous", sid, queries.previous_sessions_stats(db, patient_id, exercise_id,
                                                                                      sid), {"count": 0})
        plans = await self._read("prescriptions", sid, queries.prescriptions(db, patient_id), [])

        started = _seconds(row.get("started_at")) or (live.started_at if live else None)
        ended = _seconds(row.get("ended_at")) or time.time()
        duration_s = round(max(0.0, ended - started)) if started is not None else None

        qualities = [float(r["quality"]) for r in reps if isinstance(r.get("quality"), (int, float))]
        reps_total = len(reps)
        good_reps = sum(1 for q in qualities if q >= 1.0)
        score = form_score(qualities)
        flags = Counter(str(e["rule_id"]) for e in events if e.get("rule_id"))
        if not events:  # no form_events stored: fall back to the per-rep flag arrays
            flags = Counter(str(f) for r in reps for f in r.get("flags") or [])
        if score is None and reps_total:  # reps without quality: penalise flags (spec §5.3 fallback)
            score = max(0, min(100, 100 - round(15 * sum(flags.values()) / reps_total)))

        set_of = {r["rep"]: r.get("set_no") for r in reps}
        per_set_load = {r.get("set_no"): _r(r.get("tw_load_left_pct")) for r in tw_sets}
        per_set: list[dict[str, Any]] = []
        for set_no in sorted({r.get("set_no") for r in reps if r.get("set_no") is not None}):
            in_set = [r for r in reps if r.get("set_no") == set_no]
            per_set.append({"set": int(set_no), "reps": len(in_set),
                            "good_reps": sum(1 for r in in_set if (r.get("quality") or 0) >= 1.0),
                            "tw_load_left_pct": per_set_load.get(set_no)})

        rom_avg = _r(_mean([float(r["rom"]) for r in reps if isinstance(r.get("rom"), (int, float))]))
        asym_avg = _r(_mean([float(r["asym_avg"]) for r in reps if isinstance(r.get("asym_avg"), (int, float))]))
        good_rate = round(good_reps / reps_total, 2) if reps_total else None
        comparison = {
            "prev_sessions": int(previous.get("count") or 0),
            "good_rep_rate_delta": _delta(good_rate, previous.get("avg_good_rep_rate"), 2),
            "rom_delta_deg": _delta(rom_avg, previous.get("avg_rom_deg")),
            "asym_delta_pct": _delta(asym_avg, previous.get("avg_asym_pct")),
            "form_score_delta": _delta(score, previous.get("last_form_score"), 0),
        }
        if comparison["form_score_delta"] is not None:
            comparison["form_score_delta"] = int(comparison["form_score_delta"])

        flagged = self._flagged(events, set_of, started)
        plan = next((p for p in plans if p.get("exercise_id") == exercise_id), None)
        targets = ({"sets": plan.get("sets"), "reps": plan.get("reps"), **(plan.get("targets") or {})}
                   if plan else {})
        profile = live.profile if live else None
        pain_pre = row.get("pain_pre")
        if pain_pre is None and profile is not None:
            pain_pre = profile.pain_today
        data: dict[str, Any] = {
            "first_name": profile.first_name if profile else None,
            "exercise": exercise_id, "exercise_name": display_name(exercise_id),
            "sets_done": len(per_set), "reps_total": reps_total, "good_reps": good_reps, "form_score": score,
            "flags": dict(flags), "duration_s": duration_s, "pain_pre": pain_pre, "pain_post": row.get("pain_post"),
            "effort": row.get("effort"), "tw_load_left_pct": _r(tw_load), "per_set": per_set,
            "rom_avg_deg": rom_avg, "asym_avg_pct": asym_avg, "good_rep_rate": good_rate,
            "previous": {k: _r(v, 2) if isinstance(v, float) else v for k, v in previous.items()},
            "comparison": comparison, "targets": targets,
        }
        return data, flagged, comparison

    @staticmethod
    def _flagged(events: list[dict[str, Any]], set_of: dict[Any, Any],
                 started: float | None) -> list[dict[str, Any]]:
        moments: list[tuple[int, float, dict[str, Any]]] = []
        for e in events:
            rule_id = e.get("rule_id")
            if not rule_id:
                continue
            at = _seconds(e.get("time"))
            severity = e.get("severity") or "warning"
            set_no = set_of.get(e.get("rep"))
            moments.append((_SEVERITY_RANK.get(str(severity), 5), at or 0.0, {
                "set": int(set_no) if set_no is not None else None, "rep": e.get("rep"), "rule_id": rule_id,
                "label": flag_label(str(rule_id)), "severity": severity,
                "t": round(at - started, 1) if at is not None and started is not None else None,
            }))
        moments.sort(key=lambda m: (m[0], m[1]))
        return [m[2] for m in moments[:MAX_FLAGGED_MOMENTS]]

    # ---- words (Gemini or template) -------------------------------------------------------------------------

    async def _words(self, sid: str, data: dict[str, Any]) -> tuple[dict[str, Any], str, str | None]:
        """({spoken, note (unlabeled), highlights, next_focus}, source, model)."""
        template = template_analysis(data)
        if not data["reps_total"]:  # nothing to analyse: the fixed "No reps were counted" text
            return template, "template", None
        allowed = _numbers(data)
        parsed, replies = await self._ask(sid, data)
        spoken = note = None
        if parsed is not None:
            spoken = validate_text(parsed.spoken, "spoken", allowed)
            note = validate_text(parsed.note, "note", allowed)
        if spoken is None or note is None:
            for reply in replies:  # never replay a rejected answer from the cache
                await gemini.discard(reply)
            return template, "template", None
        assert parsed is not None
        texts = [t for t in (validate_text(h, "highlight", allowed) for h in parsed.highlights) if t]
        highlights = [{"kind": _highlight_kind(t), "text": t} for t in texts] or template["highlights"]
        focus = validate_text(parsed.next_focus, "highlight", allowed) if parsed.next_focus else None
        if parsed.next_focus and focus is None:
            focus = template["next_focus"]
        for reply in replies[:-1]:
            await gemini.discard(reply)
        model = replies[-1].model if replies else None
        return {"spoken": spoken, "note": note, "highlights": highlights, "next_focus": focus}, "ai", model

    async def _ask(self, sid: str, data: dict[str, Any]) -> tuple[AnalysisOut | None, list[gemini.Reply]]:
        messages = [_data_message(data)]
        system = load_prompt("analysis")
        try:
            reply = await gemini.ask("analyst", gemini.models.smart(), system, messages, timeout_s=ANALYST_TIMEOUT_S,
                                     json_mode=True)
        except gemini.AIUnavailable as exc:
            log.info("analysis fallback", extra={"session_id": sid, "reason": str(exc)})
            return None, []
        replies = [reply]

        async def retry(error: str) -> str | None:
            fix = {"role": "user", "content": f"Your answer was rejected ({error}). Return only the JSON object "
                                              "with string fields spoken and note, highlights (a list of 1 to 3 "
                                              "strings) and next_focus (a string or null)."}
            try:
                again = await gemini.ask("analyst", gemini.models.smart(), system,
                                         [*messages, reply.as_message(), fix], timeout_s=ANALYST_TIMEOUT_S,
                                         json_mode=True)
            except gemini.AIUnavailable:
                return None
            replies.append(again)
            return again.text

        return await validate_json(reply.text, AnalysisOut, retry), replies

    # ---- therapist notification ---------------------------------------------------------------------------

    async def _notify(self, sid: str, patient_id: str, data: dict[str, Any]) -> None:
        service = self.notifications_provider()
        if service is None:
            return
        live = self.registry.get(sid)
        therapist_id = (live.profile.therapist_id if live else None) or DEFAULT_THERAPIST_ID
        first_name = data.get("first_name") or patient_id
        body = (f"Form {data['form_score']}/100 · {data['good_reps']}/{data['reps_total']} good reps"
                if data.get("form_score") is not None else "No reps counted")
        try:
            await service.create(kind="session_summary", severity="low", therapist_id=therapist_id,
                                 patient_id=patient_id, title=f"{first_name} finished {data['exercise_name']}",
                                 body=body, link=f"/sessions/{sid}", source_table="session_analyses",
                                 source_id=sid, dedupe_key=f"session_summary:{sid}")
        except Exception as exc:  # noqa: BLE001 - a notification failure never undoes the analysis
            log.warning("analysis notification failed", extra={"session_id": sid, "error": repr(exc)})
