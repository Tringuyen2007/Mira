"""TrainerVoice: the LCD's trainer voice. Maps engine and hub events to pre-recorded lines and emits `say`.

No AI and no network: every line is a scripted cue picked from the cue library (`cue_library.pick`), voiced
once offline by the CLI generator, so the running server never spends ElevenLabs free-tier characters here.
The kiosk plays `audio_url`, shows `text` as the caption and animates the mascot while she talks.

Event -> line (spec §3, docs/superpowers/specs/2026-10-11-lcd-agent-design.md):
- `awaiting_gesture` -> `raise_hand`; `gesture` -> `lets_go`; `countdown{n}` -> `countdown_{n}` (3, 2, 1 only).
- `rep{quality: "good", index: i}` -> `count_{min(i, 20)}`, then `halfway` at `i == ceil(target / 2)` (when
  `i < target - 1`) and `last_rep` at `i == target - 1`. The target comes from the rep itself or the last
  `countdown`/`arrived` of the session; with no known target both extras are skipped. Other reps are silent
  (the correction `cue` already speaks).
- `paused` -> `pause`; `resumed` -> `resume`; `session_summary` -> `session_done`.
- `set_done` -> `set_done`, but only when no `summary_ready` with audio arrives for that session within 1 s
  (SessionVoice's own summary audio wins when runtime TTS is on).
- `alert` steps narrate the emergency call: `escalated`/`call_registered` -> `calling_contact`,
  `call_ongoing` -> `contact_on_call`, `call_failed`/`call_not_connected`/`call_error` -> `call_failed`
  (each line once per alert).

Never talks over safety: a confirmed `fall`, the Guardian's `check_in` cue or `alert detected` opens an
incident hold for the session, during which only the call-flow lines are spoken; a closing alert step
(`resolved`, `closed`, `resolved_by_therapist`) lifts it. The same line id is not repeated within 1.5 s.

Engine events arrive through `bridge.subscribe_events(on_event)`; agent events (`alert`, `cue`,
`session_summary`, `summary_ready`, sent with `hub.emit`) through `hub.emit_observers` (`observe`). An event seen
on both streams is handled once.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from server.ai import cue_library
from server.engine_bridge import CLOSED_ALERT_STEPS
from server.hub import Hub
from server.logging_setup import get_logger

log = get_logger("trainer_voice")

DEDUPE_WINDOW_S = 1.5
SET_DONE_WAIT_S = 1.0
MAX_COUNT = 20
SEEN_EVENTS_MAX = 256
COUNTDOWN_LINES = {3: "countdown_3", 2: "countdown_2", 1: "countdown_1"}
SIMPLE_LINES = {"awaiting_gesture": "raise_hand", "gesture": "lets_go", "paused": "pause", "resumed": "resume",
                "session_summary": "session_done"}
CALL_LINES = {"escalated": "calling_contact", "call_registered": "calling_contact",
              "call_ongoing": "contact_on_call",
              "call_failed": "call_failed", "call_not_connected": "call_failed", "call_error": "call_failed"}
# Agent events (hub.emit) this agent needs; engine events come through the bridge subscription.
AGENT_EVENT_TYPES = frozenset({"alert", "cue", "session_summary", "summary_ready"})

Pick = Callable[[str], "dict[str, Any] | None"]


class TrainerVoice:
    """Event -> scripted line -> `say` hub event. Construct once; feed it events from both streams."""

    def __init__(self, hub: Hub, sessions: Any, *, pick: Pick = cue_library.pick,
                 now: Callable[[], float] = time.time) -> None:
        self.hub = hub
        self.sessions = sessions
        self._pick = pick
        self._now = now
        self._incident: set[str | None] = set()
        self._reps_target: dict[str | None, int] = {}
        self._last_said: dict[tuple[str | None, str], float] = {}
        self._call_lines_said: set[tuple[Any, str]] = set()
        self._set_done_tasks: dict[str | None, asyncio.Task[None]] = {}
        self._seen: deque[tuple[Any, ...]] = deque(maxlen=SEEN_EVENTS_MAX)

    # ---- inputs ----------------------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        """Bridge subscriber (engine events) and the target of `observe` (agent events)."""
        self._handle(ev)

    def observe(self, ev: dict[str, Any]) -> None:
        """`hub.emit_observers` hook: sync, so the agent event is handled on the next loop turn (after the
        triggering event is published, so the `say` follows it on the wire)."""
        if ev.get("type") not in AGENT_EVENT_TYPES:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(self._handle, dict(ev))

    async def close(self) -> None:
        tasks = list(self._set_done_tasks.values())
        self._set_done_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- mapping ---------------------------------------------------------------------------------

    def _handle(self, ev: dict[str, Any]) -> None:
        try:
            self._dispatch(ev)
        except Exception:  # noqa: BLE001 - a voice bug must never break the event stream
            log.exception("trainer voice failed", extra={"event_type": ev.get("type")})

    def _dispatch(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "say" or self._already_seen(ev):
            return
        sid = self._session_id(ev)
        self._track_target(sid, ev)
        if kind == "alert":
            self._on_alert(sid, ev)
            return
        if (kind == "fall" and ev.get("stage") == "confirmed") or (kind == "cue" and ev.get("id") == "check_in"):
            self._open_incident(sid)
            return
        if kind == "summary_ready":
            if ev.get("audio_url"):
                task = self._set_done_tasks.pop(sid, None)
                if task is not None:
                    task.cancel()
            return
        if sid in self._incident:
            return
        if kind in SIMPLE_LINES:
            self._say(sid, SIMPLE_LINES[kind])
        elif kind == "countdown":
            line = COUNTDOWN_LINES.get(ev.get("n"))  # type: ignore[arg-type]
            if line is not None:
                self._say(sid, line)
        elif kind == "rep":
            self._on_rep(sid, ev)
        elif kind == "set_done":
            self._schedule_set_done(sid)

    def _on_rep(self, sid: str | None, ev: dict[str, Any]) -> None:
        index = ev.get("index")
        if ev.get("quality") != "good" or not isinstance(index, int) or isinstance(index, bool) or index < 1:
            return
        self._say(sid, f"count_{min(index, MAX_COUNT)}")
        target = self._reps_target.get(sid)
        if target is None:
            return
        if index == math.ceil(target / 2) and index < target - 1:
            self._say(sid, "halfway")
        elif index == target - 1:
            self._say(sid, "last_rep")

    def _on_alert(self, sid: str | None, ev: dict[str, Any]) -> None:
        step = ev.get("step")
        if step == "detected":
            self._open_incident(sid)
        elif step in CLOSED_ALERT_STEPS:
            if ev.get("session_id") is None:  # e.g. a dashboard resolve: the LCD runs one session at a time
                self._incident.clear()
            else:
                self._incident.discard(sid)
        line = CALL_LINES.get(str(step))
        if line is None:
            return
        key = (ev.get("alert_id") or sid, line)
        if key in self._call_lines_said:  # escalated + call_registered both mean "calling": say it once
            return
        if self._say(sid, line):
            self._call_lines_said.add(key)

    def _open_incident(self, sid: str | None) -> None:
        self._incident.add(sid)
        task = self._set_done_tasks.pop(sid, None)
        if task is not None:
            task.cancel()

    def _schedule_set_done(self, sid: str | None) -> None:
        old = self._set_done_tasks.pop(sid, None)
        if old is not None:
            old.cancel()
        task = asyncio.get_running_loop().create_task(self._set_done_later(sid), name="trainer-set-done")
        self._set_done_tasks[sid] = task

    async def _set_done_later(self, sid: str | None) -> None:
        await asyncio.sleep(SET_DONE_WAIT_S)
        if self._set_done_tasks.get(sid) is asyncio.current_task():
            del self._set_done_tasks[sid]
        if sid not in self._incident:
            self._say(sid, "set_done")

    # ---- helpers ---------------------------------------------------------------------------------

    def _say(self, sid: str | None, line_id: str) -> bool:
        now = self._now()
        last = self._last_said.get((sid, line_id))
        if last is not None and now - last < DEDUPE_WINDOW_S:
            return False
        line = self._pick(line_id)
        if not line:
            return False
        self._last_said[(sid, line_id)] = now
        self.hub.emit("say", {"session_id": sid, "t": round(now, 3), "id": line.get("id", line_id),
                              "text": line.get("text"), "audio_url": line.get("audio_url")})
        return True

    def _session_id(self, ev: dict[str, Any]) -> str | None:
        sid = ev.get("session_id")
        if sid is None and ev.get("type") != "alert":
            current = getattr(self.sessions, "current", None)
            sid = getattr(current, "session_id", None)
        return sid

    def _track_target(self, sid: str | None, ev: dict[str, Any]) -> None:
        target = ev.get("reps_target")
        if isinstance(target, int) and not isinstance(target, bool) and target > 0:
            self._reps_target[sid] = target

    def _already_seen(self, ev: dict[str, Any]) -> bool:
        """The same event reaching us on both streams (bridge + hub observer) is handled once."""
        t = ev.get("t")
        if t is None:
            return False
        key = (ev.get("type"), ev.get("session_id"), t, ev.get("step"), ev.get("id"), ev.get("index"), ev.get("n"))
        if key in self._seen:
            return True
        self._seen.append(key)
        return False
