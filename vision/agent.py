"""Remote vision agent: Eric's one-camera Engine on the camera laptop -> ``wss://<mira-host>/ws/vision``.

    python -m vision.agent --host wss://45.76.255.253 --exercise squat --reps 3 --sets 1 --height-cm 170

The agent dials the server once (outbound only, token in the query string), sends Engine frames and events in
the server's wire format (``server/samples/engine_frame.json``, ``docs/vision-media-contract.md``) and listens on
the same socket for ``command`` (start | pause | skip | stop), ``incident_closed`` and ``end_set_early``.

Privacy gate: no camera is opened and nothing is inferred until the server sends ``command start`` (the patient
pressed "Start today's session" in the iOS app). ``command stop``, the socket dropping or Ctrl-C stops capture.
Raw video never crosses this socket; the optional WHIP publisher (``--whip-cmd``) is a separate process that is
started and stopped with the session.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from vision.engine import Engine
from vision.types import FrameResult

log = logging.getLogger("vision.agent")

Message = dict[str, Any]

MEASURING_CUE_EVERY_S = 5.0
FALL_STATUS_EVERY_S = 2.0
LYING_COM_M = 0.45
SITTING_COM_M = 0.85
STILL_SPEED_MPS = 0.15
GOOD_REP_QUALITY = 80
MAX_WIRE_KEYS = 32  # the server drops larger messages (server.main.MAX_CLIENT_MSG_KEYS)
RECONNECT_S = (1.0, 2.0, 5.0, 10.0)

# Engine angle keys -> the team frame contract (README "Frame contract: joint angles"). Knee/hip are flexion:
# 0 = straight, so 180 minus the Engine's interior angle.
FLEXION_ANGLES = {"left_knee": "knee_l", "right_knee": "knee_r", "left_hip": "hip_l", "right_hip": "hip_r"}
DIRECT_ANGLES = {"trunk_lean": "trunk_lean", "trunk_side_lean": "trunk_side", "pelvis_rotation": "pelvis_rot",
                 "knee_to_ankle_gap_ratio": "knee_ratio", "left_ankle": "ankle_l", "right_ankle": "ankle_r",
                 "left_elbow": "elbow_l", "right_elbow": "elbow_r", "left_shoulder": "shoulder_l",
                 "right_shoulder": "shoulder_r"}

# Spoken/captioned text for Engine cue ids (form rules in data/rules/*.json plus the agent's own prompts).
# audio_url stays null: the LCD captions these; voiced lines are the server's TrainerVoice catalogue.
CUE_TEXT = {
    "knee_valgus": "Push your knees out over your toes.",
    "trunk_lean": "Keep your chest up.",
    "knee_asymmetry": "Even out your weight on both legs.",
    "stand_straight": "Stand straight and still so I can measure you.",
}
MEASURING_CUE_ID = "stand_straight"


def translate_angles(angles: dict[str, float]) -> dict[str, float]:
    """Engine angle names -> contract names; knee/hip become flexion in degrees."""
    out: dict[str, float] = {}
    for src, dst in FLEXION_ANGLES.items():
        value = angles.get(src)
        if isinstance(value, (int, float)) and math.isfinite(value):
            out[dst] = round(180.0 - float(value), 1)
    for src, dst in DIRECT_ANGLES.items():
        value = angles.get(src)
        if isinstance(value, (int, float)) and math.isfinite(value):
            out[dst] = round(float(value), 1)
    if "trunk_lean" in out:
        out["trunk_lean"] = round(abs(out["trunk_lean"]), 1)
    return out


def rep_quality_label(quality: Any, flags: list[str]) -> str:
    """The kiosk/TrainerVoice count a rep only when ``quality == "good"`` (mock contract)."""
    score = float(quality) if isinstance(quality, (int, float)) and not isinstance(quality, bool) else 0.0
    return "good" if not flags and score >= GOOD_REP_QUALITY else "fix_form"


def _posture(com_y: float | None) -> str:
    if com_y is None or not math.isfinite(com_y):
        return "unknown"
    if com_y < LYING_COM_M:
        return "lying"
    if com_y < SITTING_COM_M:
        return "sitting"
    return "standing"


class Translator:
    """Engine frames/events -> the server's §4.5 wire dicts for one session. Pure: no I/O, no clock."""

    def __init__(self, session_id: str, exercise_id: str, reps_target: int, sets_target: int) -> None:
        self.session_id = session_id
        self.exercise_id = exercise_id
        self.reps_target = reps_target
        self.sets_target = sets_target
        self.seq = 0
        self.set_no = 1
        self.sent_stopped = False
        self._last_measuring_cue: float | None = None
        self._fall: dict[str, Any] | None = None  # {"next_status_t", "still_s", "last_com", "last_t", "posture", "moving", "on_mat"}

    def _stamp(self, msg: Message, t: float) -> Message:
        self.seq += 1
        msg["seq"] = self.seq
        msg["t"] = round(float(t), 3)
        msg["session_id"] = self.session_id
        if len(msg) > MAX_WIRE_KEYS:
            raise ValueError(f"wire message has {len(msg)} keys (max {MAX_WIRE_KEYS})")
        return msg

    def frame(self, fr: Message) -> list[Message]:
        """``FrameResult.to_dict()`` -> ``frame`` (+ a rate-limited ``stand_straight`` cue while measuring,
        + ``fall_status`` every 2 s after a confirmed fall)."""
        t = float(fr["t"])
        out: list[Message] = []
        measuring = fr.get("body") == "measuring"
        if measuring and (self._last_measuring_cue is None or t - self._last_measuring_cue >= MEASURING_CUE_EVERY_S):
            self._last_measuring_cue = t
            out.append(self._stamp({"type": "cue", "id": MEASURING_CUE_ID, "text": CUE_TEXT[MEASURING_CUE_ID],
                                    "audio_url": None, "body_progress": fr.get("body_progress")}, t))
        conf = [c if isinstance(c, (int, float)) and math.isfinite(c) else 0.0 for c in (fr.get("conf") or [])]
        flags = list(fr.get("flags") or [])
        if measuring and "measuring" not in flags:
            flags.append("measuring")
        com = fr.get("com")
        out.append(self._stamp({
            "type": "frame", "exercise_id": self.exercise_id, "set_no": self.set_no,
            "joints": fr["joints"], "confidence": conf, "angles": translate_angles(fr.get("angles") or {}),
            "com": com, "load_l_pct": fr.get("load_l_pct"), "heel_l_pct": fr.get("heel_l_pct"),
            "heel_r_pct": fr.get("heel_r_pct"),
            "confidence_avg": round(sum(conf) / len(conf), 3) if conf else 0.0, "flags": flags,
            "body": fr.get("body", "ready"), "body_progress": fr.get("body_progress", 1.0),
            "tracking": fr.get("tracking", "lost"), "on_mat": bool(fr.get("on_mat", False)),
            "phase": fr.get("phase", "idle"), "rep": fr.get("rep", 0),
        }, t))
        status = self._fall_status(t, com, bool(fr.get("on_mat", False)))
        if status is not None:
            out.append(status)
        return out

    def _fall_status(self, t: float, com: Any, on_mat: bool) -> Message | None:
        fall = self._fall
        if fall is None:
            return None
        com_y = None
        if isinstance(com, (list, tuple)) and len(com) == 3 and all(isinstance(v, (int, float)) for v in com):
            com_y = float(com[1])
            last_com, last_t = fall.get("last_com"), fall.get("last_t")
            if last_com is not None and last_t is not None and t > last_t:
                dt = t - last_t
                speed = math.dist(last_com, com) / dt
                moving = speed > STILL_SPEED_MPS
                fall["moving"] = moving
                fall["still_s"] = 0.0 if moving else fall["still_s"] + dt
            fall["last_com"], fall["last_t"] = list(com), t
            fall["posture"] = _posture(com_y)
        fall["on_mat"] = on_mat
        if t < fall["next_status_t"]:
            return None
        fall["next_status_t"] = t + FALL_STATUS_EVERY_S
        return self._stamp({"type": "fall_status", "posture": fall["posture"], "moving": bool(fall["moving"]),
                            "still_s": round(fall["still_s"], 1), "on_mat": on_mat}, t)

    def event(self, ev: Message) -> list[Message]:
        kind = ev.get("type")
        t = float(ev.get("t", time.time()))
        plan = {"exercise_id": self.exercise_id, "reps_target": self.reps_target, "sets_target": self.sets_target}
        if kind == "arrived":
            return [self._stamp({"type": "arrived", **plan}, t)]
        if kind == "countdown":
            return [self._stamp({"type": "countdown", "n": ev.get("n"), "set": self.set_no, **plan}, t)]
        if kind == "cue":
            cue_id = str(ev.get("id"))
            return [self._stamp({"type": "cue", "id": cue_id, "text": CUE_TEXT.get(cue_id, ev.get("text")),
                                 "audio_url": None}, t)]
        if kind == "rep":
            flags = [str(f) for f in (ev.get("flags") or [])]
            return [self._stamp({"type": "rep", "index": ev.get("index"), "set": ev.get("set", self.set_no),
                                 "exercise_id": self.exercise_id, "quality": rep_quality_label(ev.get("quality"), flags),
                                 "quality_score": ev.get("quality"), "flags": flags, "rom": ev.get("rom"),
                                 "asym_max": ev.get("asym_max"), "duration_s": ev.get("duration_s")}, t)]
        if kind == "set_done":
            set_no = int(ev.get("set", self.set_no))
            out = [self._stamp({"type": "set_done", "set": set_no, "reps": ev.get("reps"),
                                "avg_quality": ev.get("avg_quality"), "exercise_id": self.exercise_id}, t)]
            if set_no >= self.sets_target:
                out.extend(self.stopped(t, "program_complete"))
            else:
                self.set_no = set_no + 1
            return out
        if kind == "fall":
            stage = ev.get("stage")
            if stage != "confirmed":
                return [self._stamp({"type": "fall", "stage": "possible"}, t)]  # ignored by the server; for logs
            if self._fall is None:
                self._fall = {"next_status_t": t + FALL_STATUS_EVERY_S, "still_s": 0.0, "last_com": None,
                              "last_t": None, "posture": "lying", "moving": False, "on_mat": True}
            return [self._stamp({"type": "fall", "stage": "confirmed", "posture": self._fall["posture"],
                                 "on_mat": self._fall["on_mat"], "exercise_id": self.exercise_id,
                                 "set": self.set_no}, t)]
        if kind in ("awaiting_gesture", "gesture", "paused", "resumed", "camera_moved"):
            msg = {k: v for k, v in ev.items() if k not in ("seq", "t", "session_id")}
            return [self._stamp(msg, t)]
        log.debug("unmapped engine event %s", kind)
        return []

    def fall_closed(self) -> None:
        self._fall = None

    def stopped(self, t: float, reason: str) -> list[Message]:
        if self.sent_stopped:
            return []
        self.sent_stopped = True
        return [self._stamp({"type": "stopped", "reason": reason}, t)]


