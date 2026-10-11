"""Server-facing vision engine. All inputs meet at process_frame()."""
from __future__ import annotations

import queue
import time
import math
from pathlib import Path
from typing import Iterable, Iterator

from vision.config import CONFIG_DIR, REPO_ROOT, load_cameras, load_mat, load_person
from vision.capture import mono_to_unix
from vision.fall import FallDetector
from vision.gestures import GestureDetector
from vision.live import LiveSource
from vision.markers import MarkerDetector, placement_hint
from vision.measures import populate_angles
from vision.moved import MoveMonitor
from vision.physics import PhysicsEstimator
from vision.pipeline import Pipeline, PipelineOutput
from vision.placement import target_aim, target_eye
from vision.presence import PresenceDetector
from vision.replay import replay_outputs
from vision.reps import ExerciseCounter
from vision.session import Session
from vision.types import FrameResult


class Engine:
    """Pull frames with ``frames()`` and drain ``events`` with get_nowait().

    A source may yield FrameResult or PipelineOutput. If omitted, the configured
    live cameras are opened when frames() starts. ``replay_path`` uses saved
    FrameResults by default, so a fallback works even without camera hardware.
    """

    def __init__(self, config_dir=CONFIG_DIR, rules_dir=None, *, source: Iterable | None = None,
                 replay_path: str | Path | None = None, replay_realtime: bool = True,
                 retriangulate: bool = False):
        self.config_dir = Path(config_dir)
        self.rules_dir = Path(rules_dir) if rules_dir is not None else REPO_ROOT / "data" / "rules"
        if source is not None and replay_path is not None:
            raise ValueError("choose source or replay_path")
        self._source = source
        self.replay_path = Path(replay_path) if replay_path is not None else None
        self.replay_realtime, self.retriangulate = replay_realtime, retriangulate
        self.mat = load_mat(self.config_dir / "mat.yaml")
        self.cams = load_cameras(self.config_dir / "cameras.yaml")
        self.events: queue.Queue[dict] = queue.Queue()
        self.session: Session | None = None
        self.counter: ExerciseCounter | None = None
        self.presence = PresenceDetector(self.mat)
        self.gestures = GestureDetector()
        self.fall = FallDetector()
        self.physics = PhysicsEstimator()
        self._last_t: float | None = None
        self._live: LiveSource | None = None
        self._monitor: MoveMonitor | None = None
        self._moved_sent: set[str] = set()
        self._started_monotonic: float | None = None
        self._height_m: float | None = None

    def start(self, exercise_id: str, reps_target: int, sets: int, patient: dict) -> None:
        """Begin a new session. Frames remain pull-driven until frames() is iterated."""
        if self.session is not None and self.session.state not in ("done", "idle"):
            raise RuntimeError("stop the current session first")
        if self._live is not None:
            self._live.stop()
            self._live = None
        patient = patient or {}
        height_cm = patient.get("height_cm")
        if height_cm is None:
            height_cm = load_person(self.config_dir / "person.yaml")
        if height_cm is not None:
            height_cm = float(height_cm)
            if not math.isfinite(height_cm) or height_cm <= 0:
                raise ValueError("patient height_cm must be a positive finite number")
        self._height_m = None if height_cm is None else height_cm / 100.0
        self.counter = ExerciseCounter(exercise_id, patient.get("rule_overrides"), self.rules_dir)
        self.session = Session(reps_target, sets)
        self.presence.reset()
        self.gestures.reset()
        self.fall.reset()
        self.physics.reset()
        self._last_t = None
        self._moved_sent.clear()
        self._started_monotonic = None
        self._monitor = MoveMonitor(self.cams, MarkerDetector(self.mat)) if self.cams else None
        self.events = queue.Queue()

    def stop(self) -> None:
        if self.session is not None:
            self.session.stop()
        if self._live is not None:
            self._live.stop()
            self._live = None
        self.physics.reset()

    def command(self, name: str, t: float | None = None) -> None:
        """Apply a phone command at a FrameResult-clock time."""
        if self.session is None:
            raise RuntimeError("start a session first")
        t = self._last_t if t is None else t
        t = time.time() if t is None else float(t)
        if name == "pause":
            self._emit(self.session.pause(t, "phone"))
            if self.counter:
                self.counter.interrupt()
        elif name == "resume":
            self._emit(self.session.resume(t))
        elif name == "skip":
            if self.session.state == "running":
                self._finish_set(t)
        elif name == "stop":
            self.stop()
        else:
            raise ValueError(f"unknown command: {name}")

    def _emit(self, events: Iterable[dict]) -> None:
        for event in events:
            self.events.put(event)

    def _finish_set(self, t: float) -> None:
        assert self.counter is not None and self.session is not None
        summary = self.counter.finish_set()
        self._emit(self.session.set_done(t, summary.accepted_reps, summary.mean_quality))

    def process_frame(self, frame: FrameResult, *, camera_moved: Iterable[str] = ()) -> FrameResult:
        """One downstream path for live, replay and synthetic frames."""
        if self.session is None or self.counter is None:
            raise RuntimeError("start a session first")
        if self._last_t is not None and frame.t <= self._last_t:
            raise ValueError("frames must have strictly increasing timestamps")
        self._last_t = frame.t
        floor_exercise = self.counter.exercise == "glute_bridge"
        floor = floor_exercise and self.session.state in ("running", "paused", "rest")
        for name in camera_moved:
            if name not in self._moved_sent:
                self._moved_sent.add(name)
                self.physics.reset()
                self._emit([{"type": "camera_moved", "camera": name, "t": frame.t}])
            if "camera_moved" not in frame.flags:
                frame.flags.append("camera_moved")
        if "camera_moved" in frame.flags:
            frame.angles.clear()

        frame.flags = [flag for flag in frame.flags if not flag.startswith("form:")]
        if not frame.angles:
            populate_angles(frame)
        # Re-estimate on both paths so stance/contact evidence is identical for
        # the counter. Pipeline's measurements remain the source of joint angles.
        physical = self.physics.apply(frame)
        state = self.session.state
        presence_events = self.presence.update(frame, in_set=state == "running", floor_exercise=floor)
        frame.on_mat = bool(self.presence.on_mat)
        mid_rep = self.counter.phase not in ("idle", "ready")
        gesture_events = self.gestures.update(frame, mid_rep=mid_rep, floor_exercise=floor)
        self._emit(self.fall.update(frame, floor_exercise=floor_exercise))
        for event in presence_events:
            if event["type"] == "arrived":
                self._emit(self.session.arrived(frame.t))
            elif event["type"] == "paused":
                self._emit(self.session.pause(frame.t, event["reason"]))
                self.counter.interrupt()
        for event in gesture_events:
            if event["type"] == "gesture":
                self._emit(self.session.gesture(frame.t))
            elif event["type"] == "paused":
                self._emit(self.session.pause(frame.t, event["reason"]))
                self.counter.interrupt()
        self._emit(self.session.tick(frame.t))

        if self.session.state == "running":
            update = self.counter.update(frame, physical)
            self._emit({"type": "cue", "id": e.rule_id, "t": e.t} for e in update.rule_events)
            if update.result is not None and update.result.accepted:
                result = update.result
                self._emit([{"type": "rep", "index": result.number, "quality": result.quality,
                             "flags": list(result.violations), "rom": result.rom_deg,
                             "peak_angle": None, "asym_avg": None,
                             "asym_max": result.max_asymmetry_deg, "smoothness": None,
                             "duration_s": result.duration_s, "set": self.session.set_index,
                             "t": frame.t}])
                if self.counter.rep >= self.session.reps_target:
                    self._finish_set(frame.t)
        else:
            self.counter.interrupt()
            frame.phase, frame.rep = self.session.state, self.counter.rep
        return frame

    def _live_outputs(self) -> Iterator[PipelineOutput]:
        source = LiveSource(self.cams, mat=self.mat)
        self._live = source
        pipeline = Pipeline(self.cams, mat=self.mat, height_m=self._height_m)
        try:
            source.start()
            problems = source.wait_ready()
            if problems:
                raise RuntimeError("; ".join(problems))
            self._started_monotonic = time.monotonic()
            while self.session is not None and self.session.state != "done":
                for pair in source.poll():
                    yield pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
        finally:
            source.stop()
            self._live = None

    def _camera_changes(self) -> list[str]:
        if self._live is None or self._monitor is None or self._started_monotonic is None:
            return []
        if time.monotonic() - self._started_monotonic > 2.5:
            return []
        moved = []
        for name, latest in self._live.latest_frames().items():
            if latest is not None and self._monitor.update(name, latest[0], time.monotonic()) == "moved":
                moved.append(name)
        return moved

    def frames(self) -> Iterator[FrameResult]:
        if self.session is None:
            raise RuntimeError("start a session first")
        if self.replay_path is not None:
            inputs = replay_outputs(self.replay_path, retriangulate=self.retriangulate, cams=self.cams,
                                    mat=self.mat, height_m=self._height_m,
                                    realtime=self.replay_realtime)
        elif self._source is not None:
            inputs = self._source
        else:
            inputs = self._live_outputs()
        try:
            for item in inputs:
                if self.session.state == "done":
                    break
                frame = item.frame if isinstance(item, PipelineOutput) else item
                yield self.process_frame(frame, camera_moved=self._camera_changes())
        finally:
            close = getattr(inputs, "close", None)
            if close is not None:
                close()

    def placement_status(self) -> dict:
        """Vision-side payload for Tri's calibration endpoint, for configured cameras."""
        cameras = {}
        for name, cam in sorted(self.cams.items()):
            if not cam.calibrated:
                cameras[name] = {"ok": False, "msg": "Camera is not calibrated"}
                continue
            cameras[name] = placement_hint(cam.R, cam.t, target_eye(cam, self.mat, name),
                                           target_aim(cam, self.mat), self.mat.tol_position_m,
                                           self.mat.tol_angle_deg)
            if self._monitor is not None:
                cameras[name]["marker_status"] = self._monitor.status(name)
        return {"cameras": cameras,
                "board_refined": any(c.extrinsics_source == "board" for c in self.cams.values()),
                "body_check": "pending"}
