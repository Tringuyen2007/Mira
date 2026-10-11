"""Measure each camera's zoom and pose from the four floor markers, with a bird's-eye map.

    python -m vision.calib_extrinsics

Keep the mat empty. Top: each camera with the detected markers (yellow) and a box showing the space a
person standing on the mat takes up (green = fits, red = cut off). Bottom left: a bird's-eye map of
the mat and where each camera is and what it sees. Bottom right: status.

Zoom: measured from the markers when possible, otherwise the zoom saved from an earlier session, or a
typical-webcam value; calib_refine fine-tunes it either way, so you can always save.

Keys: s save (then run calib_refine), t lock these camera spots so the overlay can guide you back to
them later at this location, q quit without saving.
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np

from vision.capture import Camera, camera_problem
from vision.config import load_cameras, load_mat, save_cameras
from vision.draw import GREEN, RED, WHITE, YELLOW, draw_text, fit, pt
from vision.frames import cam_center
from vision.markers import MarkerDetector, average_detections, camera_warnings, placement_hint, solve_with_zoom
from vision.placement import (
    comparison_lines, draw_overlay, draw_person_box, fit_report, layout, lock_in_place, render_map, swap_warning,
    target_aim, targets_stale,
)

TILE = (640, 360)
ORANGE = (0, 150, 255)
NAMES = ("A", "B")


def _need_both(results: dict[str, tuple], names=NAMES) -> list[str]:
    problems = []
    for n in names:
        if n not in results:
            problems.append(f"camera {n} has no marker solution (it needs to see at least 2 markers)")
            continue
        problems += [f"camera {n}: {w}" for w in results[n][3]]
    return problems


def lock_problems(results: dict[str, tuple], names=NAMES) -> list[str]:
    """Why the current spots can't be locked: a missing camera or a nonsense pose (wrong marker IDs)."""
    return _need_both(results, names)


def save_problems(results: dict[str, tuple], names=NAMES) -> list[str]:
    """Why calibration can't be saved. A rough or assumed zoom is fine: calib_refine fine-tunes it."""
    return _need_both(results, names)


GREY = (150, 150, 150)
LEGEND_WIDTH = 66  # characters that fit the status panel


def _short(reason: str) -> str:
    reason = reason.replace(" (it needs to see at least 2 markers)", ": needs 2 markers")
    return reason if len(reason) <= LEGEND_WIDTH - 14 else reason[:LEGEND_WIDTH - 17] + "..."


def legend_lines(save_blockers: list[str], lock_blockers: list[str]) -> list[tuple[str, tuple]]:
    """What each key does, when to press it, and whether it would work right now."""
    save_state = ("ready", GREEN) if not save_blockers else ("not yet: " + _short(save_blockers[0]), ORANGE)
    lock_state = ("ready", GREEN) if not lock_blockers else ("not yet: " + _short(lock_blockers[0]), ORANGE)
    return [
        ("KEYS", WHITE),
        (f"s  save calibration  [{save_state[0]}]", save_state[1]),
        ("   when both cameras see 2+ markers and their boxes are green", GREY),
        ("   (orange zoom is fine). Then run: python -m vision.calib_refine", GREY),
        (f"t  lock these camera spots (optional)  [{lock_state[0]}]", lock_state[1]),
        ("   once the cameras are where you want them at this location;", GREY),
        ("   next time the overlay guides you back to exactly these spots", GREY),
        ("q  quit without saving", WHITE),
    ]


def pad_tiles(tiles: list[np.ndarray]) -> list[np.ndarray]:
    """Two tiles across, so the layout is the same with one camera (the other slot says why it's empty)."""
    tiles = list(tiles)
    while len(tiles) < 2:
        blank = np.full((TILE[1], TILE[0], 3), 32, np.uint8)
        draw_text(blank, "one-camera setup", (10, 24), WHITE, 0.6)
        tiles.append(blank)
    return tiles


