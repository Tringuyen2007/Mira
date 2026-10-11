"""Greeting and post-set summary (TRI §6.6). Runs off engine events, always in background tasks.

- Greeting: prepared when a session starts (Gemini FAST → validators → ElevenLabs), emitted as
  `greeting{text, audio_url}` on `arrived`. Fallback: the generic pre-recorded greeting cue, else a template.
- Summary: on `set_done`, the set's measured numbers → Gemini JSON {spoken, therapist_note} → validators →
  voice → stored (server.db.writes.update_session_ai when a database is attached) → `summary_ready`.
  Fallback: template text from the same numbers.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from server.ai import cue_library, eleven, gemini
from server.ai.prompts import load_prompt
from server.ai.validators import label_note, validate_json, validate_text
from server.logging_setup import get_logger
from server.state import LiveSession, SessionRegistry

log = get_logger("ai.voice")

AI_TIMEOUT_S = 4.0
GREETING_WAIT_S = 6.0  # on `arrived`, wait this long for a greeting still being prepared

# Plain-language reminder for the most frequent form flag in a set (template summaries).
FLAG_REMINDERS = {
    "knee_cave_l": "push your left knee out",
    "knee_cave_r": "push your right knee out",
    "knees_out": "push your knees out over your toes",
    "trunk_lean": "keep your chest up",
    "depth_more": "go a little deeper if it feels okay",
    "slow_down": "slow each rep down",
}


class SummaryOut(BaseModel):
    spoken: str
    therapist_note: str


@dataclass
class _SetStats:
    reps: int = 0
    good: int = 0
    flags: Counter[str] = field(default_factory=Counter)
    peak_hr: int | None = None


def _data_message(data: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": "DATA:\n" + json.dumps(data, ensure_ascii=False)}


def _numbers(value: Any) -> list[float]:
    """Every numeric leaf of a JSON-like value (the numbers the AI may use)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    return []


def _read_data(messages: list[dict[str, Any]]) -> dict[str, Any]:
    content = str(messages[0].get("content", ""))
    return json.loads(content.removeprefix("DATA:\n"))


def template_greeting(data: dict[str, Any]) -> str:
    return f"Hi {data.get('first_name') or 'there'}! Raise your hand when you're ready."


def template_spoken(data: dict[str, Any]) -> str:
    text = f"Set {data['set']} done: {data['good_form_reps']} of {data['reps']} reps with good form."
    flags: dict[str, int] = data.get("flags") or {}
    reminder = FLAG_REMINDERS.get(max(flags, key=lambda f: flags[f])) if flags else None
    text += f" Next set, try to {reminder}." if reminder else " Nice steady work."
    return text + " Take a good rest."


def template_note(data: dict[str, Any]) -> str:
    target = f" (target {data['reps_target']})" if data.get("reps_target") else ""
    flags = ", ".join(f"{k} ×{v}" for k, v in (data.get("flags") or {}).items()) or "none"
    pain = f" Pain today: {data['pain_today']}/10." if data.get("pain_today") is not None else ""
    return (f"Set {data['set']}: {data['reps']} reps{target}, {data['good_form_reps']} with good form. "
            f"Form flags: {flags}.{pain}")


