"""FastAPI app: lifespan, CORS, tunnel filter, WebSockets, static mounts and routers.

Run exactly one process: uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import math
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from server.agents.haptics import HapticCoach
from server.agents.safety_guardian import SafetyGuardian
from server.agents.scheduler import Scheduler
from server.agents.session_analyst import SessionAnalyst
from server.agents.session_coach import SessionCoach
from server.ai import cue_library, gemini
from server.ai.session_voice import SessionVoice
from server.api import alerts, calibration, demo, health, lcd_link, patients, prescriptions
from server.api import care as care_api
from server.api import me as me_api
from server.api import me_watch
from server.api import watch_admin
from server.api import notifications as notifications_api
from server.api import copilot as copilot_api
from server.api import dashboard
from server.api import insight as insight_api
from server.api import pair as pair_api
from server.api import proposals
from server.api import sessions as sessions_api
from server.api.db_dep import db_error_handler
from server.config import Config, REPO_ROOT, cfg
from server.db import migrate, queries
from server.db.persist import DbStats, SessionPersistence
from server.db.pool import Database, DBError
from server.db.wrist import WristRecorder
from server.db.writer import WRIST_COPY_SQL, WRIST_MAX_QUEUE, FrameWriter, wrist_row
from server.devices import DEVICE_EVENT_TYPES, DEVICE_HEALTH_KEYS, DeviceRegistry
from server.engine_bridge import EngineBridge
from server.hub import Hub, RateLimiter
from server.logging_setup import get_logger, setup_logging, shutdown_logging
from server.notify import webhooks
from server.notify.notifications import NotificationService
from server.notify.notifications import on_alert_step as notify_on_alert_step
from server.notify.retell_client import RetellNotifier
from server.security import (Principal, TokenCheckUnavailable, TunnelFilterMiddleware, require_principal,
                             require_token, ws_scope, ws_token_ok)
from server.session_end import MockAutoRestart, SessionEnder
from server.state import AppState, SessionRegistry, get_state, set_state
from server import watch_wire

log = get_logger("main")

# Client → server message types accepted on /ws/events (§4.3); anything else is dropped. Engine events
# (fall, fall_status, gesture, …) can never come from a client.
CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "device_health", "watch_sync"})

# A patient's phone may steer the session and relay its watch; it is never the LCD.
PATIENT_CLIENT_EVENT_TYPES = frozenset({"command", "hr", "wrist", "watch_button", "watch_sync"})
# `command` names that control the session itself (as opposed to pain/im_ok, which the Guardian/Coach
# handle off the broadcast event). These are forwarded to the engine over the reverse channel.
SESSION_COMMANDS = frozenset({"start", "pause", "skip", "stop"})
MAX_CLIENT_MSG_KEYS = 32
DEVICE_HEALTH_STR_MAX = 64  # chars kept of a device_health string (screen, version)
CLIENT_MSGS_PER_S = 20.0
MOCK_EXERCISE_ID = "squat"
# Static web pages, mounted only when the directory exists. `device/` itself holds the iOS sources, so it is
# never served as a whole: the therapist dashboard lives in device/dashboard, the kiosk page in device/kiosk,
# and a static demo copy of the kiosk (no server data, shareable) in device/lcd-web.
STATIC_PAGES = {"dashboard": REPO_ROOT / "device" / "dashboard", "device": REPO_ROOT / "device" / "kiosk",
                "lcd-web": REPO_ROOT / "device" / "lcd-web"}  # static demo copy of the kiosk, no token


class MultiDirStaticFiles(StaticFiles):
    """StaticFiles that looks a path up in several directories, first match wins."""

    def __init__(self, directories: list[Path]) -> None:
        super().__init__(directory=directories[0])
        self.all_directories = [str(d) for d in directories]


class KioskStaticFiles(StaticFiles):
    """Static pages minus what only developers need: dev/, tests/ and READMEs are never served.
    Compared case-insensitively (macOS volumes)."""

    HIDDEN_DIRS = frozenset({"dev", "tests"})

    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = [p.lower() for p in Path(path).parts]
        if (parts and parts[0] in self.HIDDEN_DIRS) or (parts and parts[-1] == "readme.md"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def _mock_loops(c: Config) -> bool:
    """True when the mock engine's squat program repeats forever (pre-Plan-2 behaviour): its own `stopped`
    never fires, so neither the last-set auto-stop nor the mock auto-restart should race it."""
    return c.MIRA_SOURCE.startswith("mock") and c.MOCK_LOOP


def _last_set_enabled(c: Config) -> bool:
    """Whether `SessionEnder` should end the session when the last set is done (engines without `stopped`).
    Never when MOCK_LOOP keeps the mock program running forever."""
    return c.AUTO_STOP_AFTER_LAST_SET and not _mock_loops(c)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    problem = cfg.token_problem() or cfg.device_token_problem() or cfg.vision_token_problem() or cfg.lcd_link_problem()
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
        if cfg.DB_AUTO_MIGRATE and db.available:
            try:
                applied = await asyncio.to_thread(migrate.apply_migrations, cfg.TIGER_DSN)
                log.info("migrations checked", extra={"count": len(applied)})
            except Exception as exc:  # noqa: BLE001 - a schema hiccup must never block the live demo
                log.warning("migrations failed; continuing", extra={"error": str(exc)})
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
    devices = DeviceRegistry(hub)
    state.devices = devices
    state.stat_providers["devices"] = devices.status
    devices.start()
    writer: FrameWriter | None = None
    wrist_writer: FrameWriter | None = None
    wrist_recorder: WristRecorder | None = None
    if db is not None and persistence is not None and cfg.TIGER_DSN:  # narrowed for the type checker
        writer = FrameWriter(cfg.TIGER_DSN, patient_provider=current_patient_id)
        writer.start()
        bridge.subscribe_frames(writer.submit)
        bridge.subscribe_events(persistence.on_event)
        state.stat_providers["db"] = DbStats(db, writer, cfg.MOCK_PATIENT_ID)

        wrist_writer = FrameWriter(cfg.TIGER_DSN, patient_provider=current_patient_id, copy_sql=WRIST_COPY_SQL,
                                   row_fn=wrist_row, max_queue=WRIST_MAX_QUEUE, thread_name="wrist-writer")
        wrist_writer.start()

        def patient_of(session_id: str) -> str | None:
            found = sessions.get(session_id)
            return found.patient_id if found is not None else None

        wrist_recorder = WristRecorder(wrist_writer, patient_of)
        bridge.subscribe_events(wrist_recorder.on_event)
    else:
        state.stat_providers["db"] = lambda: {"available": False, "status": "disabled"}
    notifier = RetellNotifier()
    guardian = SafetyGuardian(hub, bridge, notifier, sessions, cfg.CHECKIN_TIMEOUT_S, alert_hooks=state.on_alert_step)
    bridge.subscribe_events(guardian.on_event)
    state.guardian, state.notifier = guardian, notifier
    sessions.on_stop.append(guardian.on_session_stop)  # a stopped session ends its breather
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
    haptics = HapticCoach(hub)
    bridge.subscribe_events(haptics.on_event)

    ender = SessionEnder(sessions, bridge.live, auto_stop_after_last_set=_last_set_enabled(cfg))
    bridge.subscribe_events(ender.on_event)
    auto_restart: MockAutoRestart | None = None
    if cfg.MIRA_SOURCE.startswith("mock") and not _mock_loops(cfg):
        auto_restart = MockAutoRestart(sessions, cfg.MOCK_PATIENT_ID, MOCK_EXERCISE_ID, cfg.MOCK_AUTO_RESTART_S,
                                       incident_open=lambda: bool(guardian.incidents))
        sessions.on_stop.append(auto_restart.on_stop)
    # Every ended session gets a whole-session analysis (session_summary event, session_analyses row).
    analyst = SessionAnalyst(hub, sessions)
    sessions.on_stop.append(analyst.on_stop)
    state.analyst = analyst

    # Every session start (boot or REST) restarts the mock engine's program (so it `arrived`s again) and
    # warms up the greeting, so server.ai.session_voice.SessionVoice can emit it as soon as `arrived` fires.
    sessions.on_start.append(lambda session: bridge.live.on_session_start(session.profile.first_name,
                                                                        session.exercise_id,
                                                                        session.profile.device_prefs))
    sessions.on_start.append(lambda session: bridge.notify_session_start(session.patient_id))
    sessions.on_start.append(voice.prepare_greeting)

    if cfg.MIRA_SOURCE.startswith("mock"):
        await sessions.start(cfg.MOCK_PATIENT_ID, MOCK_EXERCISE_ID, origin="boot")
    await bridge.start()
    try:
        yield
    finally:
        if auto_restart is not None:  # first: it must not start a session while everything below shuts down
            await auto_restart.close()
        if scheduler is not None:
            await scheduler.stop()
        await notifications.stop()
        await notifier.stop()
        await guardian.stop()
        await devices.stop()
        await bridge.stop()
        await voice.close()
        await coach.close()
        if sessions.current is not None:
            await sessions.stop(sessions.current.session_id)
        await analyst.close()
        if writer is not None:
            await asyncio.to_thread(writer.stop)
        if wrist_writer is not None:
            await asyncio.to_thread(wrist_writer.stop)
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


#: watch_button name → the session command it maps to (unknown names are ignored). `resume` (the watch's Stopped
#: screen) is a `start`: the engine resumes a paused session on `start`.
WATCH_BUTTON_COMMANDS = {"ready": "start", "stop": "stop", "resume": "start"}


def _on_client_event(msg: dict[str, Any]) -> None:
    kind = msg.get("type")
    if kind not in CLIENT_EVENT_TYPES or len(msg) > MAX_CLIENT_MSG_KEYS:
        log.debug("dropping client event", extra={"event_type": str(kind)[:40]})
        return
    state = get_state()
    if kind == "watch_sync":
        _on_watch_sync(state, msg)
        return
    if kind == "wrist":
        clean = watch_wire.sanitize_wrist(msg)
        if clean is None:
            return
        msg = clean
    state.bridge.inject(msg)
    if kind == "command" and msg.get("name") in SESSION_COMMANDS:
        _forward_session_command(state, msg)
    elif kind == "watch_button":
        name = WATCH_BUTTON_COMMANDS.get(msg.get("name"))
        if name:
            _forward_session_command(state, {"type": "command", "name": name, "payload": {"source": "watch"}})


def _on_watch_sync(state: AppState, msg: dict[str, Any]) -> None:
    """A sync-wave window goes straight to ClockSync (never broadcast: ~50 numbers a second nobody else needs).
    Without a current session there is nothing to store the offset on, so the window is dropped."""
    clean = watch_wire.sanitize_watch_sync(msg)
    sync = state.clock_sync
    current = state.sessions.current
    if clean is None or sync is None or current is None:
        return
    sync.on_window(clean, current.session_id)


def sanitize_device_health(msg: dict[str, Any]) -> dict[str, Any]:
    """A device's device_health, reduced to the allow-listed keys with bounded scalar values, stamped as the
    LCD: strings are cut to DEVICE_HEALTH_STR_MAX chars, battery_pct must be a number (clamped to 0-100), and
    objects, lists and nulls are dropped."""
    out: dict[str, Any] = {}
    for k, v in msg.items():
        if k not in DEVICE_HEALTH_KEYS:
            continue
        if k == "battery_pct":
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out[k] = min(100, max(0, v))
        elif isinstance(v, str):
            out[k] = v[:DEVICE_HEALTH_STR_MAX]
        elif isinstance(v, (int, float, bool)):
            out[k] = v
    out.update(type="device_health", app="lcd", t=round(time.time(), 3))
    return out


def _device_event_handler() -> Callable[[dict[str, Any]], None]:
    """Per-connection handler for a device-scoped /ws/events client: rate-limited, device_health only."""
    limiter = RateLimiter(CLIENT_MSGS_PER_S)

    def handle(msg: dict[str, Any]) -> None:
        if not limiter.allow() or msg.get("type") != "device_health" or len(msg) > MAX_CLIENT_MSG_KEYS:
            return
        state = get_state()
        clean = sanitize_device_health(msg)
        state.bridge.inject(clean)
        if state.devices is not None:
            state.devices.on_health(clean)

    return handle


def _device_accepts(msg: dict[str, Any]) -> bool:
    return msg.get("type") in DEVICE_EVENT_TYPES


def _forward_session_command(state: AppState, msg: dict[str, Any]) -> None:
    """start/pause/skip/stop go to the engine over the reverse channel (`EngineBridge.send_to_engine`), same
    as `incident_closed`. A user `start` must never resume a session the Safety Guardian is holding open
    (a pain/HR incident, or a breather): the Guardian, not the patient, decides when that ends."""
    session = state.sessions.current
    session_id = session.session_id if session is not None else None
    name = msg.get("name")
    guardian = state.guardian
    if name == "start" and guardian is not None and session_id is not None and guardian.holding(session_id):
        log.info("ignoring user start: Guardian holds the session", extra={"session_id": session_id})
        return
    state.bridge.send_to_engine({"type": "command", "name": name, "payload": msg.get("payload") or {},
                                 "session_id": session_id})


def _parse_since_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


async def _serve_ws(ws: WebSocket, channel: str, on_message: Any = None, *, allow_device: bool = False) -> None:
    try:
        principal = await ws_scope(ws)
    except TokenCheckUnavailable:
        # The server can't tell yet whether this token is valid (no database, or a database hiccup): tell the
        # phone to try again later, never that its token was rejected (1008 would stop LiveSocket's retries).
        await ws.accept()
        await ws.close(code=1013)
        return
    if principal is None or (principal.scope == "device" and not allow_device):
        await ws.close(code=1008)
        return
    await ws.accept()
    hub = get_state().hub
    if principal.scope == "full":
        await hub.connect(channel, ws, on_message)
        return
    if principal.scope == "patient":
        await _serve_patient_ws(ws, channel, principal)
        return
    if channel != "events":
        await hub.connect(channel, ws)
        return
    devices = get_state().devices
    if devices is not None:
        devices.connected()
    try:
        await hub.connect(channel, ws, _device_event_handler(), accept=_device_accepts)
    finally:
        if devices is not None:
            devices.disconnected()


async def _serve_vision_ws(ws: WebSocket) -> None:
    """Authenticated ingress for an outbound remote vision-agent connection.

    The laptop dials Vultr, so it needs no public address or router port-forwarding.  Messages are the existing
    vision.Engine frame/event contract and therefore reach persistence, safety and every UI stream unchanged.
    """
    from starlette.websockets import WebSocketDisconnect

    token = ws.query_params.get("token", "")
    if not cfg.MIRA_VISION_TOKEN or not secrets.compare_digest(token, cfg.MIRA_VISION_TOKEN):
        await ws.close(code=1008)
        return
    await ws.accept()
    limiter = RateLimiter(60.0)
    bridge = get_state().bridge

    async def send_command(msg: dict[str, Any]) -> None:
        await ws.send_json(msg)

    bridge.set_remote_sender(send_command)
    try:
        while True:
            msg = await ws.receive_json()
            if (not limiter.allow() or not isinstance(msg, dict) or len(msg) > MAX_CLIENT_MSG_KEYS
                    or not isinstance(msg.get("type"), str) or len(msg["type"]) > 64):
                continue
            bridge.ingest_remote(msg)
    except WebSocketDisconnect:
        return
    finally:
        # Do not clear a newer reconnect that replaced this socket while this handler was unwinding.
        bridge.clear_remote_sender(send_command)


def _patient_accepts(patient_id: str) -> Callable[[dict[str, Any]], bool]:
    """Per-client hub filter: a phone sees frames and events for its own patient, even after the session that
    produced them has ended (e.g. the analyst's `session_summary`, emitted ~2s after `stop()` already cleared
    `sessions.current`). Priority: an explicit `patient_id` on the message decides outright; else an explicit
    `session_id` is looked up (the registry still knows ended sessions) and that session's patient decides;
    else (no tag at all — `/ws/live` and `/ws/pose` frames) fall back to the current-session rule."""
    def accept(msg: dict[str, Any]) -> bool:
        tagged_patient = msg.get("patient_id")
        if isinstance(tagged_patient, str):
            return tagged_patient == patient_id
        session_id = msg.get("session_id")
        if isinstance(session_id, str):
            session = get_state().sessions.get(session_id)
            return session is not None and session.patient_id == patient_id
        current = get_state().sessions.current
        return current is not None and current.patient_id == patient_id

    return accept


def _patient_owns_current(patient_id: str) -> Callable[[dict[str, Any]], bool]:
    """Inbound gate for a patient's own messages: ONLY the current session's patient, never an explicit
    `patient_id`/`session_id` tag on the message (unlike `_patient_accepts`, the outbound filter that
    deliberately honours those tags so a phone still receives its own `session_summary` after the session
    it describes has ended). Without this, a phone could write `{"type":"command","name":"stop",
    "patient_id":"someone-else"}` and steer another patient's live session."""
    def owns(msg: dict[str, Any]) -> bool:  # noqa: ARG001 - signature matches the hub's message-filter shape
        current = get_state().sessions.current
        return current is not None and current.patient_id == patient_id

    return owns