class SessionRunner(threading.Thread):
    """Runs one Engine session on its own thread (the Engine is blocking/pull-driven) and hands every wire
    message to ``emit`` (thread-safe by contract)."""

    def __init__(self, engine: Engine, translator: Translator, emit: Callable[[Message], None], *,
                 exercise_id: str, reps_target: int, sets_target: int, patient: dict[str, Any]) -> None:
        super().__init__(name="vision-session", daemon=True)
        self.engine = engine
        self.translator = translator
        self.emit = emit
        self.exercise_id, self.reps_target, self.sets_target, self.patient = exercise_id, reps_target, sets_target, patient
        self._stop_flag = threading.Event()
        self._last_t: float | None = None
        self.error: str | None = None
        self._status_at = 0.0
        self._n = 0

    def run(self) -> None:
        reason = "program_complete"
        try:
            self.engine.start(self.exercise_id, self.reps_target, self.sets_target, self.patient)
            for frame in self.engine.frames():
                self._last_t = frame.t
                for msg in self.translator.frame(frame.to_dict()):
                    self.emit(msg)
                self._drain_events()
                self._log_status(frame)
                if self._stop_flag.is_set():
                    reason = "stopped"
                    break
            self._drain_events()
            if self._stop_flag.is_set():
                reason = "stopped"
        except Exception as exc:  # noqa: BLE001 - the server must learn the stream ended
            self.error = repr(exc)
            log.exception("engine session failed")
            reason = "engine_error"
        finally:
            try:
                self.engine.stop()
            except Exception:  # noqa: BLE001
                log.exception("engine stop failed")
            for msg in self.translator.stopped(self._last_t if self._last_t is not None else time.time(), reason):
                self.emit(msg)

    def _log_status(self, frame: FrameResult, every_s: float = 5.0) -> None:
        """One line every 5 s so the operator can tell "no person seen" from "measuring" from "ready"."""
        self._n += 1
        now = time.monotonic()
        if now - self._status_at < every_s:
            return
        self._status_at = now
        conf = frame.conf
        seen = int((conf > 0.5).sum()) if conf is not None else 0
        state = self.engine.session.state if self.engine.session is not None else "?"
        hint = ""
        if frame.tracking == "lost" or seen == 0:
            hint = "  <- no person detected: check camera index (python -m vision.cams), lighting, and that they are on the mat"
        elif frame.body == "measuring":
            hint = "  <- stand straight and still, facing the camera, until the body is measured"
        log.info("frames=%d tracking=%s body=%s progress=%.0f%% joints_seen=%d/33 on_mat=%s session=%s rep=%d%s",
                 self._n, frame.tracking, frame.body, 100 * float(frame.body_progress), seen, frame.on_mat, state,
                 self.engine.counter.rep if self.engine.counter is not None else 0, hint)

    def _drain_events(self) -> None:
        while True:
            try:
                ev = self.engine.events.get_nowait()
            except Exception:  # queue.Empty
                return
            for msg in self.translator.event(ev):
                self.emit(msg)

    def command(self, name: str) -> None:
        if name == "stop":
            self._stop_flag.set()
        try:
            self.engine.command(name, self._last_t)
        except Exception as exc:  # noqa: BLE001 - e.g. resume while not paused
            log.warning("engine command %s rejected: %s", name, exc)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_flag.set()
        try:
            self.engine.command("stop", self._last_t)
        except Exception:  # noqa: BLE001 - not started yet
            self.engine.stop()
        self.join(timeout)


