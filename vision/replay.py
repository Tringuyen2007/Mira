"""Replay a JSONL recording as FrameResults, optionally re-running 3D with the current calibration.

    python -m vision.replay data/recordings/<file>.jsonl [--retriangulate] [--speed 1.0] [--fast]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterator

import numpy as np

from vision.config import CameraConfig, load_cameras
from vision.pipeline import Pipeline, PipelineOutput
from vision.types import FrameResult, PoseResult


def read_header(path) -> dict:
    with open(path) as f:
        header = json.loads(f.readline())
    if header.get("type") != "header":
        raise ValueError(f"{path} does not start with a recording header")
    return header


def cams_from_header(header: dict) -> dict[str, CameraConfig]:
    return {n: CameraConfig.from_dict(n, d) for n, d in (header.get("cameras") or {}).items()}


def iter_frames(path) -> Iterator[tuple[FrameResult, dict[str, PoseResult | None], float | None]]:
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") != "frame":
                continue
            raw = {k: (PoseResult.from_dict(v) if v else None) for k, v in (d.pop("raw") or {}).items()}
            skew = d.pop("skew_s", None)
            d.pop("type")
            yield FrameResult.from_dict(d), raw, skew


def replay_outputs(path, retriangulate: bool = False, cams: dict[str, CameraConfig] | None = None,
                   realtime: bool = True, speed: float = 1.0) -> Iterator[PipelineOutput]:
    pipeline = Pipeline(cams if cams is not None else load_cameras()) if retriangulate else None
    start_t = start_wall = None
    for frame, raw, skew in iter_frames(path):
        if realtime:
            if start_t is None:
                start_t, start_wall = frame.t, time.monotonic()
            else:
                delay = (frame.t - start_t) / speed - (time.monotonic() - start_wall)
                if delay > 0:
                    time.sleep(delay)
        if pipeline is not None:
            yield pipeline.process(frame.t, raw.get("A"), raw.get("B"), skew)
        else:
            yield PipelineOutput(frame=frame, raw=raw, tri=None, skew_s=skew)


def replay(path, retriangulate: bool = False, speed: float = 1.0, realtime: bool = True) -> Iterator[FrameResult]:
    for out in replay_outputs(path, retriangulate=retriangulate, realtime=realtime, speed=speed):
        yield out.frame


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", type=Path)
    p.add_argument("--retriangulate", action="store_true", help="re-run 3D from the raw 2D with the current config")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--fast", action="store_true", help="no real-time pacing")
    args = p.parse_args()
    for out in replay_outputs(args.path, args.retriangulate, realtime=not args.fast, speed=args.speed):
        f = out.frame
        n = int(np.isfinite(f.joints).all(axis=1).sum())
        print(f"{f.t:.3f}  {f.tracking:13s}  joints={n:2d}")


if __name__ == "__main__":
    main()
