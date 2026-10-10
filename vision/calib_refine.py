"""Refine both camera poses from ~20 s of you moving on the mat.

    python -m vision.calib_refine            # live capture (also saved as a recording)
    python -m vision.calib_refine --from data/recordings/refine-<time>.jsonl

During capture: walk around the mat, squat, raise and spread your arms, turn.
Stay visible to both cameras. Requires lens calibration and calib_extrinsics first.
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
from vision.pipeline import Pipeline
from vision.record import Recorder, default_path
from vision.replay import iter_frames

COUNTDOWN_S = 3.0


def capture_live(cams, model: str, seconds: float):
    from vision.live import LiveSource

    pairs = []
    path = default_path("refine-")
    pipeline = Pipeline(cams)
    with LiveSource(cams, model) as src, Recorder(path, cams, mat_raw()) as rec:
        start = time.monotonic()
        while (elapsed := time.monotonic() - start) < COUNTDOWN_S + seconds:
            capturing = elapsed >= COUNTDOWN_S
            for pair in src.poll(0.005):
                if capturing:
                    rec.write(pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s))
                    pairs.append((pair.a, pair.b))
            tiles = []
            for n, v in sorted(src.latest_frames().items()):
                if v is None:
                    tiles.append(np.zeros((360, 640, 3), np.uint8))
                    continue
                frame, pose = v
                tile = fit(frame, (640, 360))
                if pose.detected:
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
    cv2.destroyAllWindows()
    print(f"saved capture to {path} ({len(pairs)} frames)")
    return pairs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--from", dest="from_path", type=Path)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    p.add_argument("--marker-weight", type=float, default=5.0)
    p.add_argument("--force", action="store_true", help="save even if the guards reject the result")
    args = p.parse_args()

    cams, mat = load_cameras(), load_mat()
    if args.from_path:
        pairs = [(raw.get("A"), raw.get("B")) for _, raw, _ in iter_frames(args.from_path)]
    else:
        print(__doc__)
        pairs = capture_live(cams, args.model, args.seconds)
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
        cams[n].R, cams[n].t = report.R[n], report.t[n]
        cams[n].extrinsics_source, cams[n].extrinsics_rms = "bundle", report.body_rms_after
    save_cameras(cams)
    print("saved refined extrinsics. Next: python -m vision.debug_view and python -m vision.checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