_STRIP_CLIENT_TAGS = ("patient_id", "session_id")


def _strip_client_tags(msg: dict[str, Any]) -> dict[str, Any]:
    """Drop any client-supplied `patient_id`/`session_id` before `_on_client_event`: those keys are stamped
    by the server on outbound events for routing, never trusted as input from a phone."""
    return {k: v for k, v in msg.items() if k not in _STRIP_CLIENT_TAGS}


def _patient_event_handler(patient_id: str) -> Callable[[dict[str, Any]], None]:
    """Like _client_event_handler, minus device_health, and only while the session is the patient's own."""
    limiter = RateLimiter(CLIENT_MSGS_PER_S)
    owns = _patient_owns_current(patient_id)

    def handle(msg: dict[str, Any]) -> None:
        if not limiter.allow() or msg.get("type") not in PATIENT_CLIENT_EVENT_TYPES or not owns(msg):
            return
        _on_client_event(_strip_client_tags(msg))

    return handle


async def _serve_patient_ws(ws: WebSocket, channel: str, principal: Principal) -> None:
    assert principal.patient_id is not None
    hub = get_state().hub
    handler = _patient_event_handler(principal.patient_id) if channel == "events" else None
    await hub.connect(channel, ws, handler, accept=_patient_accepts(principal.patient_id))