def _status_panel(lines: list[tuple[str, tuple]]) -> np.ndarray:
    img = np.full((TILE[1], TILE[0], 3), 32, np.uint8)
    for k, (text, color) in enumerate(lines):
        draw_text(img, text, (10, 24 + 22 * k), color, 0.5)
    return img


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    names = tuple(n for n in NAMES if n in cams)  # one camera is fine
    if not names:
        print("no camera is configured: run python -m vision.cams first")
        return 1

    detector = MarkerDetector(mat)
    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in names}
    for c in cameras.values():
        c.start()
    history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
    last_seq = {n: -1 for n in cameras}
    best = {n: None for n in cameras}
    results: dict[str, tuple] = {}  # name -> (ZoomSolve, obs, hint or None, warnings, fit messages)
    last_solve = last_print = 0.0
    note = ("", WHITE)
    print(__doc__)
    try:
        while True:
            problems = {n: camera_problem(cams[n], cam.error, cam.actual_size) for n, cam in cameras.items()}
            now = time.monotonic()
            if now - last_solve > 0.5:
                last_solve = now
                for n, cam in cameras.items():
                    if problems[n]:  # e.g. wrong resolution: a pose solved for this image size would be wrong
                        results.pop(n, None)
                        continue
                    size = cam.actual_size or (cams[n].width, cams[n].height)
                    saved = (cams[n].K, cams[n].zoom_std) if cams[n].K is not None else None
                    obs = average_detections(list(history[n]))
                    sol, best[n] = solve_with_zoom(obs, mat, size, best[n], saved)
                    if sol is None:
                        results.pop(n, None)
                        continue
                    warns = camera_warnings(sol.R, sol.t) + (["only one marker seen: pose is unreliable"]
                                                             if len(obs) < 2 else [])
                    hint = None
                    if cams[n].target_eye is not None:
                        hint = placement_hint(sol.R, sol.t, cams[n].target_eye, target_aim(cams[n], mat),
                                              mat.tol_position_m, mat.tol_angle_deg)
                    results[n] = (sol, obs, hint, warns, fit_report(sol.K, sol.R, sol.t, size, mat))
            centers = {n: cam_center(r[0].R, r[0].t) for n, r in results.items() if not r[3]}
            locked = {n: cams[n].target_eye for n in names if cams[n].target_eye is not None}
            stale = targets_stale(centers, locked)

            tiles = []
            for n, cam in cameras.items():
                item = cam.latest()
                if item is None:
                    tile = np.zeros((TILE[1], TILE[0], 3), np.uint8)
                    draw_text(tile, problems[n] or f"{n}: waiting for frames", (8, 22), RED, 0.5)
                    tiles.append(tile)
                    continue
                seq, _, frame = item
                if seq != last_seq[n]:
                    last_seq[n] = seq
                    history[n].append(detector.detect(frame))
                tile = fit(frame, TILE)
                scale = np.array([TILE[0] / frame.shape[1], TILE[1] / frame.shape[0]])
                dets = history[n][-1] if history[n] else {}
                for i, c in dets.items():
                    cv2.polylines(tile, [(c * scale).astype(np.int32)], True, YELLOW, 2)
                    draw_text(tile, str(i), pt(c[0] * scale), YELLOW)
                draw_text(tile, f"{n}: sees markers {sorted(dets) or 'none'}", (8, 22))
                y = 44
                if n in results:
                    sol, obs, hint, warns, fits = results[n]
                    if not warns:
                        draw_person_box(tile, scale, sol.K, sol.R, sol.t, mat, ok=not fits)
                    if hint is not None and not stale and not warns:
                        draw_overlay(tile, scale, sol.K, sol.R, sol.t, mat, cams[n].target_eye, hint,
                                     aim=target_aim(cams[n], mat))
                    good_zoom = sol.quality in ("measured", "saved")
                    draw_text(tile, sol.zoom_msg, (8, y), WHITE if good_zoom else ORANGE, 0.45)
                    y += 20
                    for msg, color in ([(w, RED) for w in warns] + [(f, RED) for f in fits]
                                       or [("a person fits in view", GREEN)]):
                        draw_text(tile, msg, (8, y), color, 0.5)
                        y += 20
                elif problems[n] is None:
                    draw_text(tile, "needs to see at least 2 markers: tilt down or raise the camera", (8, y), RED, 0.45)
                if problems[n]:
                    draw_text(tile, problems[n], (8, y), RED, 0.45)
                tiles.append(tile)

            map_cams = {}
            for n in names:
                v = {"suggested": None if n in locked else mat.camera_targets.get(n), "target": locked.get(n)}
                if n in results and not results[n][3]:
                    sol = results[n][0]
                    v.update(R=sol.R, t=sol.t, K=sol.K, size=cameras[n].actual_size or (cams[n].width, cams[n].height))
                map_cams[n] = v
            layouts = {n: layout(r[0].R, r[0].t) for n, r in results.items() if not r[3]}
            status = [(line, WHITE) for line in comparison_lines(layouts)]
            if swap_warning(layouts):
                status.append((swap_warning(layouts), RED))
            if stale:
                status.append(("locked spots are from another setup: press t to lock these", ORANGE))
            elif not locked:
                status.append(("no spots locked: press t to remember these spots", WHITE))
            status += [("", WHITE)] + legend_lines(save_problems(results, names), lock_problems(results, names)) + [note]
            bottom = np.hstack([render_map(mat, map_cams, TILE), _status_panel(status)])

            if now - last_print > 1.0:
                last_print = now
                for n, (sol, obs, hint, warns, fits) in sorted(results.items()):
                    c = cam_center(sol.R, sol.t)
                    print(f"{n}: markers {sorted(obs)} rms {sol.rms:.2f}px {sol.zoom_msg} at "
                          f"({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f})" + (f" -> {hint['msg']}" if hint else "")
                          + "".join(f" | {m}" for m in warns + fits))
            cv2.imshow("Mira extrinsics", np.vstack([np.hstack(pad_tiles(tiles)), bottom]))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("t"):
                problems_ = lock_problems(results, names)
                if problems_:
                    note = ("cannot lock: " + problems_[0], RED)
                    print("cannot lock spots: " + "; ".join(problems_))
                    continue
                for n in names:
                    sol = results[n][0]
                    cams[n].target_eye, cams[n].target_aim = lock_in_place(sol.R, sol.t, mat.aim_point)
                save_cameras(cams)
                note = ("spots locked", GREEN)
                print("locked camera spots: " + ", ".join(f"{n} {np.round(cams[n].target_eye, 2).tolist()}"
                                                          for n in names))
            if key == ord("s"):
                problems_ = save_problems(results, names)
                if problems_:
                    note = ("cannot save: " + problems_[0], RED)
                    print("cannot save: " + "; ".join(problems_))
                    continue
                for n in names:
                    sol, obs, _, _, _ = results[n]
                    cfg = cams[n]
                    cfg.K, cfg.dist, cfg.zoom_std, cfg.R, cfg.t = sol.K, np.zeros(5), sol.zoom_std, sol.R, sol.t
                    cfg.extrinsics_rms, cfg.marker_obs, cfg.extrinsics_source = sol.rms, obs, "markers"
                save_cameras(cams)
                print("saved zoom and camera positions. Next: python -m vision.calib_refine")
                return 0
    finally:
        for c in cameras.values():
            c.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
