"""Lens calibration for one webcam from a checkerboard shown on a screen.

    python -m vision.calib_intrinsics --cam A
    python -m vision.calib_intrinsics --save-board board.png   # to show on an iPad/other screen

1. Move the 'board' window to the screen you will film (f toggles full screen).
2. Measure the width of one row of 10 squares in mm with a ruler, press space,
   and type it in the terminal (or pass --row-mm).
3. Point the webcam at the board from many angles, distances and positions;
   hold still briefly to capture. 25 views are captured automatically.
Calibrating clears that camera's extrinsics (they depend on the lens model).
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np

from vision.capture import Camera
from vision.config import load_cameras, save_cameras
from vision.draw import GREEN, RED, WHITE, draw_text, fit

PATTERN = (9, 6)  # inner corners (cols, rows): a 10 x 7 square board
SQUARES_ACROSS = PATTERN[0] + 1


def board_image(square_px: int = 120, margin_px: int = 60) -> np.ndarray:
    cols, rows = PATTERN[0] + 1, PATTERN[1] + 1
    img = np.full((rows * square_px + 2 * margin_px, cols * square_px + 2 * margin_px), 255, np.uint8)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                y, x = margin_px + r * square_px, margin_px + c * square_px
                img[y:y + square_px, x:x + square_px] = 0
    return img


def board_object_points(square_m: float) -> np.ndarray:
    grid = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)
    return np.hstack([grid * square_m, np.zeros((len(grid), 1))]).astype(np.float32)


def view_descriptor(corners, size) -> np.ndarray:
    """Position, scale and perspective tilt of a detected board, for picking varied views."""
    c = np.asarray(corners, float).reshape(-1, 2)
    w, h = size
    g = c.reshape(PATTERN[1], PATTERN[0], 2)
    top, bottom = np.linalg.norm(g[0, -1] - g[0, 0]), np.linalg.norm(g[-1, -1] - g[-1, 0])
    left, right = np.linalg.norm(g[-1, 0] - g[0, 0]), np.linalg.norm(g[-1, -1] - g[0, -1])
    span = c.max(axis=0) - c.min(axis=0)
    return np.array([
        c[:, 0].mean() / w,
        c[:, 1].mean() / h,
        np.sqrt(span[0] * span[1] / (w * h)),
        2 * (top - bottom) / (top + bottom),
        2 * (left - right) / (left + right),
    ])


def is_novel(desc, previous, threshold: float = 0.08) -> bool:
    return all(np.linalg.norm(desc - p) > threshold for p in previous)


def calibrate(obj_points, img_points, size):
    obj = [np.asarray(o, np.float32) for o in obj_points]
    img = [np.asarray(i, np.float32).reshape(-1, 1, 2) for i in img_points]
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj, img, size, None, None)
    errs = []
    for o, i, r, t in zip(obj, img, rvecs, tvecs):
        proj, _ = cv2.projectPoints(o, r, t, K, dist)
        errs.append(float(np.sqrt(np.mean(np.sum((proj - i) ** 2, axis=2)))))
    return float(rms), K, dist.ravel(), np.array(errs)


def calibrate_with_outlier_drop(obj_points, img_points, size):
    """Calibrate, drop views with error > 2x the median once, recalibrate."""
    rms, K, dist, errs = calibrate(obj_points, img_points, size)
    keep = errs <= 2 * np.median(errs)
    if keep.all() or keep.sum() < 10:
        return rms, K, dist, errs, 0
    obj = [o for o, k in zip(obj_points, keep) if k]
    img = [i for i, k in zip(img_points, keep) if k]
    rms, K, dist, errs = calibrate(obj, img, size)
    return rms, K, dist, errs, int((~keep).sum())


def _wait_for_board(board: np.ndarray) -> bool:
    cv2.namedWindow("board", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    full = False
    while True:
        cv2.imshow("board", board)
        key = cv2.waitKey(50) & 0xFF
        if key == ord("f"):
            full = not full
            cv2.setWindowProperty("board", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN if full else cv2.WINDOW_NORMAL)
        elif key == ord(" "):
            return True
        elif key in (ord("q"), 27):
            return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cam", choices=["A", "B"])
    p.add_argument("--row-mm", type=float, help="measured width of one row of 10 squares, in mm")
    p.add_argument("--views", type=int, default=25)
    p.add_argument("--save-board", help="write the board as a PNG and exit")
    args = p.parse_args()

    if args.save_board:
        cv2.imwrite(args.save_board, board_image(160, 80))
        print(f"wrote {args.save_board}: show it full-screen, then measure the 10-square row")
        return 0
    if not args.cam:
        p.error("--cam is required")
    cams = load_cameras()
    if args.cam not in cams:
        print(f"camera {args.cam} is not configured: run python -m vision.cams first")
        return 1
    cfg = cams[args.cam]

    print(__doc__)
    board = board_image()
    if not _wait_for_board(board):
        return 1
    row_mm = args.row_mm or float(input("Width of one row of 10 squares, in mm: "))
    square_m = row_mm / SQUARES_ACROSS / 1000
    obj = board_object_points(square_m)

    cam = Camera(args.cam, cfg.index, cfg.width, cfg.height)
    cam.start()
    views, descs = [], []
    last_corners, last_seq, size = None, -1, None
    try:
        while len(views) < args.views:
            status = board.copy()
            draw_text(status, f"{len(views)}/{args.views} captured", (10, 40), 0, 1.0)
            cv2.imshow("board", status)
            item = cam.latest()
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 1
            if item is None or item[0] == last_seq:
                continue
            last_seq, _, frame = item
            size = (frame.shape[1], frame.shape[0])
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCornersSB(gray, PATTERN, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
            preview = frame.copy()
            if found:
                c = corners.reshape(-1, 2)
                still = last_corners is not None and np.mean(np.linalg.norm(c - last_corners, axis=1)) < 1.5
                last_corners = c
                desc = view_descriptor(c, size)
                cv2.drawChessboardCorners(preview, PATTERN, corners, found)
                if still and is_novel(desc, descs):
                    views.append(c.astype(np.float32))
                    descs.append(desc)
                    cv2.rectangle(preview, (0, 0), (size[0] - 1, size[1] - 1), GREEN, 20)
                    print(f"captured {len(views)}/{args.views}")
            else:
                last_corners = None
            preview = fit(preview, (960, 540))
            draw_text(preview, f"{len(views)}/{args.views}: vary angle, distance, position; hold still to capture",
                      (10, 25), WHITE if found else RED, 0.6)
            cv2.imshow("camera", preview)
    finally:
        cam.stop()
        cv2.destroyAllWindows()

    rms, K, dist, errs, dropped = calibrate_with_outlier_drop([obj] * len(views), views, size)
    print(f"camera {args.cam}: RMS {rms:.3f} px ({len(errs)} views, {dropped} dropped)")
    print(f"  fx {K[0, 0]:.1f}  fy {K[1, 1]:.1f}  cx {K[0, 2]:.1f}  cy {K[1, 2]:.1f}")
    print(f"  worst views (px): {np.round(np.sort(errs)[-5:], 3).tolist()}")
    if rms >= 0.5:
        print("  WARNING: RMS >= 0.5 px. Re-run with steadier, more varied views.")
    cfg.K, cfg.dist, cfg.intrinsics_rms = K, dist, rms
    cfg.width, cfg.height = size
    if cfg.R is not None:
        print("  extrinsics cleared: re-run python -m vision.calib_extrinsics")
    cfg.R = cfg.t = cfg.extrinsics_source = cfg.extrinsics_rms = None
    save_cameras(cams)
    print("saved config/cameras.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
