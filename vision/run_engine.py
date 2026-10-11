"""Run a guided vision session with the debug-style camera preview.

    python -m vision.run_engine --exercise squat --reps 3 --height-cm 170
"""
from __future__ import annotations

import argparse
import json
import queue
import time

import cv2
import numpy as np

from vision.avatar import AvatarView
from vision.capture import mono_to_unix
from vision.debug_view import ViewMouse, compose_view
from vision.live import LiveSource
from vision.markers import MarkerDetector
from vision.moved import MoveMonitor
from vision.pipeline import Pipeline, PipelineOutput
from vision.record import Recorder, default_path
from vision.config import mat_raw
from vision.engine import Engine

WINDOW = "Mira exercise"
BANNER_H = 80


def _with_session_banner(image: np.ndarray, engine: Engine, completed_reps: int = 0) -> np.ndarray:
    """Put the accepted rep count where it remains readable over any camera scene."""
    banner = np.full((BANNER_H, image.shape[1], 3), (28, 28, 28), dtype=np.uint8)
    count = max(completed_reps, engine.counter.rep if engine.counter is not None else 0)
    target = engine.session.reps_target if engine.session is not None else 0
    state = engine.session.state if engine.session is not None else "idle"
    exercise = engine.counter.exercise if engine.counter is not None else "exercise"
    prompts = {
        "idle": "Step onto the mat",
        "awaiting_gesture": "Raise a hand to start",
        "countdown": "Get ready",
        "running": f"Do {exercise.replace('_', ' ')}",
        "paused": "Paused",
        "rest": "Rest",
        "done": "Set complete  -  press q to close",
    }
    cv2.putText(banner, f"REPS  {count} / {target}", (18, 53), cv2.FONT_HERSHEY_SIMPLEX,
                1.35, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(banner, prompts.get(state, state), (650, 49), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (100, 230, 255), 2, cv2.LINE_AA)
    return np.vstack((banner, image))


def run_preview(engine: Engine, record: bool = False) -> None:
    """Feed the live pipeline into Engine while drawing the same panels as debug_view."""
    cams, mat = engine.cams, engine.mat
    source = LiveSource(cams, mat=mat)
    pipeline = Pipeline(cams, mat=mat, height_m=engine._height_m)
    detector = MarkerDetector(mat)
    monitor = MoveMonitor(cams, detector)
    view = AvatarView()
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, ViewMouse(view, panel=(640, 360 + BANNER_H,
                                                        1280, 720 + BANNER_H)))
    last: PipelineOutput | None = None
    last_draw = 0.0
    show_markers = False
    completed_reps = 0
    recorder = Recorder(default_path(), cams, mat_raw()) if record else None
    if recorder is not None:
        print(f"Saving pose recording to {recorder.path}", flush=True)
    try:
        with source:
            problems = source.wait_ready()
            if problems:
                raise RuntimeError("; ".join(problems))
            while True:
                if engine.session.state != "done":
                    for pair in source.poll(0.005):
                        moved = [name for name in cams if monitor.status(name) == "moved"]
                        last = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s,
                                                camera_moved=bool(moved))
                        engine.process_frame(last.frame, camera_moved=moved)
                        if recorder is not None:
                            recorder.write(last)
                        while True:
                            try:
                                event = engine.events.get_nowait()
                            except queue.Empty:
                                break
                            if event["type"] == "set_done":
                                completed_reps = event["reps"]
                            elif event["type"] == "rep":
                                completed_reps = event["index"]
                            elif event["type"] == "countdown" and event["n"] == 3 and engine.session.set_index > 1:
                                completed_reps = 0
                            print(json.dumps(event), flush=True)
                        if engine.session.state == "done":
                            break
                now = time.monotonic()
                if now - last_draw >= 1 / 30:
                    last_draw = now
                    latest = source.latest_frames()
                    images = {name: item[0] for name, item in latest.items() if item}
                    for name, image in images.items():
                        monitor.update(name, image, now)
                    poses = last.raw if last is not None else {}
                    image = compose_view(cams, mat, images, poses, last, source.stats(),
                                         source.skew.summary(), None,
                                         detector if show_markers else None,
                                         {name: monitor.message(name) for name in cams}, view,
                                         keys="keys: m markers   q quit")
                    cv2.imshow(WINDOW, _with_session_banner(image, engine, completed_reps))
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("m"):
                    show_markers = not show_markers
                view.handle_key(key)
    finally:
        if recorder is not None:
            recorder.close()
            print(f"Saved {recorder.count} frames to {recorder.path}", flush=True)
        cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live guided vision session")
    parser.add_argument("--exercise", choices=("squat", "glute_bridge", "single_leg_stance"),
                        default="squat")
    parser.add_argument("--reps", type=int, default=3, help="accepted reps per set")
    parser.add_argument("--sets", type=int, default=1)
    parser.add_argument("--height-cm", type=float, default=None,
                        help="patient height; otherwise use config/person.yaml")
    parser.add_argument("--record", action="store_true", help="save pose data for checking rep decisions")
    args = parser.parse_args()
    if args.reps < 1 or args.sets < 1:
        parser.error("--reps and --sets must be positive")

    engine = Engine()
    patient = {} if args.height_cm is None else {"height_cm": args.height_cm}
    engine.start(args.exercise, args.reps, args.sets, patient)
    print("Waiting for the person to enter the mat. Raise a hand when prompted; press q to close.",
          flush=True)
    try:
        run_preview(engine, record=args.record)
    except KeyboardInterrupt:
        print("Session stopped.", flush=True)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