class WhipPublisher:
    """Hook for the camera stream (docs/vision-media-contract.md): runs an external WHIP publisher command for the
    session and stops it with the session. The agent itself never encodes or sends video."""

    def __init__(self, command_template: str | None, url_template: str | None) -> None:
        self.command_template = command_template
        self.url_template = url_template
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self, session_id: str) -> None:
        if not self.command_template or not self.url_template:
            return
        self.stop()
        url = self.url_template.format(session_id=session_id)
        cmd = shlex.split(self.command_template.format(whip_url=url, session_id=session_id))
        log.info("starting WHIP publisher for %s", url)
        self._proc = subprocess.Popen(cmd)  # noqa: S603 - operator-supplied command line

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


class Agent:
    """Socket-side state machine: applies reverse-channel messages, owns the current SessionRunner."""

    def __init__(self, engine_factory: Callable[[], Engine], emit: Callable[[Message], None], *,
                 exercise_id: str, reps_target: int, sets_target: int, patient: dict[str, Any] | None = None,
                 whip: WhipPublisher | None = None) -> None:
        self.engine_factory = engine_factory
        self.emit = emit
        self.exercise_id, self.reps_target, self.sets_target = exercise_id, reps_target, sets_target
        self.patient = patient or {}
        self.whip = whip or WhipPublisher(None, None)
        self.runner: SessionRunner | None = None
        self.session_id: str | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None and self.runner.is_alive()

    def handle(self, msg: Message) -> None:
        kind = msg.get("type")
        if kind == "command":
            name, sid = msg.get("name"), msg.get("session_id")
            if name == "start":
                if self.running and sid == self.session_id:
                    self.runner.command("resume")  # type: ignore[union-attr]  (a user `start` while paused resumes)
                elif isinstance(sid, str) and sid:
                    self.start_session(sid)
                else:
                    log.warning("start command without session_id ignored")
            elif name == "pause" and self.running:
                self.runner.command("pause")  # type: ignore[union-attr]
            elif name == "skip" and self.running:
                self.runner.command("skip")  # type: ignore[union-attr]
            elif name == "stop":
                self.stop_session()
        elif kind == "end_set_early" and self.running:
            self.runner.command("skip")  # type: ignore[union-attr]
        elif kind == "incident_closed" and self.runner is not None:
            self.runner.translator.fall_closed()
        else:
            log.debug("ignoring reverse-channel message %s", kind)

    def start_session(self, session_id: str) -> None:
        self.stop_session()
        log.info("command start: opening the camera for session %s", session_id)
        translator = Translator(session_id, self.exercise_id, self.reps_target, self.sets_target)
        self.runner = SessionRunner(self.engine_factory(), translator, self.emit, exercise_id=self.exercise_id,
                                    reps_target=self.reps_target, sets_target=self.sets_target, patient=self.patient)
        self.session_id = session_id
        self.whip.start(session_id)
        self.runner.start()

    def stop_session(self) -> None:
        self.whip.stop()
        if self.runner is not None:
            log.info("stopping capture for session %s", self.session_id)
            self.runner.stop()
            self.runner = None
        self.session_id = None


