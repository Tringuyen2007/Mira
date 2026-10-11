"""Refine both camera poses from ~20 s of you moving on the mat.

    python -m vision.calib_refine            # live capture (also saved as a recording)
    python -m vision.calib_refine --from data/recordings/refine-<time>.jsonl

During capture: walk around the mat, squat, raise and spread your arms, turn.
Stay visible to both cameras. Requires calib_extrinsics first. Refuses to start if a camera has
moved since calib_extrinsics (--force overrides). Refines camera poses and zoom.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from vision.bundle import refine_extrinsics
from vision.capture import mono_to_unix
from vision.config import load_cameras, load_mat, mat_raw, save_cameras
from vision.draw import GREEN, YELLOW, draw_skeleton, draw_text, fit
from vision.markers import MarkerDetector
from vision.moved import MoveMonitor
from vision.pipeline import Pipeline
from vision.record import Recorder, default_path
from vision.replay import iter_frames
from vision.types import CameraPeople

COUNTDOWN_S = 8.0  # time to walk from the laptop to the mat
REFINED_ZOOM_STD = 0.02  # a zoom that survived the body refinement counts as measured


def refined_zoom_std(previous: float | None) -> float:
    return REFINED_ZOOM_STD if previous is None else min(previous, REFINED_ZOOM_STD)
MOVE_CHECK_S = 2.0


def moved_cameras(src, mat, seconds: float = MOVE_CHECK_S) -> list[str]:
    """Watch the markers for a moment; names of cameras whose markers moved since calib_extrinsics."""
    monitor = MoveMonitor(src.cams, MarkerDetector(mat), interval_s=0.25)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for n, v in src.latest_frames().items():
            if v is not None:
                monitor.update(n, v[0], time.monotonic())
        time.sleep(0.02)
    for n in sorted(src.cams):
        if monitor.message(n):
            print(monitor.message(n))
    return [n for n in sorted(src.cams) if monitor.status(n) == "moved"]


def capture_live(cams, mat, model: str, seconds: float, force: bool = False):
    from vision.live import LiveSource

    pairs = []
    path = default_path("refine-")
    pipeline = Pipeline(cams, mat=mat)
    with LiveSource(cams, model, mat) as src:
        problems = src.wait_ready()
        if problems:
            print("cannot capture: " + "; ".join(problems))
            return None
        moved = moved_cameras(src, mat)
        if moved and not force:
            print(f"camera(s) {moved} moved since calib_extrinsics: re-run it first (or --force)")
            return None
        with Recorder(path, cams, mat_raw()) as rec:
            _capture(src, rec, pipeline, pairs, seconds)
    cv2.destroyAllWindows()
    print(f"saved capture to {path} ({len(pairs)} frames)")
    return pairs


def refine_views(pair, out):
    """The two views to refine from. With exactly one person in each camera they are the same person,
    so use them as found: person selection needs the cameras to agree under the current calibration,
    which would starve the refinement of exactly the data it needs to fix a calibration that is off.
    Otherwise the person the pipeline selected."""
    if (isinstance(pair.a, CameraPeople) and isinstance(pair.b, CameraPeople)
            and len(pair.a.people) == 1 and len(pair.b.people) == 1):
        return pair.a.people[0], pair.b.people[0]
    return out.raw["A"], out.raw["B"]


def _capture(src, rec, pipeline, pairs, seconds: float) -> None:
    start = time.monotonic()
    while (elapsed := time.monotonic() - start) < COUNTDOWN_S + seconds:
        capturing = elapsed >= COUNTDOWN_S
        for pair in src.poll(0.005):
            if capturing:
                out = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
                rec.write(out)
                pairs.append(refine_views(pair, out))
        tiles = []
        for n, v in sorted(src.latest_frames().items()):
            if v is None:
                tiles.append(np.zeros((360, 640, 3), np.uint8))
                continue
            frame, found = v
            tile = fit(frame, (640, 360))
            for pose in found.people:
                draw_skeleton(tile, pose.lm2d * [640 / frame.shape[1], 360 / frame.shape[0]],
                              ok=pose.vis >= 0.5, color=GREEN)
            tiles.append(tile)
        msg = (f"capturing: {COUNTDOWN_S + seconds - elapsed:.0f} s left, keep moving"
               if capturing else f"get ready: {COUNTDOWN_S - elapsed:.0f}")
        img = np.hstack(tiles)
        draw_text(img, msg, (10, 30), GREEN if capturing else YELLOW, 0.8)
        cv2.imshow("Mira refine", img)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--from", dest="from_path", type=Path)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    p.add_argument("--marker-weight", type=float, default=5.0)
    p.add_argument("--force", action="store_true",
                   help="capture even if a camera moved; save even if the guards reject the result")
    args = p.parse_args()

    cams, mat = load_cameras(), load_mat()
    if args.from_path:
        pairs = [(raw.get("A"), raw.get("B")) for _, raw, _ in iter_frames(args.from_path)]
    else:
        print(__doc__)
        pairs = capture_live(cams, mat, args.model, args.seconds, args.force)
        if pairs is None:
            return 1
    try:
        report = refine_extrinsics(cams, mat, pairs, marker_weight=args.marker_weight)
    except ValueError as e:
        print(f"refinement not possible: {e}")
        return 1
    print(report.summary())
    if not report.ok and not args.force:
        print("not saved (use --force to override)")
        return 1
    for n in ("A", "B"):
        cams[n].R, cams[n].t, cams[n].K = report.R[n], report.t[n], report.K[n]
        if report.ok:  # a forced save must not make a rejected zoom look trustworthy for later sessions
            cams[n].zoom_std = refined_zoom_std(cams[n].zoom_std)
        cams[n].extrinsics_source, cams[n].extrinsics_rms = "bundle", report.body_rms_after
    save_cameras(cams)
    print("saved refined extrinsics. Next: python -m vision.debug_view and python -m vision.checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
