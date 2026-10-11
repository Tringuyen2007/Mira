"""Write pipeline output to JSONL. Skeleton numbers only: never images or video."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vision.config import REPO_ROOT, CameraConfig
from vision.pipeline import PipelineOutput

RECORDINGS_DIR = REPO_ROOT / "data" / "recordings"


def default_path(prefix: str = "") -> Path:
    return RECORDINGS_DIR / f"{prefix}{datetime.now():%Y%m%d-%H%M%S}.jsonl"


class Recorder:
    def __init__(self, path: Path, cams: dict[str, CameraConfig], mat: dict):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self._f = open(self.path, "w")
        self._write({
            "type": "header",
            "version": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "cameras": {n: c.to_dict() for n, c in cams.items()},
            "mat": mat,
        })

    def write(self, out: PipelineOutput) -> None:
        raw = {k: (v.to_dict() if v is not None else None) for k, v in out.raw.items()}
        line = {"type": "frame", **out.frame.to_dict(), "raw": raw, "skew_s": out.skew_s}
        if out.people is not None:  # everyone found, so selection can be re-run on replay
            line["people"] = {k: [p.to_dict() for p in v] for k, v in out.people.items()}
        self._write(line)
        self.count += 1

    def _write(self, obj: dict) -> None:
        self._f.write(json.dumps(obj, allow_nan=False) + "\n")

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
