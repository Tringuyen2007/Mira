"""Shared in-process state: patient profiles, live sessions and the AppState container."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from server.config import Config, cfg
from server.logging_setup import get_logger

if TYPE_CHECKING:
    from server.engine_bridge import EngineBridge
    from server.hub import Hub

log = get_logger("sessions")

STORE_TIMEOUT_S = 1.0


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


@dataclass
class LiveSession:
    session_id: str
    patient_id: str
    exercise_id: str
    started_at: float
    profile: PatientProfile


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
            contact_name="Linh",
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

    async def start(self, patient_id: str, exercise_id: str) -> LiveSession:
        profile = await self._load_profile(patient_id)
        session = LiveSession(
            session_id=str(uuid.uuid4()),
            patient_id=patient_id,
            exercise_id=exercise_id,
            started_at=time.time(),
            profile=profile,
        )
        self._sessions[session.session_id] = session
        self.current = session
        await self._store_call("session_started", session)
        log.info("session started", extra={"session_id": session.session_id, "patient_id": patient_id,
                                            "exercise_id": exercise_id})
        return session

    async def stop(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if self.current is session:
            self.current = None
        await self._store_call("session_stopped", session)
        log.info("session stopped", extra={"session_id": session_id})

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


_state: AppState | None = None


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialised (server lifespan has not started)")
    return _state


def set_state(s: AppState | None) -> None:
    global _state
    _state = s
