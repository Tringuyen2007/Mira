"""Safety Guardian (§7.2): fall / pain ≥ 7 / HR over limit → check-in → escalate → live updates → close.

Fixed rules only: no AI decides anything here, and nothing on this path waits on the database. The
patient profile is the one cached in memory at session start. One incident per session at a time;
the incident stays open after escalation until the call watcher calls `close()`.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from server.agents.incident_snapshot import build_snapshot, posture_phrase, status_text
from server.config import cfg
from server.logging_setup import get_logger, scrub
from server.notify import templates
from server.state import PatientProfile, default_profile, get_state

if TYPE_CHECKING:
    from server.engine_bridge import EngineBridge
    from server.hub import Hub
    from server.state import SessionRegistry

log = get_logger("guardian")

PAIN_THRESHOLD = 7
HR_SUSTAIN_S = 5.0
UPDATE_INTERVAL_S = 10.0
STILL_UPDATE_EVERY_S = 30.0
HR_UPDATE_DELTA_BPM = 15
SNAPSHOT_LOOKBACK_S = 5.0
NO_CALL_HOLD_S = 240.0  # no call to watch: keep the incident (Call 911 shown) this long, like a call watch
MAX_CLOCK_SKEW_S = 3600.0  # event times further than this from the server clock are not trusted
RESPONSE_GESTURES = ("thumbs_up", "raised_hand")
RETELL_STEPS = ("escalated", "call_update", "acknowledged", "therapist_call", "call_retry", "call_failed")

AlertHook = Callable[["Incident", str, "dict[str, Any] | None"], Any]


class Notifier(Protocol):
    def attach(self, guardian: SafetyGuardian) -> None: ...

    async def escalate(self, inc: Incident) -> dict[str, Any]: ...

    async def push_update(self, inc: Incident, text: str, status: str) -> bool: ...


@dataclass(eq=False)
class Incident:
    alert_id: str
    kind: str  # fall | pain | hr
    session_id: str | None
    profile: PatientProfile
    started_t: float  # engine/event time of the trigger (epoch seconds)
    trigger: dict[str, Any]
    state: str = "detected"  # detected | checking_in | resolved | escalated | closed
    checkins: int = 0
    responses: list[str] = field(default_factory=list)
    hr_before: int | None = None
    hr_now: int | None = None
    pain_level: int | None = None
    last_status: dict[str, Any] | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    escalation: dict[str, Any] | None = None  # notifier.escalate() result, None while escalating
    call_id: str | None = None
    call_state: str | None = None
    timer: asyncio.Task[None] | None = None
    # live-update bookkeeping (§8.6.4)
    pushed_facts: set[str] = field(default_factory=set)
    pending: dict[str, str] = field(default_factory=dict)
    pending_status: str | None = None
    last_push_at: float | None = None
    flush_task: asyncio.Task[None] | None = None
    hold_task: asyncio.Task[None] | None = None
    posture_now: str | None = None
    moving_now: bool | None = None
    still_baseline: float | None = None
    hr_at_update: int | None = None

    @property
    def patient_id(self) -> str:
        return self.profile.patient_id

    @property
    def first_name(self) -> str:
        return self.profile.first_name


def _session_key(ev: dict[str, Any]) -> str:
    return str(ev.get("session_id") or "")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _event_time(ev: dict[str, Any]) -> float:
    """The event's epoch time, or the server clock when it is missing or implausible (e.g. a client in ms)."""
    now = time.time()
    t = _number(ev.get("t"))
    return t if t is not None and abs(t - now) <= MAX_CLOCK_SKEW_S else now


