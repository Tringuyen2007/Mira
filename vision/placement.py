"""Camera placement: saved symmetric targets, the ghost mat overlay and the A/B comparison."""
from __future__ import annotations

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.draw import GREEN, RED, WHITE, YELLOW, draw_text, pt
from vision.frames import cam_center, look_at_pose, project

MIRROR = np.array([-1.0, 1.0, 1.0])  # reflect across the mat's centreline (x -> -x)


def target_eye(cam: CameraConfig | None, mat: MatConfig, name: str) -> np.ndarray:
    """The locked target if there is one, else the mat.yaml default."""
    if cam is not None and cam.target_eye is not None:
        return np.asarray(cam.target_eye, float)
    return np.asarray(mat.camera_targets[name], float)


def lock_targets(center_a, center_b) -> tuple[np.ndarray, np.ndarray]:
    """A symmetric target pair from where the cameras are now: A's spot averaged with B's mirrored spot."""
    eye_a = (np.asarray(center_a, float) + np.asarray(center_b, float) * MIRROR) / 2
    return eye_a, eye_a * MIRROR


def target_pose(eye, aim) -> tuple[np.ndarray, np.ndarray]:
    return look_at_pose(np.asarray(eye, float), np.asarray(aim, float))


def ghost_polylines(mat: MatConfig, K, eye, aim) -> list[np.ndarray]:
    """Mat outline and marker squares as they would appear from the target spot (pixels, undistorted)."""
    R, t = target_pose(eye, aim)
    shapes = [np.array([[x, 0.0, z] for x, z in mat.mat_polygon()])] + list(mat.marker_corners().values())
    out = []
    for X in shapes:
        if np.all((X @ R.T + t)[:, 2] > 0.05):  # skip anything behind the camera
            out.append(project(X, K, None, R, t))
    return out


def layout(R, t) -> dict:
    """Floor distance from mat centre, height and bearing around the mat (A ≈ +45°, B ≈ -45°)."""
    c = cam_center(R, t)
    return {"dist_m": float(np.hypot(c[0], c[2])), "height_m": float(c[1]),
            "bearing_deg": float(np.degrees(np.arctan2(c[0], c[2])))}


def comparison_lines(layouts: dict[str, dict]) -> list[str]:
    lines = [f"{n}: {v['dist_m']:.2f} m from centre, {v['height_m']:.2f} m high, at {v['bearing_deg']:+.0f}°"
             for n, v in sorted(layouts.items())]
    if "A" in layouts and "B" in layouts:
        a, b = layouts["A"], layouts["B"]
        lines.append(f"A-B difference: distance {abs(a['dist_m'] - b['dist_m']) * 100:.0f} cm, "
                     f"height {abs(a['height_m'] - b['height_m']) * 100:.0f} cm, "
                     f"angle {abs(abs(a['bearing_deg']) - abs(b['bearing_deg'])):.1f}°")
    return lines


def _dashed(img, poly, color, dash_px: float = 10.0) -> None:
    poly = np.vstack([poly, poly[:1]])
    for p, q in zip(poly[:-1], poly[1:]):
        n = max(int(np.linalg.norm(q - p) / dash_px), 1)
        for k in range(0, n, 2):
            a, b = p + (q - p) * k / n, p + (q - p) * min(k + 1, n) / n
            cv2.line(img, pt(a), pt(b), color, 2)


def draw_overlay(tile, scale, K, R, t, mat: MatConfig, eye, hint: dict | None) -> None:
    """Crosshair at the image centre, aim dot, ghost mat, and the position/aim instructions."""
    for poly in ghost_polylines(mat, K, eye, mat.aim_point):
        _dashed(tile, poly * scale, GREEN)
    c = np.asarray(K, float)[:2, 2] * scale
    cv2.line(tile, pt(c - [14, 0]), pt(c + [14, 0]), WHITE, 1)
    cv2.line(tile, pt(c - [0, 14]), pt(c + [0, 14]), WHITE, 1)
    if R is not None:
        aim_px = project(mat.aim_point, K, None, R, t)[0] * scale
        if np.all(np.isfinite(aim_px)):
            cv2.circle(tile, pt(aim_px), 6, RED, 2)
    if hint is not None:
        y = tile.shape[0] - 40
        draw_text(tile, hint["position_msg"], (8, y), GREEN if hint["position_ok"] else YELLOW, 0.6)
        draw_text(tile, hint["aim_msg"], (8, y + 24), GREEN if hint["aim_ok"] else YELLOW, 0.6)