def _fake_greeting(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
    data = _read_data(messages)
    reps = f" {data['reps_target']} reps today." if data.get("reps_target") else ""
    return gemini.Reply(text=f"Hi {data.get('first_name') or 'there'}!{reps} "
                             "Raise your hand when you're ready.")


def _fake_summary(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
    data = _read_data(messages)
    return gemini.Reply(text=json.dumps({
        "spoken": f"Nice work on set {data['set']}! {data['good_form_reps']} of {data['reps']} reps had good form.",
        "therapist_note": template_note(data),
    }))


gemini.register_fake("greeting", _fake_greeting)
gemini.register_fake("summary", _fake_summary)


def _default_db() -> Any | None:
    try:
        from server.state import get_state

        return get_state().db
    except RuntimeError:
        return None


class SessionVoice:
    def __init__(self, hub: Any, sessions: SessionRegistry, live: Any | None = None,
                 db_provider: Callable[[], Any | None] = _default_db) -> None:
        self.hub = hub
        self.sessions = sessions
        self.live = live  # bridge.live: plan targets seen so far (reps_target, sets_target)
        self.db_provider = db_provider
        self._greetings: dict[str, asyncio.Task[tuple[str, str | None, str]]] = {}
        self._sets: dict[tuple[str, int], _SetStats] = {}
        self._peak_hr: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- event entry point (bridge.subscribe_events) ------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        sid = ev.get("session_id")
        if not isinstance(sid, str):
            return
        if kind == "rep":
            stats = self._sets.setdefault((sid, int(ev.get("set") or 0)), _SetStats())
            stats.reps += 1
            stats.good += ev.get("quality") == "good"
            stats.flags.update(f for f in ev.get("flags") or [] if isinstance(f, str))
        elif kind == "hr" and isinstance(ev.get("bpm"), (int, float)):
            self._peak_hr[sid] = max(self._peak_hr.get(sid, 0), int(ev["bpm"]))
        elif kind == "arrived":
            self._spawn(self._emit_greeting(sid))
        elif kind == "set_done":
            stats = self._sets.pop((sid, int(ev.get("set") or 0)), _SetStats())
            stats.peak_hr = self._peak_hr.pop(sid, None)
            self._spawn(self._summarize(sid, ev, stats))

    def prepare_greeting(self, session: LiveSession) -> None:
        """Start writing and voicing the greeting now, so `arrived` can emit it immediately."""
        if session.session_id not in self._greetings:
            self._greetings[session.session_id] = self._spawn(self._make_greeting(session))

    async def close(self) -> None:
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
            log.error("voice task failed", extra={"error": repr(task.exception())})

    # ---- greeting -----------------------------------------------------------------------------

    async def _emit_greeting(self, sid: str) -> None:
        session = self.sessions.get(sid)
        if session is None:
            return
        self.prepare_greeting(session)
        try:
            text, audio_url, source = await asyncio.wait_for(asyncio.shield(self._greetings[sid]), GREETING_WAIT_S)
        except (asyncio.TimeoutError, TimeoutError):
            text, audio_url, source = self._fallback_greeting(self._greeting_data(session))
        except Exception as exc:  # noqa: BLE001 - a failed preparation still greets with the template
            log.warning("greeting preparation failed", extra={"session_id": sid, "error": repr(exc)})
            self._greetings.pop(sid, None)
            text, audio_url, source = template_greeting(self._greeting_data(session)), None, "template"
        self.hub.emit("greeting", {"session_id": sid, "t": round(time.time(), 3), "text": text,
                                   "audio_url": audio_url, "source": source})

    def _greeting_data(self, session: LiveSession) -> dict[str, Any]:
        data: dict[str, Any] = {"first_name": session.profile.first_name, "exercise": session.exercise_id}
        for key in ("reps_target", "sets_target"):
            value = getattr(self.live, key, None) if self.live is not None else None
            if isinstance(value, int):
                data[key] = value
        return data

    async def _make_greeting(self, session: LiveSession) -> tuple[str, str | None, str]:
        data = self._greeting_data(session)
        text: str | None = None
        try:
            reply = await gemini.ask("greeting", gemini.models.fast(), load_prompt("greeting"), [_data_message(data)],
                                     timeout_s=AI_TIMEOUT_S)
            text = validate_text(reply.text, "greeting", _numbers(data))
            if text is None:
                await gemini.discard(reply)
        except gemini.AIUnavailable as exc:
            log.info("greeting fallback", extra={"session_id": session.session_id, "reason": str(exc)})
        if text is None:
            return self._fallback_greeting(data)
        audio_url = await eleven.tts(text, f"greeting:{session.patient_id}")
        if audio_url is None:
            generic = cue_library.pick("greeting_generic")
            if generic and generic["audio_url"]:
                return generic["text"], generic["audio_url"], "template"
        return text, audio_url, "ai"

    @staticmethod
    def _fallback_greeting(data: dict[str, Any]) -> tuple[str, str | None, str]:
        generic = cue_library.pick("greeting_generic")
        if generic and generic["audio_url"]:
            return generic["text"], generic["audio_url"], "template"
        return template_greeting(data), None, "template"

    # ---- post-set summary ---------------------------------------------------------------------

    def _summary_data(self, session: LiveSession | None, ev: dict[str, Any], stats: _SetStats) -> dict[str, Any]:
        reps = ev.get("reps") if isinstance(ev.get("reps"), int) else stats.reps
        data: dict[str, Any] = {
            "exercise": ev.get("exercise_id") or (session.exercise_id if session else None),
            "set": int(ev.get("set") or 0),
            "reps": reps,
            "good_form_reps": min(stats.good, reps),
            "flags": dict(stats.flags),
        }
        for key in ("reps_target", "sets_target"):
            value = getattr(self.live, key, None) if self.live is not None else None
            if isinstance(value, int):
                data[key] = value
        # Optional measurements the engine may add to set_done.
        for key in ("rom_deg", "rom_target_deg", "load_pct", "duration_s"):
            if isinstance(ev.get(key), (int, float)) and not isinstance(ev.get(key), bool):
                data[key] = ev[key]
        if stats.peak_hr is not None:
            data["peak_hr"] = stats.peak_hr
        if session is not None and session.profile.pain_today is not None:
            data["pain_today"] = session.profile.pain_today
        return data

    async def _summarize(self, sid: str, ev: dict[str, Any], stats: _SetStats) -> None:
        session = self.sessions.get(sid)
        data = self._summary_data(session, ev, stats)
        allowed = _numbers(data)
        spoken: str | None = None
        note: str | None = None
        parsed, replies = await self._ask_summary(sid, data)
        if parsed is not None:
            spoken = validate_text(parsed.spoken, "spoken", allowed)
            note = validate_text(parsed.therapist_note, "note", allowed)
        # Never replay a rejected answer from the cache: keep only the reply that was fully accepted.
        rejected = replies if spoken is None or note is None else replies[:-1]
        for reply in rejected:
            await gemini.discard(reply)
        source = "ai" if spoken is not None else "template"
        spoken = spoken or template_spoken(data)
        note = label_note(note) if note is not None else template_note(data)
        audio_url = await eleven.tts(spoken, f"summary:{sid}:{data['set']}")
        await self._store(sid, spoken, note)
        self.hub.emit("summary_ready", {"session_id": sid, "t": round(time.time(), 3), "set": data["set"],
                                        "spoken": spoken, "therapist_note": note, "audio_url": audio_url,
                                        "source": source})

    async def _ask_summary(self, sid: str, data: dict[str, Any]) -> tuple[SummaryOut | None, list[gemini.Reply]]:
        """The parsed answer (or None) and every reply received, so rejected ones can be discarded."""
        messages = [_data_message(data)]
        system = load_prompt("summary")
        try:
            reply = await gemini.ask("summary", gemini.models.fast(), system, messages, timeout_s=AI_TIMEOUT_S,
                                     json_mode=True)
        except gemini.AIUnavailable as exc:
            log.info("summary fallback", extra={"session_id": sid, "reason": str(exc)})
            return None, []
        replies = [reply]

        async def retry(error: str) -> str | None:
            fix = {"role": "user", "content": f"Your answer was rejected ({error}). Return only the JSON object "
                                              "with string fields spoken and therapist_note."}
            try:
                again = await gemini.ask("summary", gemini.models.fast(), system,
                                         [*messages, reply.as_message(), fix], timeout_s=AI_TIMEOUT_S, json_mode=True)
            except gemini.AIUnavailable:
                return None
            replies.append(again)
            return again.text

        return await validate_json(reply.text, SummaryOut, retry), replies

    async def _store(self, sid: str, spoken: str, note: str) -> None:
        """Persist to sessions.ai_summary / ai_note through server.db.writes when available (best effort)."""
        db = self.db_provider()
        if db is None:
            return
        try:
            from server.db import writes  # type: ignore[import-not-found,attr-defined,unused-ignore]
        except ImportError:
            return
        update = getattr(writes, "update_session_ai", None)
        if update is None:
            return
        try:
            await asyncio.wait_for(update(db, sid, ai_summary=spoken, ai_note=note), 2.0)
        except Exception as exc:  # noqa: BLE001 - storing the summary must never break the event flow
            log.warning("summary store failed", extra={"session_id": sid, "error": repr(exc)})