class SafetyGuardian:
    def __init__(self, hub: Hub, bridge: EngineBridge, notifier: Notifier, sessions: SessionRegistry,
                 timeout_s: float, *, alert_hooks: list[AlertHook] | None = None,
                 update_interval_s: float = UPDATE_INTERVAL_S, no_call_hold_s: float = NO_CALL_HOLD_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.hub = hub
        self.bridge = bridge
        self.notifier = notifier
        self.sessions = sessions
        self.timeout_s = timeout_s
        self.alert_hooks = alert_hooks if alert_hooks is not None else []
        self.update_interval_s = update_interval_s
        self.no_call_hold_s = no_call_hold_s
        self.clock = clock
        self.incidents: dict[str, Incident] = {}  # session key → open incident
        self.hr_over_since: dict[str, float] = {}
        self.last_hr: dict[str, int] = {}
        self._background: set[asyncio.Task[Any]] = set()
        notifier.attach(self)

    # ---- event intake ----------------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind, name = ev.get("type"), ev.get("name")
        try:
            if kind == "fall" and ev.get("stage") == "confirmed":
                await self.start(ev, "fall")
            elif kind == "command" and name == "pain":
                level = _number((ev.get("payload") or {}).get("level"))
                if level is not None and level >= PAIN_THRESHOLD:
                    await self.start(ev, "pain", pause=True)
            elif kind == "hr":
                await self.on_hr(ev)
            elif (kind == "gesture" and name in RESPONSE_GESTURES) or (kind == "command" and name == "im_ok"):
                await self.patient_responded(ev)
            elif kind == "fall_status":
                await self.on_status(ev)
        except Exception:  # noqa: BLE001 - one bad event must never stop the safety path
            log.exception("guardian failed on event", extra={"event_type": kind, "session_id": ev.get("session_id")})

    async def on_hr(self, ev: dict[str, Any]) -> None:
        bpm, t = _number(ev.get("bpm")), _event_time(ev)
        if bpm is None:
            return
        key = _session_key(ev)
        self.last_hr[key] = int(bpm)
        inc = self.incidents.get(key)
        if inc is not None:
            inc.hr_now = int(bpm)
            if inc.state == "escalated":
                self._hr_update(inc)
        # HR rule: above the limit for ≥ 5 s, not one noisy reading.
        if bpm > self._profile(key).hr_limit:
            since = self.hr_over_since.setdefault(key, t)
            if t - since >= HR_SUSTAIN_S and key not in self.incidents:
                await self.start(ev, "hr", pause=True)
        else:
            self.hr_over_since.pop(key, None)

    async def start(self, ev: dict[str, Any], kind: str, pause: bool = False) -> None:
        key = _session_key(ev)
        if key in self.incidents:  # idempotent: one incident at a time (duplicate fall events)
            log.info("incident already open, ignoring trigger", extra={
                "alert_id": self.incidents[key].alert_id, "session_id": ev.get("session_id"), "trigger": kind})
            return
        level = _number((ev.get("payload") or {}).get("level")) if kind == "pain" else None
        inc = Incident(alert_id=str(uuid.uuid4()), kind=kind, session_id=ev.get("session_id"),
                       profile=self._profile(key), started_t=_event_time(ev),
                       trigger=dict(ev), hr_before=self.last_hr.get(key), hr_now=self.last_hr.get(key),
                       pain_level=int(level) if level is not None else None)
        self.incidents[key] = inc
        await self.log(inc, "detected", payload={"trigger": {k: v for k, v in ev.items() if k != "seq"}})
        if pause:
            self.bridge.inject({"type": "paused", "reason": kind, "session_id": inc.session_id})
        self._check_in(inc)
        inc.state = "checking_in"
        await self.log(inc, "checked_in")
        inc.timer = self._spawn(self._wait_then_escalate(inc), name=f"guardian-timer-{inc.alert_id}")

    async def patient_responded(self, ev: dict[str, Any]) -> None:
        inc = self.incidents.get(_session_key(ev))
        if inc is None:
            return
        response = str(ev.get("name") or "im_ok")
        inc.responses.append(response)
        if inc.state == "checking_in":  # before the timer: false alarm, close it (row kept)
            if inc.timer is not None:
                inc.timer.cancel()
            inc.state = "resolved"
            await self.log(inc, "resolved", payload={"response": response})
            self._end(inc)
        elif inc.state == "escalated":  # mid-call: keep the incident open and tell the caller
            await self.log(inc, "patient_responded", payload={"response": response})
            if inc.escalation is not None and not inc.escalation.get("contact_call_id"):
                await self.close(inc)  # no call was placed, so nothing will close it later
                return
            s = self.live_snapshot(inc)
            text = templates.response_update(inc.first_name, response, inc.profile.pronoun_poss)
            self.queue_update(inc, f"responded:{response}", text, status_text(s))

    async def on_status(self, ev: dict[str, Any]) -> None:
        inc = self.incidents.get(_session_key(ev))
        if inc is None:
            return
        inc.last_status = dict(ev)
        if inc.state != "escalated":
            return
        s = self.live_snapshot(inc)
        posture, moving, still = s["posture"], s["moving"], s["still_s"]
        name, be = inc.first_name, "are" if inc.profile.pronoun_subj == "they" else "is"
        status = status_text(s)
        if inc.still_baseline is None and still is not None and inc.call_state == "ongoing":
            inc.still_baseline = still  # "another 30 s" counts from when the contact is on the line
        if posture is not None and posture != inc.posture_now:
            phrase = posture_phrase(s) or posture
            self.queue_update(inc, f"posture:{posture}", templates.posture_update(name, phrase, be), status)
        if not inc.responses:  # once she has responded, moving/still are no longer news
            if moving is True and inc.moving_now is False:
                self.queue_update(inc, "moving", templates.moving_update(name), status)
            elif moving is not True and still is not None and inc.still_baseline is not None:
                periods = int((still - inc.still_baseline) // STILL_UPDATE_EVERY_S)
                if periods >= 1:
                    self.queue_update(inc, f"still:{periods}", templates.still_update(name, be), status)
        if posture is not None:
            inc.posture_now = posture
        if moving is not None:
            inc.moving_now = moving

    def _hr_update(self, inc: Incident) -> None:
        if inc.hr_now is None:
            return
        if inc.hr_at_update is None:
            inc.hr_at_update = inc.hr_now
            return
        if abs(inc.hr_now - inc.hr_at_update) > HR_UPDATE_DELTA_BPM:
            inc.hr_at_update = inc.hr_now
            text = templates.hr_update(inc.profile.pronoun_poss, inc.hr_now)
            self.queue_update(inc, f"hr:{inc.hr_now}", text, status_text(self.live_snapshot(inc)))

    # ---- escalation ------------------------------------------------------------------------------

    async def _wait_then_escalate(self, inc: Incident) -> None:
        await asyncio.sleep(self.timeout_s)
        if inc.state != "checking_in":  # race guard: resolved meanwhile
            return
        inc.state = "escalated"
        try:
            inc.snapshot = self.live_snapshot(inc)
            inc.posture_now, inc.moving_now = inc.snapshot["posture"], inc.snapshot["moving"]
            inc.hr_at_update = inc.hr_now
        except Exception:  # noqa: BLE001 - a snapshot bug must not stop the call; call_variables falls back
            log.exception("snapshot failed", extra={"alert_id": inc.alert_id})
        try:
            result = await self.notifier.escalate(inc)  # retries inside; starts the call watcher
        except Exception as exc:  # noqa: BLE001 - the contract says never raises; don't trust it here
            log.exception("notifier.escalate raised", extra={"alert_id": inc.alert_id})
            result = {"error": repr(exc)[:300]}
        inc.escalation = result
        await self.log(inc, "escalated", payload={"retell": result, "snapshot": inc.snapshot})
        if not result.get("contact_call_id") and inc.state == "escalated":
            # No call watcher will ever close this incident: close now if she already responded, else after
            # a bounded hold, so a later fall in this session is never ignored.
            if inc.responses:
                await self.close(inc)
            else:
                inc.hold_task = self._spawn(self._close_after(inc, self.no_call_hold_s),
                                            name=f"guardian-hold-{inc.alert_id}")

    async def _close_after(self, inc: Incident, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        await self.close(inc, reason="no_call_hold_elapsed")

    def live_snapshot(self, inc: Incident) -> dict[str, Any]:
        frames = self.bridge.ring_buffer.frames_since(inc.started_t - SNAPSHOT_LOOKBACK_S)
        return build_snapshot(inc, frames, inc.profile)

    # ---- live updates (§8.6.4): ≤ 1 push per interval, merged, never the same fact twice ---------------

    def queue_update(self, inc: Incident, fact: str, text: str, status: str) -> None:
        if fact in inc.pushed_facts or fact in inc.pending:
            return
        inc.pending[fact] = text
        inc.pending_status = status
        self.flush_updates(inc)

    def flush_updates(self, inc: Incident) -> None:
        """Start pushing pending facts in the background. Never waits on the network: the push loop is its own
        task, so event intake is never held up by update_live. The notifier calls this when the call connects."""
        if inc.pending and (inc.flush_task is None or inc.flush_task.done()):
            inc.flush_task = self._spawn(self._push_pending(inc), name=f"guardian-updates-{inc.alert_id}")

    async def _push_pending(self, inc: Incident) -> None:
        while inc.pending and inc.state == "escalated" and inc.call_state == "ongoing":
            if inc.last_push_at is not None:
                wait = self.update_interval_s - (self.clock() - inc.last_push_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue
            facts, text = list(inc.pending), " ".join(inc.pending.values())
            status = inc.pending_status or templates.DEFAULT_CURRENT_STATUS
            inc.pending.clear()
            inc.last_push_at = self.clock()
            if await self.notifier.push_update(inc, text, status):
                inc.pushed_facts.update(facts)

    # ---- closing ---------------------------------------------------------------------------------

    async def close(self, inc: Incident, reason: str | None = None) -> None:
        """Called by the call watcher when the call is over (§8.4). Idempotent.

        A push already in flight is left to finish (its `call_update` step is then logged); the push loop
        stops by itself once the incident is closed.
        """
        if inc.state == "closed":
            return
        inc.state = "closed"
        current = asyncio.current_task()
        for task in (inc.timer, inc.hold_task):
            if task is not None and task is not current:
                task.cancel()
        await self.log(inc, "closed", payload={"reason": reason} if reason else None)
        self._end(inc)

    def _end(self, inc: Incident) -> None:
        key = str(inc.session_id or "")
        if self.incidents.get(key) is inc:
            self.incidents.pop(key)  # a later fall in the same session can open a new incident
        self.hr_over_since.pop(key, None)
        if inc.kind == "fall":
            self.bridge.send_to_engine({"type": "incident_closed"})

    # ---- outputs ---------------------------------------------------------------------------------

    def _check_in(self, inc: Incident) -> None:
        inc.checkins += 1
        sid = inc.session_id
        now = round(time.time(), 3)
        self.hub.emit("cue", {"id": templates.CHECKIN_CUE_ID, "text": templates.CHECKIN_TEXT,
                              "audio_url": templates.CHECKIN_AUDIO_URL, "session_id": sid, "t": now})
        self.hub.emit("haptic", {"pattern": templates.CHECKIN_HAPTIC, "session_id": sid, "t": now})

    async def log(self, inc: Incident, step: str, payload: dict[str, Any] | None = None) -> None:
        """One alert step: alerts row (fire-and-forget) + `alert` WS event + JSON log + hooks. Never yields."""
        safe = scrub(payload, []) if payload is not None else None
        p = inc.profile
        self.hub.emit("alert", {
            "alert_id": inc.alert_id, "kind": inc.kind, "step": step, "patient_id": inc.patient_id,
            "session_id": inc.session_id, "call_id": inc.call_id,
            "message": templates.alert_message(inc.kind, step, p.first_name, p.pronoun_subj),
            "show_call_911": inc.state == "escalated", "t": round(time.time(), 3),
        })
        log.info(f"alert {step}", extra={"alert_id": inc.alert_id, "session_id": inc.session_id,
                                         "call_id": inc.call_id, "kind": inc.kind, "step": step,
                                         "payload": safe})
        self._persist(inc, step, safe)
        for hook in self.alert_hooks:
            try:
                result = hook(inc, step, safe)
                if inspect.isawaitable(result):
                    self._spawn(result, name="guardian-alert-hook")
            except Exception:  # noqa: BLE001 - a listener (e.g. notifications) must not break the safety path
                log.exception("alert hook failed", extra={"alert_id": inc.alert_id, "step": step})

    def _persist(self, inc: Incident, step: str, payload: dict[str, Any] | None) -> None:
        try:
            from server.db import writes  # F1; absent or without a database → no-op
            db = get_state().db
        except (ImportError, RuntimeError):
            return
        if db is None:
            return
        channel = "retell" if step.startswith(("call_", "webhook_")) or step in RETELL_STEPS else "device"
        self._spawn(writes.insert_alert_step(db, datetime.now(UTC), inc.alert_id, inc.patient_id, inc.session_id,
                                             inc.kind, step, channel, payload), name="guardian-alert-row")

    def _spawn(self, awaitable: Any, *, name: str) -> asyncio.Task[Any]:
        """A tracked background task whose failure is always logged (never silently swallowed)."""
        task = asyncio.ensure_future(awaitable)
        task.set_name(name)
        self._background.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("guardian background task failed", extra={"task": task.get_name(),
                                                                "error": repr(task.exception())})

    def _profile(self, key: str) -> PatientProfile:
        session = self.sessions.get(key) if key else None
        session = session or self.sessions.current
        if session is not None:
            return session.profile
        return default_profile(cfg.MOCK_PATIENT_ID)

    async def stop(self) -> None:
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