# --------------------------------------------------------------------------------------------------
# Synthetic source (no camera): a looping guided squat for rehearsing the wire without hardware
# --------------------------------------------------------------------------------------------------


def synthetic_outputs(realtime: bool = True, hz: float = 20.0) -> Iterator[FrameResult]:
    """Stand 2 s, raise a hand 2 s, stand through the countdown, then squats every 2.5 s, forever."""
    import numpy as np

    from vision.tests.synth import standing_skeleton

    def frame(t: float, knee: float = 170.0, raised: bool = False) -> FrameResult:
        joints = standing_skeleton()
        joints[[23, 24], 1] -= max(0.0, 170 - knee) * .006
        if raised:
            joints[15, 1] = 1.85
        return FrameResult(t, joints, np.ones(33), angles={"left_knee": knee, "right_knee": knee},
                           image_metrics={"knee_mean": 175 - (170 - knee),
                                          "hip_y": 400 + (.95 - joints[[23, 24], 1].mean()) * 200,
                                          "torso_px": 125}, tracking="single_cam_B", body="ready")

    def knee_at(s: float) -> float:
        # one squat per 2.5 s: down over 1 s to 90°, up over 1 s, 0.5 s rest
        phase = s % 2.5
        if phase < 1.0:
            return 170 - 80 * phase
        if phase < 2.0:
            return 90 + 80 * (phase - 1.0)
        return 170.0

    t0 = time.time()
    i = 0
    while True:
        elapsed = i / hz
        t = t0 + elapsed
        if elapsed < 2.0:
            yield frame(t)
        elif elapsed < 4.0:
            yield frame(t, raised=True)
        elif elapsed < 8.0:
            yield frame(t)
        else:
            yield frame(t, knee_at(elapsed - 8.0))
        i += 1
        if realtime:
            time.sleep(max(0.0, t0 + i / hz - time.time()))


