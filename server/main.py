"""FastAPI app: lifespan, CORS, tunnel filter, WebSockets, static mounts and routers.

Run exactly one process: uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.agents.safety_guardian import SafetyGuardian
from server.agents.scheduler import Scheduler
from server.agents.session_coach import SessionCoach
from server.ai import cue_library, gemini
from server.ai.session_voice import SessionVoice
from server.api import alerts, calibration, demo, health, patients, prescriptions
from server.api import notifications as notifications_api
from server.api import copilot as copilot_api
from server.api import insight as insight_api
from server.api import proposals
from server.api import sessions as sessions_api
from server.api.db_dep import db_error_handler
from server.config import REPO_ROOT, cfg
from server.db import queries
from server.db.persist import DbStats, SessionPersistence
from server.db.pool import Database, DBError
from server.db.writer import FrameWriter
from server.engine_bridge import EngineBridge
from server.hub import Hub, RateLimiter
from server.logging_setup import get_logger, setup_logging, shutdown_logging
from server.notify import webhooks
from server.notify.notifications import NotificationService
from server.notify.notifications import on_alert_step as notify_on_alert_step
from server.notify.retell_client import RetellNotifier
from server.security import TunnelFilterMiddleware, require_token, ws_token_ok
from server.state import AppState, SessionRegistry, get_state, set_state

log = get_logger("main")

# Client → server message types accepted on /ws/events (§4.3); anything else is dropped. Engine events
# (fall, fall_status, gesture, …) can never come from a client.
CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "device_health"})
MAX_CLIENT_MSG_KEYS = 32
CLIENT_MSGS_PER_S = 20.0
MOCK_EXERCISE_ID = "squat"


class MultiDirStaticFiles(StaticFiles):
    """StaticFiles that looks a path up in several directories, first match wins."""

    def __init__(self, directories: list[Path]) -> None:
        super().__init__(directory=directories[0])
        self.all_directories = [str(d) for d in directories]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    problem = cfg.token_problem()
    if problem:
        raise RuntimeError(f"{problem}; refusing to start. Generate one with: "
                           "python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
    setup_logging()
    log.info("starting", extra={"config": cfg.redacted()})

    hub = Hub()
    db: Database | None = None
    persistence: SessionPersistence | None = None
    if cfg.TIGER_DSN and not cfg.DB_DISABLED:
        db = Database(cfg.TIGER_DSN, cfg.COPILOT_DSN)
        await db.open()
        await _warn_on_stale_grants(db)
        persistence = SessionPersistence(db)
    sessions = SessionRegistry(store=persistence)
    if persistence is not None:
        persistence.sessions = sessions

    def current_session_id() -> str | None:
        return sessions.current.session_id if sessions.current else None

    def current_patient_id() -> str | None:
        return sessions.current.patient_id if sessions.current else None

    bridge = EngineBridge(hub, asyncio.get_running_loop(), session_provider=current_session_id)
    state = AppState(cfg, hub, bridge, sessions, db=db)
    state.stat_providers.update({"hub": hub.stats, "bridge": bridge.stats, "ai": gemini.stats})
    writer: FrameWriter | None = None
    if db is not None and persistence is not None and cfg.TIGER_DSN:  # narrowed for the type checker
        writer = FrameWriter(cfg.TIGER_DSN, patient_provider=current_patient_id)
        writer.start()
        bridge.subscribe_frames(writer.submit)
        bridge.subscribe_events(persistence.on_event)
        state.stat_providers["db"] = DbStats(db, writer, cfg.MOCK_PATIENT_ID)
    else:
        state.stat_providers["db"] = lambda: {"available": False, "status": "disabled"}
    notifier = RetellNotifier()
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, cfg.CHECKIN_TIMEOUT_S, alert_hooks=state.on_alert_step)
    bridge.subscribe_events(guardian.on_event)
    state.guardian, state.notifier = guardian, notifier
    state.stat_providers["notifier"] = notifier.stats
    notifications = NotificationService(hub, db, notifier=notifier, guardian=guardian)
    state.notifications = notifications
    state.on_alert_step.append(notify_on_alert_step)
    state.stat_providers["notifications"] = notifications.stats
    set_state(state)
    if state.db is not None:
        await gemini.seed_budget(state.db)
    scheduler: Scheduler | None = None
    if db is not None:
        scheduler = Scheduler(db)
        scheduler.start()
        state.scheduler = scheduler
        state.stat_providers["scheduler"] = scheduler.stats

    await asyncio.to_thread(cue_library.reload)  # load cues.json off the loop; picks are in memory after
    bridge.add_event_enricher(cue_library.enrich_cue_event)
    voice = SessionVoice(hub, sessions, live=bridge.live)
    bridge.subscribe_events(voice.on_event)
    coach = SessionCoach(hub, sessions, bridge, incident_open=lambda sid: sid in guardian.incidents)
    bridge.subscribe_events(coach.on_event)
    state.coach = coach

    # Every session start (boot or REST) restarts the mock engine's program (so it `arrived`s again) and
    # warms up the greeting, so server.ai.session_voice.SessionVoice can emit it as soon as `arrived` fires.
    sessions.on_start.append(lambda session: bridge.notify_session_start(session.patient_id))
    sessions.on_start.append(voice.prepare_greeting)

    if cfg.MIRA_SOURCE.startswith("mock"):
        await sessions.start(cfg.MOCK_PATIENT_ID, MOCK_EXERCISE_ID)
    await bridge.start()
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        await notifications.stop()
        await notifier.stop()
        await guardian.stop()
        await bridge.stop()
        await voice.close()
        await coach.close()
        if sessions.current is not None:
            await sessions.stop(sessions.current.session_id)
        if writer is not None:
            await asyncio.to_thread(writer.stop)
        if db is not None:
            await db.close()
        set_state(None)
        log.info("stopped")
        shutdown_logging()


async def _warn_on_stale_grants(db: Database) -> None:
    """One clear WARNING when copilot_ro's grants don't match roles.sql (the Data Guard would only report
    "permission denied" errors)."""
    if not (db.available and db.has_ro):
        return
    try:
        problems = await queries.copilot_ro_grant_problems(db)
    except DBError as exc:
        log.warning("could not check copilot_ro grants", extra={"error": str(exc)})
        return
    if problems:
        log.warning("copilot_ro grants are stale", extra={"problems": problems, "fix": queries.ROLES_FIX})


def _client_event_handler() -> Callable[[dict[str, Any]], None]:
    """Per-connection handler for /ws/events: rate-limited, then filtered by `_on_client_event`."""
    limiter = RateLimiter(CLIENT_MSGS_PER_S)

    def handle(msg: dict[str, Any]) -> None:
        if not limiter.allow():
            if limiter.dropped == 1 or limiter.dropped % 100 == 0:
                log.warning("client over rate limit, dropping", extra={"dropped": limiter.dropped})
            return
        _on_client_event(msg)

    return handle


def _on_client_event(msg: dict[str, Any]) -> None:
    if msg.get("type") not in CLIENT_EVENT_TYPES or len(msg) > MAX_CLIENT_MSG_KEYS:
        log.debug("dropping client event", extra={"event_type": str(msg.get("type"))[:40]})
        return
    get_state().bridge.inject(msg)


def _parse_since_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


async def _serve_ws(ws: WebSocket, channel: str, on_message: Any = None) -> None:
    if not await ws_token_ok(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    await get_state().hub.connect(channel, ws, on_message)


def _mount_static(app: FastAPI) -> None:
    data_dir = Path(cfg.DATA_DIR)
    audio_dirs = [data_dir / "cues" / "mp3", data_dir / "audio"]
    for d in audio_dirs:
        d.mkdir(parents=True, exist_ok=True)
    app.mount("/audio", MultiDirStaticFiles(audio_dirs), name="audio")
    for name in ("device", "dashboard"):
        directory = REPO_ROOT / name
        if directory.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=directory, html=True), name=name)


def create_app() -> FastAPI:
    app = FastAPI(title="Mira server", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(TunnelFilterMiddleware)  # added last = outermost
    app.add_exception_handler(DBError, db_error_handler)

    app.include_router(health.router)
    app.include_router(demo.router)
    for module in (sessions_api, patients, prescriptions, calibration, alerts, notifications_api, insight_api):
        app.include_router(module.router, dependencies=[Depends(require_token)])
    for module in (copilot_api, proposals):
        app.include_router(module.router, dependencies=[Depends(require_token)])
    app.include_router(webhooks.router)

    @app.websocket("/ws/pose")
    async def ws_pose(ws: WebSocket) -> None:
        await _serve_ws(ws, "pose")

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await _serve_ws(ws, "live")

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await _serve_ws(ws, "events", _client_event_handler())

    @app.websocket("/ws/notifications")
    async def ws_notifications(ws: WebSocket) -> None:
        if not await ws_token_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        since_id = _parse_since_id(ws.query_params.get("since_id"))
        therapist_id = ws.query_params.get("therapist_id") or None
        service = get_state().notifications
        if service is None:
            await ws.close(code=1011)
            return
        await service.serve(ws, since_id=since_id, therapist_id=therapist_id)

    _mount_static(app)
    return app


app = create_app()
