"""Download MediaPipe model files into vision/models/.

    python -m vision.models.download
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent
BASE = "https://storage.googleapis.com/mediapipe-models"
MODELS = {
    "pose_landmarker_full.task": f"{BASE}/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "pose_landmarker_heavy.task": f"{BASE}/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
    "hand_landmarker.task": f"{BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
}


def main() -> None:
    for name, url in MODELS.items():
        dest = MODEL_DIR / name
        if dest.exists():
            print(f"have {name} ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        tmp = dest.with_suffix(".part")
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
        print(f"  saved {dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
