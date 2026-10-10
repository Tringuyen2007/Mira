"""FastAPI app: lifespan, CORS, tunnel filter, WebSockets, static mounts and routers.

Run exactly one process: uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.api import alerts, calibration, demo, health, patients, prescriptions
from server.api import sessions as sessions_api
from server.api.db_dep import db_error_handler
from server.config import REPO_ROOT, cfg
from server.db.persist import DbStats, SessionPersistence
from server.db.pool import Database, DBError
from server.db.writer import FrameWriter
from server.engine_bridge import EngineBridge
from server.hub import Hub
from server.logging_setup import get_logger, setup_logging, shutdown_logging
from server.notify import webhooks
from server.security import TunnelFilterMiddleware, require_token, ws_token_ok
from server.state import AppState, SessionRegistry, get_state, set_state

log = get_logger("main")

# Client → server message types accepted on /ws/events (§4.3); anything else is dropped.
CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "device_health"})
MAX_CLIENT_MSG_KEYS = 32
MOCK_EXERCISE_ID = "squat"


class MultiDirStaticFiles(StaticFiles):
    """StaticFiles that looks a path up in several directories, first match wins."""

    def __init__(self, directories: list[Path]) -> None:
        super().__init__(directory=directories[0])
        self.all_directories = [str(d) for d in directories]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not cfg.MIRA_API_TOKEN:
        raise RuntimeError("MIRA_API_TOKEN is not set; refusing to start (see .env.example)")
    setup_logging()
    log.info("starting", extra={"config": cfg.redacted()})

    hub = Hub()
    db: Database | None = None
    persistence: SessionPersistence | None = None
    if cfg.TIGER_DSN and not cfg.DB_DISABLED:
        db = Database(cfg.TIGER_DSN, cfg.COPILOT_DSN)
        await db.open()
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
    state.stat_providers.update({"hub": hub.stats, "bridge": bridge.stats})
    writer: FrameWriter | None = None
    if db is not None and persistence is not None and cfg.TIGER_DSN:  # narrowed for the type checker
        writer = FrameWriter(cfg.TIGER_DSN, patient_provider=current_patient_id)
        writer.start()
        bridge.subscribe_frames(writer.submit)
        bridge.subscribe_events(persistence.on_event)
        state.stat_providers["db"] = DbStats(db, writer, cfg.MOCK_PATIENT_ID)
    else:
        state.stat_providers["db"] = lambda: {"available": False, "status": "disabled"}
    set_state(state)

    if cfg.MIRA_SOURCE.startswith("mock"):
        await sessions.start(cfg.MOCK_PATIENT_ID, MOCK_EXERCISE_ID)
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()
        if sessions.current is not None:
            await sessions.stop(sessions.current.session_id)
        if writer is not None:
            await asyncio.to_thread(writer.stop)
        if db is not None:
            await db.close()
        set_state(None)
        log.info("stopped")
        shutdown_logging()


def _on_client_event(msg: dict[str, Any]) -> None:
    if msg.get("type") not in CLIENT_EVENT_TYPES or len(msg) > MAX_CLIENT_MSG_KEYS:
        log.debug("dropping client event", extra={"event_type": str(msg.get("type"))[:40]})
        return
    get_state().bridge.inject(msg)


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
    for module in (sessions_api, patients, prescriptions, calibration, alerts):
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
        await _serve_ws(ws, "events", _on_client_event)

    _mount_static(app)
    return app


app = create_app()
