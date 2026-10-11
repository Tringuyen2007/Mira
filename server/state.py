"""Shared in-process state: patient profiles, live sessions and the AppState container."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from server.config import Config, cfg
from server.logging_setup import get_logger

if TYPE_CHECKING:
    from server.engine_bridge import EngineBridge
    from server.hub import Hub

log = get_logger("sessions")

STORE_TIMEOUT_S = 1.0
MAX_ENDED_SESSIONS = 50  # _sessions/ended are pruned to this many most-recently-ended sessions on stop()


@dataclass
class PatientProfile:
    """Cached at session start; the Guardian never queries the DB during escalation."""

    patient_id: str
    first_name: str
    pronoun_subj: str
    pronoun_obj: str
    pronoun_poss: str
    phone: str | None
    contact_name: str | None
    contact_phone: str | None
    therapist_id: str | None
    therapist_name: str | None
    therapist_phone: str | None
    condition: str | None
    affected_side: str | None
    hr_limit: int
    weeks_since_surgery: int | None
    pain_today: int | None
    falls_30d: int
    age: int | None = None
    device_prefs: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveSession:
    session_id: str
    patient_id: str
    exercise_id: str
    started_at: float
    profile: PatientProfile
    origin: str = "rest"  # rest (phone/dashboard) | auto (MockAutoRestart) | boot (mock mode start-up)


def default_profile(patient_id: str) -> PatientProfile:
    """Fallback profile when the database is unavailable. Uses the demo phone numbers from config."""
    if patient_id == cfg.MOCK_PATIENT_ID or patient_id == "maya":
        return PatientProfile(
            patient_id=patient_id,
            first_name="Maya",
            pronoun_subj="she",
            pronoun_obj="her",
            pronoun_poss="her",
            phone=cfg.DEMO_PATIENT_PHONE,
            contact_name=cfg.DEMO_CONTACT_NAME,
            contact_phone=cfg.DEMO_EMERGENCY_PHONE,
            therapist_id="okafor",
            therapist_name="Dr. Okafor",
            therapist_phone=cfg.DEMO_THERAPIST_PHONE,
            condition="right knee replacement",
            affected_side="right",
            hr_limit=130,
            weeks_since_surgery=3,
            pain_today=None,
            falls_30d=0,
            age=67,
        )
    return PatientProfile(
        patient_id=patient_id,
        first_name="the patient",
        pronoun_subj="they",
        pronoun_obj="them",
        pronoun_poss="their",
        phone=None,
        contact_name=None,
        contact_phone=cfg.DEMO_EMERGENCY_PHONE,
        therapist_id="okafor",
        therapist_name="Dr. Okafor",
        therapist_phone=cfg.DEMO_THERAPIST_PHONE,
        condition=None,
        affected_side=None,
        hr_limit=130,
        weeks_since_surgery=None,
        pain_today=None,
        falls_30d=0,
    )


class SessionStore(Protocol):
    """Persistence seam for the SessionRegistry. The database feature (F1) provides the implementation."""

    async def load_profile(self, patient_id: str) -> PatientProfile | None: ...

    async def session_started(self, session: LiveSession) -> None: ...

    async def session_stopped(self, session: LiveSession) -> None: ...


class SessionRegistry:
    """Tracks the live session. Works without a store (no database): profiles fall back to defaults."""

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store
        self.current: LiveSession | None = None
        self._sessions: dict[str, LiveSession] = {}
        # Called with the new LiveSession after every start() (boot or REST), e.g. to restart the mock
        # engine's program and to warm up the greeting (server.ai.session_voice.SessionVoice.prepare_greeting).
        self.on_start: list[Callable[[LiveSession], None]] = []
        self.ended: set[str] = set()
        self._ended_order: list[str] = []  # oldest-first, for pruning _sessions/ended in stop()
        # Called once per session after it ends (REST stop, engine `stopped`, last set, shutdown). Sync hooks run
        # inline; a coroutine result is scheduled as a task — stop() never waits on the analyst.
        self.on_stop: list[Callable[[LiveSession], Any]] = []
        # Shared by start() and start_if_idle() so two concurrent callers (e.g. a REST start racing the mock
        # auto-restart) can never both see `current is None` and both start a session.
        self._start_lock = asyncio.Lock()

    async def start(self, patient_id: str, exercise_id: str, origin: str = "rest") -> LiveSession:
        async with self._start_lock:
            return await self._start_locked(patient_id, exercise_id, origin)

    async def start_if_idle(self, patient_id: str, exercise_id: str) -> LiveSession | None:
        """Like start(), but returns None instead of starting when a session is already current. Holds the
        same lock as start(), so exactly one caller among concurrent start()/start_if_idle() calls wins."""
        async with self._start_lock:
            if self.current is not None:
                return None
            return await self._start_locked(patient_id, exercise_id, "auto")

    async def _start_locked(self, patient_id: str, exercise_id: str, origin: str) -> LiveSession:
        profile = await self._load_profile(patient_id)
        session = LiveSession(
            session_id=str(uuid.uuid4()),
            patient_id=patient_id,
            exercise_id=exercise_id,
            started_at=time.time(),
            profile=profile,
            origin=origin,
        )
        self._sessions[session.session_id] = session
        self.current = session
        await self._store_call("session_started", session)
        for hook in self.on_start:
            try:
                hook(session)
            except Exception as exc:  # noqa: BLE001 - a hook bug must not break session start
                log.warning("session start hook failed", extra={"session_id": session.session_id,
                                                                 "error": repr(exc)})
        log.info("session started", extra={"session_id": session.session_id, "patient_id": patient_id,
                                            "exercise_id": exercise_id})
        return session

    def is_ended(self, session_id: str) -> bool:
        return session_id in self.ended

    async def stop(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or session_id in self.ended:
            return
        self.ended.add(session_id)
        self._ended_order.append(session_id)
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
        self._prune_ended()
        log.info("session stopped", extra={"session_id": session_id})

    def _prune_ended(self) -> None:
        """Keep only the MAX_ENDED_SESSIONS most-recently-ended sessions; `current` is never in `ended` (it
        was cleared above), so this can never evict it."""
        while len(self._ended_order) > MAX_ENDED_SESSIONS:
            oldest = self._ended_order.pop(0)
            self.ended.discard(oldest)
            self._sessions.pop(oldest, None)

    def get(self, session_id: str) -> LiveSession | None:
        return self._sessions.get(session_id)

    async def _load_profile(self, patient_id: str) -> PatientProfile:
        if self.store is not None:
            try:
                loaded = await asyncio.wait_for(self.store.load_profile(patient_id), STORE_TIMEOUT_S)
                if loaded is not None:
                    return loaded
            except Exception as exc:  # noqa: BLE001 - any store failure falls back to the default profile
                log.warning("profile load failed, using default", extra={"patient_id": patient_id,
                                                                         "error": repr(exc)})
        return default_profile(patient_id)

    async def _store_call(self, method: str, session: LiveSession) -> None:
        if self.store is None:
            return
        try:
            await asyncio.wait_for(getattr(self.store, method)(session), STORE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - persistence must never break the live session
            log.warning(f"session store {method} failed", extra={"session_id": session.session_id,
                                                                  "error": repr(exc)})


def _log_hook_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.warning("async session stop hook failed", extra={"error": repr(task.exception())})


StatProvider = Callable[[], Any | Awaitable[Any]]


class AppState:
    """One instance, built in main.py lifespan. Features attach their services as attributes."""

    def __init__(self, cfg: Config, hub: Hub, bridge: EngineBridge, sessions: SessionRegistry,
                 db: Any | None = None) -> None:
        self.cfg = cfg
        self.hub = hub
        self.bridge = bridge
        self.sessions = sessions
        self.db = db
        # GET /stats returns {name: provider()} for every entry; features register their own.
        self.stat_providers: dict[str, StatProvider] = {}
        # Guardian calls each hook with (incident, step, payload) after every alert step.
        self.on_alert_step: list[Callable[..., Any]] = []
        self.guardian: Any | None = None
        self.notifier: Any | None = None
        self.coach: Any | None = None
        self.scheduler: Any | None = None
        self.notifications: Any | None = None
        self.analyst: Any | None = None  # server.agents.session_analyst.SessionAnalyst
        self.devices: Any = None
        # Per-patient limits on patient-scope actions (server.api.ratelimit): one per app, so tests start fresh.
        from server.api.ratelimit import PATIENT_ACTIONS_PER_MIN, PerKeyLimiter  # local: api imports state

        self.patient_limiter = PerKeyLimiter(PATIENT_ACTIONS_PER_MIN)


_state: AppState | None = None


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialised (server lifespan has not started)")
    return _state


def set_state(s: AppState | None) -> None:
    global _state
    _state = s
