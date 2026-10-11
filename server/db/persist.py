"""Glue between the live server and the database: the SessionStore behind SessionRegistry, event persistence
(rep → reps, rep flags → form_events, pain/effort → sessions) and the cached /stats provider."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from server.db import queries, writes
from server.db.pool import Database, DBError
from server.db.writer import FrameWriter
from server.logging_setup import get_logger
from server.state import LiveSession, PatientProfile, default_profile

log = get_logger("db.persist")

# Rep events carry quality as a word (§4.3); reps.quality is a number.
QUALITY_SCORES = {"good": 1.0, "fix_form": 0.5, "poor": 0.0}
REP_METRICS = ("peak_angle", "rom", "hold_s", "asym_avg", "asym_max", "smoothness")
FORM_EVENT_SEVERITY = "warning"
UNSAVED_RETRY_S = 2.0  # how often to retry a session row that couldn't be inserted


def _first_name(name: str) -> str:
    return name.split()[0] if name.strip() else name


def profile_from_row(row: dict[str, Any]) -> PatientProfile:
    """DB row → PatientProfile. Fields the schema doesn't store (pronouns, weeks since surgery) come from the
    config default for the demo persona, else neutral values."""
    base = default_profile(row["id"])
    known = base.first_name != "the patient"
    return PatientProfile(
        patient_id=row["id"],
        first_name=_first_name(row["name"]),
        pronoun_subj=base.pronoun_subj if known else "they",
        pronoun_obj=base.pronoun_obj if known else "them",
        pronoun_poss=base.pronoun_poss if known else "their",
        phone=None,
        contact_name=row.get("emergency_contact_name"),
        contact_phone=row.get("emergency_contact_phone"),
        therapist_id=row.get("therapist_id"),
        therapist_name=row.get("therapist_name"),
        therapist_phone=row.get("therapist_phone"),
        condition=row.get("condition"),
        affected_side=row.get("affected_side"),
        hr_limit=int(row.get("hr_limit") or 130),
        weeks_since_surgery=base.weeks_since_surgery if known else None,
        pain_today=row.get("pain_today"),
        falls_30d=int(row.get("falls_30d") or 0),
        age=row.get("age"),
        device_prefs=row.get("device_prefs") or {},
    )


class SessionPersistence:
    """SessionStore for SessionRegistry plus an event subscriber for the engine bridge."""

    def __init__(self, db: Database, sessions: Any = None) -> None:
        self.db = db
        self.sessions = sessions  # SessionRegistry, set after construction (it needs this store first)
        self._rep_counts: dict[str, int] = {}
        # Sessions whose row isn't confirmed in the database yet (insert failed, timed out or was cancelled by
        # the registry's 1 s bound). Retried before every later write for that session, so reps (FK) and the
        # session itself survive a database outage at session start.
        self._unsaved: dict[str, LiveSession] = {}
        self._retry_after: dict[str, float] = {}
        self._last_hr_at: dict[str, float] = {}  # session_id → monotonic time of the last persisted hr row

    # ---- SessionStore -------------------------------------------------------------------------------------

    async def load_profile(self, patient_id: str) -> PatientProfile | None:
        row = await queries.profile_row(self.db, patient_id)
        return profile_from_row(row) if row else None

    async def session_started(self, session: LiveSession) -> None:
        self._rep_counts[session.session_id] = 0
        self._unsaved[session.session_id] = session  # marked first: the registry may cancel the insert
        await self._ensure_saved(session.session_id, force=True)

    async def session_stopped(self, session: LiveSession) -> None:
        reps = self._rep_counts.pop(session.session_id, None)
        ended_at = time.time()
        await self._ensure_saved(session.session_id, force=True)
        self._unsaved.pop(session.session_id, None)
        self._retry_after.pop(session.session_id, None)
        await writes.end_session(self.db, session.session_id, ended_at, reps_done=reps)

    async def _ensure_saved(self, session_id: str, *, force: bool = False) -> None:
        session = self._unsaved.get(session_id)
        if session is None or (not force and time.monotonic() < self._retry_after.get(session_id, 0.0)):
            return
        self._retry_after[session_id] = time.monotonic() + UNSAVED_RETRY_S
        if await writes.insert_session(self.db, session.session_id, session.patient_id, session.exercise_id,
                                       session.started_at):
            self._unsaved.pop(session_id, None)
            self._retry_after.pop(session_id, None)

    # ---- events -------------------------------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        session_id = ev.get("session_id")
        if not session_id:
            return
        kind = ev.get("type")
        if session_id in self._unsaved:
            await self._ensure_saved(session_id)
        if kind == "rep":
            await self._on_rep(session_id, ev)
        elif kind == "command" and ev.get("name") == "pain":
            await self._on_pain(session_id, ev.get("payload") or {})
        elif kind == "hr":
            self._on_hr(session_id, ev)

    async def _on_rep(self, session_id: str, ev: dict[str, Any]) -> None:
        # Rep events count per set (index 1..N); reps.rep is the running number within the session.
        rep_no = self._rep_counts.get(session_id, 0) + 1
        self._rep_counts[session_id] = rep_no
        quality = ev.get("quality")
        score = float(quality) if isinstance(quality, (int, float)) else QUALITY_SCORES.get(str(quality))
        flags = [str(f) for f in ev.get("flags") or []]
        await writes.insert_rep(self.db, session_id, rep_no, set_no=ev.get("set"), started_at=ev.get("started_at"),
                                ended_at=ev.get("t"), quality=score, flags=flags,
                                **{k: ev.get(k) for k in REP_METRICS})
        patient_id = self._patient_of(session_id)
        for flag in flags:
            await writes.insert_form_event(self.db, ev.get("t"), patient_id, session_id, rep_no, flag,
                                           FORM_EVENT_SEVERITY)

    async def _on_pain(self, session_id: str, payload: dict[str, Any]) -> None:
        """command {name: pain, payload: {level, when?: pre|post, effort?}}; `when` defaults to pre before the
        first rep and post after it."""
        level, effort = payload.get("level"), payload.get("effort")
        when = payload.get("when") or ("post" if self._rep_counts.get(session_id) else "pre")
        level = int(level) if isinstance(level, (int, float)) else None
        effort = int(effort) if isinstance(effort, (int, float)) else None
        if level is None and effort is None:
            return
        await writes.record_session_feedback(self.db, session_id, effort=effort,
                                             pain_pre=level if when == "pre" else None,
                                             pain_post=level if when == "post" else None)

    def _on_hr(self, session_id: str, ev: dict[str, Any]) -> None:
        """At most one `heart_rate` row per second per session: fire-and-forget, never awaited."""
        bpm = ev.get("bpm")
        if not isinstance(bpm, (int, float)) or isinstance(bpm, bool):
            return
        now = time.monotonic()
        if now - self._last_hr_at.get(session_id, 0.0) < 1.0:
            return
        self._last_hr_at[session_id] = now
        writes.fire(writes.insert_heart_rate(self.db, ev.get("t"), self._patient_of(session_id), session_id,
                                             int(bpm)))

    def _patient_of(self, session_id: str) -> str | None:
        session = self.sessions.get(session_id) if self.sessions is not None else None
        return session.patient_id if session else None


class DbStats:
    """/stats provider for Tiger: cached, refreshed in the background, so /stats never waits on the database."""

    REFRESH_S = 30.0
    FIRST_WAIT_S = 1.5

    def __init__(self, db: Database, writer: FrameWriter | None, patient_id: str) -> None:
        self.db = db
        self.writer = writer
        self.patient_id = patient_id
        self._cache: dict[str, Any] = {}
        self._refreshed_at = 0.0
        self._task: asyncio.Task[None] | None = None

    async def __call__(self) -> dict[str, Any]:
        if time.monotonic() - self._refreshed_at > self.REFRESH_S and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._refresh())
        if not self._cache and self._task is not None:
            await asyncio.wait({self._task}, timeout=self.FIRST_WAIT_S)
        return {"available": self.db.available, "writer": self.writer.stats() if self.writer else None,
                **(self._cache or {"status": "warming up"})}

    async def _refresh(self) -> None:
        params = {"pid": self.patient_id, "ex": None, "days": 90}
        parts = {
            "total_rows": queries.total_rows(self.db),
            "compression": queries.compression(self.db),
            "aggregate_query_ms": queries.explain_ms(self.db, queries.PROGRESS_V_SQL, params),
            "raw_query_ms": queries.explain_ms(self.db, queries.PROGRESS_FALLBACK_SQL, params),
            "rows_by_origin": queries.rows_by_origin(self.db),
            "data_checks": queries.latest_data_checks(self.db),
            "ai_calls_today": queries.ai_calls_today(self.db),
        }
        results = await asyncio.gather(*parts.values(), return_exceptions=True)
        cache: dict[str, Any] = {}
        for name, result in zip(parts, results, strict=True):
            if isinstance(result, DBError):
                cache[name] = {"error": str(result)}
            elif isinstance(result, BaseException):
                log.warning("db stat failed", extra={"stat": name, "error": repr(result)})
                cache[name] = {"error": type(result).__name__}
            else:
                cache[name] = result
        cache["refreshed_at"] = round(time.time(), 1)
        self._cache = cache
        self._refreshed_at = time.monotonic()