def _mount_static(app: FastAPI) -> None:
    data_dir = Path(cfg.DATA_DIR)
    audio_dirs = [data_dir / "cues" / "mp3", data_dir / "audio"]
    for d in audio_dirs:
        d.mkdir(parents=True, exist_ok=True)
    app.mount("/audio", MultiDirStaticFiles(audio_dirs), name="audio")
    for name, directory in STATIC_PAGES.items():
        if directory.is_dir():
            static_cls = KioskStaticFiles if name in ("device", "dashboard") else StaticFiles
            app.mount(f"/{name}", static_cls(directory=directory, html=True), name=name)


def create_app() -> FastAPI:
    app = FastAPI(title="Mira server", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(TunnelFilterMiddleware)  # added last = outermost
    app.add_exception_handler(DBError, db_error_handler)

    app.include_router(health.router)
    app.include_router(demo.router)
    for module in (sessions_api, patients, prescriptions):  # patients may call these with their own token
        app.include_router(module.router, dependencies=[Depends(require_principal)])
    for module in (calibration, alerts, notifications_api, insight_api):
        app.include_router(module.router, dependencies=[Depends(require_token)])
    for module in (copilot_api, proposals, dashboard):
        app.include_router(module.router, dependencies=[Depends(require_token)])
    app.include_router(webhooks.router)
    app.include_router(lcd_link.router)  # its own secret key, no API token
    app.include_router(pair_api.router)  # /pair is public; its two admin routes carry their own require_token
    app.include_router(me_api.router)  # routes carry their own require_principal
    app.include_router(care_api.router)  # routes carry their own require_principal
    app.include_router(me_watch.router)  # routes carry their own require_principal
    app.include_router(watch_admin.router, dependencies=[Depends(require_token)])  # therapist-only

    @app.websocket("/ws/pose")
    async def ws_pose(ws: WebSocket) -> None:
        await _serve_ws(ws, "pose")

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        await _serve_ws(ws, "live", allow_device=True)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await _serve_ws(ws, "events", _client_event_handler(), allow_device=True)

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

    @app.websocket("/ws/vision")
    async def ws_vision(ws: WebSocket) -> None:
        await _serve_vision_ws(ws)

    _mount_static(app)
    return app


app = create_app()
