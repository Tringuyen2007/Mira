"""Place both cameras using the four floor markers.

    python -m vision.calib_extrinsics

Shows both cameras with detected markers and live placement hints toward the target
spots (2.8 m from mat centre at ±45°, lens 0.85 m, aimed 0.8 m above mat centre).
Adjust the cameras until both say OK, then press s to save (q quits without saving).
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np

from vision.capture import Camera
from vision.config import load_cameras, load_mat, save_cameras
from vision.draw import GREEN, RED, YELLOW, draw_text, fit, pt
from vision.frames import cam_center
from vision.markers import MarkerDetector, average_detections, camera_warnings, placement_hint, solve_camera_pose


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    missing = [n for n in ("A", "B") if n not in cams or not cams[n].has_intrinsics]
    if missing:
        print(f"camera(s) {missing} need lens calibration first: python -m vision.calib_intrinsics --cam X")
        return 1

    detector = MarkerDetector(mat)
    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in ("A", "B")}
    for c in cameras.values():
        c.start()
    history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
    last_seq = {n: -1 for n in cameras}
    results: dict[str, tuple] = {}
    last_solve = last_print = 0.0
    print(__doc__)
    try:
        while True:
            tiles = []
            for n, cam in cameras.items():
                item = cam.latest()
                if item is None:
                    tiles.append(np.zeros((360, 640, 3), np.uint8))
                    continue
                seq, _, frame = item
                if seq != last_seq[n]:
                    last_seq[n] = seq
                    history[n].append(detector.detect(frame))
                tile = fit(frame, (640, 360))
                scale = np.array([640 / frame.shape[1], 360 / frame.shape[0]])
                for i, c in (history[n][-1] if history[n] else {}).items():
                    cv2.polylines(tile, [(c * scale).astype(np.int32)], True, YELLOW, 2)
                    draw_text(tile, str(i), pt(c[0] * scale), YELLOW)
                draw_text(tile, n, (8, 22))
                if n in results:
                    _, _, rms, obs, hint, warns = results[n]
                    draw_text(tile, f"markers {sorted(obs)}  rms {rms:.2f} px", (8, 44))
                    draw_text(tile, hint["msg"], (8, 66), GREEN if hint["ok"] else YELLOW, 0.6)
                    for k, w in enumerate(warns):
                        draw_text(tile, w, (8, 88 + 18 * k), RED, 0.45)
                tiles.append(tile)
            now = time.monotonic()
            if now - last_solve > 0.5:
                last_solve = now
                for n in cameras:
                    obs = average_detections(list(history[n]))
                    sol = solve_camera_pose(obs, mat, cams[n].K, cams[n].dist)
                    if sol is None:
                        results.pop(n, None)
                        continue
                    R, t, rms = sol
                    hint = placement_hint(R, t, mat.camera_targets[n], mat.aim_point, mat.tol_position_m, mat.tol_angle_deg)
                    warns = camera_warnings(R, t) + (["only one marker seen: pose is unreliable"] if len(obs) < 2 else [])
                    results[n] = (R, t, rms, obs, hint, warns)
            if now - last_print > 1.0:
                last_print = now
                for n, (R, t, rms, obs, hint, _) in sorted(results.items()):
                    c = cam_center(R, t)
                    print(f"{n}: markers {sorted(obs)} rms {rms:.2f}px  at ({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f}) "
                          f"{np.linalg.norm(c[[0, 2]]):.2f} m from centre  -> {hint['msg']}")
            cv2.imshow("Mira extrinsics", np.hstack(tiles))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("s"):
                if set(results) != {"A", "B"}:
                    print("both cameras need a marker solution before saving")
                    continue
                for n, (R, t, rms, obs, _, _) in results.items():
                    cfg = cams[n]
                    cfg.R, cfg.t, cfg.extrinsics_rms, cfg.marker_obs = R, t, rms, obs
                    cfg.extrinsics_source = "markers"
                save_cameras(cams)
                print("saved marker extrinsics. Next: python -m vision.calib_refine")
                return 0
    finally:
        for c in cameras.values():
            c.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
