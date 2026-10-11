"""Measure each camera's zoom and pose from the four floor markers, and guide placement.

    python -m vision.calib_extrinsics

Keep the mat empty. Each camera shows the detected markers (yellow), a ghost of where the mat should
appear from the target spot (green dashed), a crosshair and an aim dot (red), and instructions:
position first, then aim. Keys: t lock the current spots as the symmetric target (first time, or
after moving rooms), s save, q quit without saving.
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np

from vision.capture import Camera, camera_problem
from vision.config import load_cameras, load_mat, save_cameras
from vision.draw import RED, WHITE, YELLOW, draw_text, fit, pt
from vision.frames import cam_center
from vision.markers import MarkerDetector, average_detections, camera_warnings, placement_hint, solve_with_zoom
from vision.placement import comparison_lines, draw_overlay, layout, lock_targets, target_eye

TILE = (640, 360)


def lock_problems(results: dict[str, tuple]) -> list[str]:
    """Why the current poses can't be locked as targets: a target saved from a guessed zoom or a
    nonsense pose (wrong marker IDs) would mislead every later session."""
    problems = []
    for n in ("A", "B"):
        if n not in results:
            problems.append(f"camera {n} has no marker solution")
            continue
        sol, _, _, warns = results[n]
        if sol.guessed:
            problems.append(f"camera {n} needs a measured zoom (markers at both ends of the mat)")
        problems += [f"camera {n}: {w}" for w in warns]
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    missing = [n for n in ("A", "B") if n not in cams]
    if missing:
        print(f"camera(s) {missing} are not configured: run python -m vision.cams first")
        return 1

    detector = MarkerDetector(mat)
    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in ("A", "B")}
    for c in cameras.values():
        c.start()
    history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
    last_seq = {n: -1 for n in cameras}
    last_good = {n: None for n in cameras}
    results: dict[str, tuple] = {}  # name -> (ZoomSolve, obs, hint, warnings)
    last_solve = last_print = 0.0
    print(__doc__)
    try:
        while True:
            tiles = []
            problems = {n: camera_problem(cams[n], cam.error, cam.actual_size) for n, cam in cameras.items()}
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
                for i, c in (history[n][-1] if history[n] else {}).items():
                    cv2.polylines(tile, [(c * scale).astype(np.int32)], True, YELLOW, 2)
                    draw_text(tile, str(i), pt(c[0] * scale), YELLOW)
                draw_text(tile, n, (8, 22))
                y = 44
                if n in results:
                    sol, obs, hint, warns = results[n]
                    draw_overlay(tile, scale, sol.K, sol.R, sol.t, mat, target_eye(cams[n], mat, n), hint)
                    draw_text(tile, f"markers {sorted(obs)}  rms {sol.rms:.2f} px", (8, y))
                    draw_text(tile, sol.zoom_msg, (8, y + 22), YELLOW if sol.guessed else WHITE, 0.45)
                    y += 44
                    for w in warns:
                        draw_text(tile, w, (8, y), RED, 0.45)
                        y += 18
                if problems[n]:
                    draw_text(tile, problems[n], (8, y), RED, 0.45)
                tiles.append(tile)
            now = time.monotonic()
            if now - last_solve > 0.5:
                last_solve = now
                for n, cam in cameras.items():
                    if problems[n]:  # e.g. wrong resolution: a pose solved for this image size would be wrong
                        results.pop(n, None)
                        continue
                    obs = average_detections(list(history[n]))
                    sol, last_good[n] = solve_with_zoom(obs, mat, cam.actual_size or (cams[n].width, cams[n].height),
                                                        last_good[n])
                    if sol is None:
                        results.pop(n, None)
                        continue
                    hint = placement_hint(sol.R, sol.t, target_eye(cams[n], mat, n), mat.aim_point,
                                          mat.tol_position_m, mat.tol_angle_deg)
                    warns = camera_warnings(sol.R, sol.t) + (["only one marker seen: pose is unreliable"]
                                                             if len(obs) < 2 else [])
                    results[n] = (sol, obs, hint, warns)
            layouts = {n: layout(r[0].R, r[0].t) for n, r in results.items()}
            strip = np.zeros((70, TILE[0] * 2, 3), np.uint8)
            for k, line in enumerate(comparison_lines(layouts)):
                draw_text(strip, line, (8, 20 + 20 * k), WHITE, 0.5)
            if now - last_print > 1.0:
                last_print = now
                for n, (sol, obs, hint, _) in sorted(results.items()):
                    c = cam_center(sol.R, sol.t)
                    print(f"{n}: markers {sorted(obs)} rms {sol.rms:.2f}px {sol.zoom_msg} at "
                          f"({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f}) -> {hint['msg']}")
            cv2.imshow("Mira extrinsics", np.vstack([np.hstack(tiles), strip]))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("t"):
                problems = lock_problems(results)
                if problems:
                    print("cannot lock targets: " + "; ".join(problems))
                    continue
                eye_a, eye_b = lock_targets(*(cam_center(results[n][0].R, results[n][0].t) for n in ("A", "B")))
                cams["A"].target_eye, cams["B"].target_eye = eye_a, eye_b
                save_cameras(cams)
                print(f"locked targets: A {np.round(eye_a, 2).tolist()}  B {np.round(eye_b, 2).tolist()}")
            if key == ord("s"):
                unsure = [n for n in ("A", "B") if n not in results or results[n][0].guessed]
                if unsure:
                    print(f"camera(s) {unsure} need a measured zoom (markers at both ends of the mat) before saving")
                    continue
                for n, (sol, obs, _, _) in results.items():
                    cfg = cams[n]
                    cfg.K, cfg.dist, cfg.R, cfg.t = sol.K, np.zeros(5), sol.R, sol.t
                    cfg.extrinsics_rms, cfg.marker_obs, cfg.extrinsics_source = sol.rms, obs, "markers"
                save_cameras(cams)
                print("saved zoom and marker extrinsics. Next: python -m vision.calib_refine")
                return 0
    finally:
        for c in cameras.values():
            c.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