# --------------------------------------------------------------------------------------------------
# Socket loop
# --------------------------------------------------------------------------------------------------


def vision_ws_url(host: str, token: str) -> str:
    host = host.rstrip("/")
    if host.startswith("http://"):
        host = "ws://" + host[len("http://"):]
    elif host.startswith("https://"):
        host = "wss://" + host[len("https://"):]
    elif not host.startswith(("ws://", "wss://")):
        host = "wss://" + host
    return f"{host}/ws/vision?token={token}"


async def run_agent(url: str, agent_factory: Callable[[Callable[[Message], None]], Agent], *,
                    once: bool = False) -> None:
    import websockets

    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue[Message] = asyncio.Queue(maxsize=600)

    def emit(msg: Message) -> None:
        def put() -> None:
            if outbox.full():  # drop the oldest frame rather than stall the engine
                try:
                    outbox.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            outbox.put_nowait(msg)
        loop.call_soon_threadsafe(put)

    agent = agent_factory(emit)
    attempt = 0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=1 << 20) as ws:
                attempt = 0
                log.info("connected to %s; waiting for command start", url.split("?")[0])

                async def pump() -> None:
                    while True:
                        msg = await outbox.get()
                        await ws.send(json.dumps(msg, separators=(",", ":")))

                sender = asyncio.create_task(pump())
                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(msg, dict):
                            agent.handle(msg)
                finally:
                    sender.cancel()
                    agent.stop_session()  # the socket is gone: the privacy gate closes with it
        except asyncio.CancelledError:
            agent.stop_session()
            raise
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            agent.stop_session()
            log.warning("vision socket: %s", exc)
        if once:
            return
        delay = RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)]
        attempt += 1
        log.info("reconnecting in %.0fs", delay)
        await asyncio.sleep(delay)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Mira remote vision agent (camera laptop -> /ws/vision)")
    parser.add_argument("--host", default=os.environ.get("MIRA_HOST", "wss://45.76.255.253"),
                        help="server base URL (ws[s]:// or http[s]://)")
    parser.add_argument("--token", default=os.environ.get("MIRA_VISION_TOKEN", ""),
                        help="MIRA_VISION_TOKEN from the server's .env (env MIRA_VISION_TOKEN)")
    parser.add_argument("--exercise", choices=("squat", "glute_bridge", "single_leg_stance"), default="squat")
    parser.add_argument("--reps", type=int, default=3, help="accepted reps per set")
    parser.add_argument("--sets", type=int, default=1)
    parser.add_argument("--height-cm", type=float, default=None, help="patient height; else config/person.yaml")
    parser.add_argument("--replay", default=None, help="camera-free fallback: a vision recording (.jsonl)")
    parser.add_argument("--synthetic", action="store_true", help="no camera: loop a synthetic guided squat")
    parser.add_argument("--whip-cmd", default=os.environ.get("MIRA_WHIP_CMD"),
                        help="external WHIP publisher command; {whip_url} and {session_id} are substituted")
    parser.add_argument("--whip-url", default=os.environ.get("MIRA_WHIP_URL"),
                        help="WHIP publish URL template, e.g. https://<host>/media/mira/{session_id}/whip")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token or MIRA_VISION_TOKEN is required")
    if args.reps < 1 or args.sets < 1:
        parser.error("--reps and --sets must be positive")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    patient = {} if args.height_cm is None else {"height_cm": args.height_cm}

    def engine_factory() -> Engine:
        if args.synthetic:
            return Engine(source=synthetic_outputs())
        if args.replay:
            return Engine(replay_path=args.replay, replay_realtime=True)
        return Engine()

    whip = WhipPublisher(args.whip_cmd, args.whip_url)

    def agent_factory(emit: Callable[[Message], None]) -> Agent:
        return Agent(engine_factory, emit, exercise_id=args.exercise, reps_target=args.reps, sets_target=args.sets,
                     patient=patient, whip=whip)

    url = vision_ws_url(args.host, args.token)
    try:
        asyncio.run(run_agent(url, agent_factory))
    except KeyboardInterrupt:
        log.info("agent stopped")


if __name__ == "__main__":
    main()
