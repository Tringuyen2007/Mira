"""Pick which camera index is A and which is B.

    python -m vision.cams

Shows every camera that opens, tiled and labelled by index. Press a or b, then the
index digit, to assign; s saves to config/cameras.yaml; q quits without saving.
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np

from vision.capture import BACKEND
from vision.config import CameraConfig, load_cameras, save_cameras
from vision.draw import GREEN, WHITE, YELLOW, draw_text, fit

TILE = (480, 270)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-index", type=int, default=6)
    args = p.parse_args()

    caps = {}
    for i in range(args.max_index):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            caps[i] = cap
        else:
            cap.release()
    if not caps:
        print("No cameras opened. Check the USB connections and the terminal's Camera permission.")
        return 1

    cams = load_cameras()
    assign = {n: c.index for n, c in cams.items()}
    sizes: dict[int, tuple[int, int]] = {}
    pending: str | None = None
    print(__doc__)
    try:
        while True:
            tiles = []
            for i, cap in caps.items():
                ok, frame = cap.read()
                tile = fit(frame, TILE) if ok else np.zeros((TILE[1], TILE[0], 3), np.uint8)
                if ok:
                    sizes[i] = (frame.shape[1], frame.shape[0])
                roles = [n for n, idx in assign.items() if idx == i]
                w, h = sizes.get(i, (0, 0))
                draw_text(tile, f"index {i}  {w}x{h}", (8, 22), WHITE, 0.6)
                if roles:
                    draw_text(tile, "camera " + "/".join(roles), (8, 48), GREEN, 0.8)
                tiles.append(tile)
            while len(tiles) % 3:
                tiles.append(np.zeros_like(tiles[0]))
            grid = np.vstack([np.hstack(tiles[r:r + 3]) for r in range(0, len(tiles), 3)])
            prompt = f"assign {pending}: press the index digit" if pending else "a/b then digit to assign, s save, q quit"
            draw_text(grid, prompt, (8, grid.shape[0] - 12), YELLOW, 0.6)
            cv2.imshow("Mira cameras", grid)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("a"), ord("b")):
                pending = chr(key).upper()
            elif pending and ord("0") <= key <= ord("9") and (key - ord("0")) in caps:
                assign[pending] = key - ord("0")
                pending = None
            elif key == ord("s"):
                for name, idx in sorted(assign.items()):
                    old = cams.get(name)
                    if old is not None and old.index == idx:
                        continue
                    if old is not None and (old.has_intrinsics or old.calibrated):
                        print(f"camera {name} moved from index {old.index} to {idx}: its calibration was cleared")
                    w, h = sizes.get(idx, (1280, 720))
                    cams[name] = CameraConfig(name=name, index=idx, hint=f"{w}x{h} at index {idx}")
                save_cameras(cams)
                print("saved:", {n: c.index for n, c in cams.items()})
                return 0
            elif key in (ord("q"), 27):
                return 0
    finally:
        for cap in caps.values():
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
