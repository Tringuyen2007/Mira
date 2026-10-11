"""Camera placement: spots locked in place, the ghost mat, the "does a person fit" box, one-step
instructions, the A/B comparison and the bird's-eye map."""
from __future__ import annotations

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.draw import GREEN, RED, WHITE, YELLOW, draw_text, pt
from vision.frames import cam_center, look_at_pose, project

PERSON_HEIGHT_M = 1.8  # the box above the mat that should stay in view
STALE_TARGET_M = 1.0  # a locked spot further than this from the camera belongs to another setup
CAM_COLORS = {"A": (0, 200, 255), "B": (255, 200, 0)}
GREY = (150, 150, 150)

MIRROR = np.array([-1.0, 1.0, 1.0])  # reflect across the mat's centreline (x -> -x)


def target_eye(cam: CameraConfig | None, mat: MatConfig, name: str) -> np.ndarray:
    """The locked target if there is one, else the mat.yaml default."""
    if cam is not None and cam.target_eye is not None:
        return np.asarray(cam.target_eye, float)
    return np.asarray(mat.camera_targets[name], float)


def target_aim(cam: CameraConfig | None, mat: MatConfig) -> np.ndarray:
    """The point the camera looked at when its spot was locked, else the mat's aim point."""
    if cam is not None and cam.target_aim is not None:
        return np.asarray(cam.target_aim, float)
    return np.asarray(mat.aim_point, float)


def lock_in_place(R, t, aim_point) -> tuple[np.ndarray, np.ndarray]:
    """Lock a camera's current spot and view: (eye, a point straight ahead at the aim point's distance)."""
    c = cam_center(R, t)
    return c, c + np.asarray(R, float)[2] * np.linalg.norm(np.asarray(aim_point, float) - c)


def targets_stale(centers: dict[str, np.ndarray], targets: dict[str, np.ndarray], max_m: float = STALE_TARGET_M) -> bool:
    """True when a camera is far from its locked spot: the spots were saved for another setup."""
    return any(np.linalg.norm(np.asarray(centers[n]) - np.asarray(targets[n])) > max_m
               for n in centers if n in targets)


def fit_report(K, R, t, image_size, mat: MatConfig, height_m: float = PERSON_HEIGHT_M, margin_px: float = 5.0) -> list[str]:
    """What a person standing anywhere on the mat would lose off the edges of the picture ([] = fits)."""
    base = np.array([[x, 0.0, z] for x, z in mat.mat_polygon()])
    box = np.vstack([base, base + [0.0, height_m, 0.0]])
    if np.any((box @ np.asarray(R).T + np.ravel(t))[:, 2] <= 0.05):
        return ["the mat is not in front of the camera"]
    uv = project(box, K, None, R, t)
    w, h = image_size
    top, feet = uv[4:, 1].min(), uv[:4, 1].max()
    msgs = []
    if top < margin_px and feet > h - margin_px:
        return ["head and feet cut off: move the camera back"]
    if top < margin_px:
        msgs.append("head cut off: tilt up or move back")
    if feet > h - margin_px:
        msgs.append("feet cut off: tilt down or move back")
    if uv[:, 0].min() < margin_px:
        msgs.append("left of the mat cut off: turn left or move back")
    if uv[:, 0].max() > w - margin_px:
        msgs.append("right of the mat cut off: turn right or move back")
    return msgs


def draw_person_box(tile, scale, K, R, t, mat: MatConfig, ok: bool, height_m: float = PERSON_HEIGHT_M) -> None:
    """Wireframe of the space above the mat a standing person fills: green if it fits the picture, red if not."""
    base = np.array([[x, 0.0, z] for x, z in mat.mat_polygon()])
    box = np.vstack([base, base + [0.0, height_m, 0.0]])
    if np.any((box @ np.asarray(R).T + np.ravel(t))[:, 2] <= 0.05):
        return
    uv = project(box, K, None, R, t) * scale
    color = GREEN if ok else RED
    for i in range(4):
        for a, b in ((i, (i + 1) % 4), (i + 4, (i + 1) % 4 + 4), (i, i + 4)):
            cv2.line(tile, pt(uv[a]), pt(uv[b]), color, 1)


_POSITION_STEPS = (("dx_cm", "Move right", "Move left", "right", "left"),
                   ("dist_cm", "Move closer", "Move back", "closer", "back"),
                   ("dy_cm", "Raise", "Lower", "up", "down"))
_AIM_STEPS = (("yaw_deg", "Turn right", "Turn left", "turn_right", "turn_left"),
              ("pitch_deg", "Tilt up", "Tilt down", "tilt_up", "tilt_down"),
              ("roll_deg", "Rotate clockwise", "Rotate counter-clockwise", "cw", "ccw"))


def next_step(hint: dict) -> tuple[str | None, str | None]:
    """The single biggest correction, position before aim: (text, arrow name), or (None, None) when done."""
    if not hint["position_ok"]:
        key, pos, neg, a_pos, a_neg = max(_POSITION_STEPS, key=lambda s: abs(hint[s[0]]))
        v = hint[key]
        return f"{pos if v > 0 else neg} {abs(v):.0f} cm", a_pos if v > 0 else a_neg
    if not hint["aim_ok"]:
        key, pos, neg, a_pos, a_neg = max(_AIM_STEPS, key=lambda s: abs(hint[s[0]]))
        v = hint[key]
        return f"{pos if v > 0 else neg} {abs(v):.1f}°", a_pos if v > 0 else a_neg
    return None, None


def swap_warning(layouts: dict[str, dict]) -> str | None:
    if "A" in layouts and "B" in layouts and layouts["A"]["bearing_deg"] < 0 < layouts["B"]["bearing_deg"]:
        return "A and B look swapped: run python -m vision.cams"
    return None


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


def draw_overlay(tile, scale, K, R, t, mat: MatConfig, eye, hint: dict | None, aim=None) -> None:
    """Crosshair at the image centre, aim dot, ghost mat (from the locked spot), and the instruction."""
    aim = mat.aim_point if aim is None else aim
    for poly in ghost_polylines(mat, K, eye, aim):
        _dashed(tile, poly * scale, GREEN)
    c = np.asarray(K, float)[:2, 2] * scale
    cv2.line(tile, pt(c - [14, 0]), pt(c + [14, 0]), WHITE, 1)
    cv2.line(tile, pt(c - [0, 14]), pt(c + [0, 14]), WHITE, 1)
    if R is not None:
        aim_px = project(aim, K, None, R, t)[0] * scale
        if np.all(np.isfinite(aim_px)):
            cv2.circle(tile, pt(aim_px), 6, RED, 2)
    if hint is not None:
        text, arrow = next_step(hint)
        if text is None:
            draw_text(tile, "In place", (8, tile.shape[0] - 16), GREEN, 0.9)
        else:
            draw_text(tile, text, (8, tile.shape[0] - 16), YELLOW, 0.9)
            draw_arrow(tile, arrow)


_ARROWS = {"left": ((0.62, 0.5), (0.38, 0.5)), "right": ((0.38, 0.5), (0.62, 0.5)),
           "up": ((0.5, 0.62), (0.5, 0.38)), "down": ((0.5, 0.38), (0.5, 0.62)),
           "tilt_up": ((0.5, 0.62), (0.5, 0.38)), "tilt_down": ((0.5, 0.38), (0.5, 0.62)),
           "turn_left": ((0.62, 0.5), (0.38, 0.5)), "turn_right": ((0.38, 0.5), (0.62, 0.5))}


def draw_arrow(tile, arrow: str) -> None:
    """A big arrow in the middle of the camera picture for the next correction."""
    h, w = tile.shape[:2]
    if arrow in _ARROWS:
        (x0, y0), (x1, y1) = _ARROWS[arrow]
        cv2.arrowedLine(tile, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), YELLOW, 6, tipLength=0.3)
    elif arrow in ("closer", "back"):  # toward / away from the mat: a growing or shrinking pair of boxes
        c = np.array([w / 2, h / 2])
        for r in ((40, 70) if arrow == "closer" else (70, 40)):
            cv2.rectangle(tile, pt(c - r), pt(c + r), YELLOW, 3)
        draw_text(tile, "closer" if arrow == "closer" else "back", pt(c + [-30, 95]), YELLOW, 0.7)
    elif arrow in ("cw", "ccw"):
        c = (w // 2, h // 2)
        cv2.ellipse(tile, c, (60, 60), 0, 200, 340, YELLOW, 4)
        end = (c[0] + 56, c[1] - 22) if arrow == "cw" else (c[0] - 56, c[1] - 22)
        start = (end[0] - 10, end[1] - 12) if arrow == "cw" else (end[0] + 10, end[1] - 12)
        cv2.arrowedLine(tile, start, end, YELLOW, 4, tipLength=1.0)


def render_map(mat: MatConfig, cams: dict[str, dict], size=(640, 360)) -> np.ndarray:
    """Bird's-eye map, Mira at the top, +x (patient's left, camera A's side) drawn left.

    cams: name -> {"R", "t", "K", "size", "target" (locked spot or None), "suggested" (default spot, optional)}.
    Each camera is a coloured dot with its field of view; its locked spot is a ring joined by a line.
    """
    from vision.debug_view import PlaneMap

    w, h = size
    img = np.full((h, w, 3), 32, np.uint8)
    view = PlaneMap((-5.5, 5.5), (-2.5, 6.0), flip_u=True, size=size)
    for x in range(-5, 6):
        cv2.line(img, pt(view([[x, -2.5]])[0]), pt(view([[x, 6.0]])[0]), (55, 55, 55), 1)
    for z in range(-2, 7):
        cv2.line(img, pt(view([[-5.5, z]])[0]), pt(view([[5.5, z]])[0]), (55, 55, 55), 1)
    cv2.fillPoly(img, [view(mat.mat_polygon()).astype(np.int32)], (110, 60, 90))
    for i, corners in mat.marker_corners().items():
        c = view(corners[:, [0, 2]].mean(axis=0, keepdims=True))[0]
        cv2.rectangle(img, pt(c - 5), pt(c + 5), (230, 230, 230), -1)
        draw_text(img, str(i), pt(c + [7, 4]), WHITE, 0.4)
    draw_text(img, "Mira this way", (w // 2 - 50, 16), GREEN, 0.5)
    draw_text(img, "1 m grid", (8, h - 8), GREY, 0.4)
    for name, v in sorted(cams.items()):
        color = CAM_COLORS.get(name, WHITE)
        if v.get("suggested") is not None:
            s_ = view([np.asarray(v["suggested"])[[0, 2]]])[0]
            cv2.circle(img, pt(s_), 9, GREY, 1)
            draw_text(img, f"{name} suggested", pt(s_ + [10, 14]), GREY, 0.35)
        if v.get("R") is None:
            continue
        R, t, K = np.asarray(v["R"]), np.asarray(v["t"]), np.asarray(v["K"])
        c3 = cam_center(R, t)
        c = view([c3[[0, 2]]])[0]
        half = np.arctan(v["size"][0] / 2 / K[0, 0])
        fwd = R[2][[0, 2]] / (np.linalg.norm(R[2][[0, 2]]) or 1.0)
        rays = []
        for a in (-half, half):
            ca, sa = np.cos(a), np.sin(a)
            d = np.array([ca * fwd[0] - sa * fwd[1], sa * fwd[0] + ca * fwd[1]])
            rays.append(view([c3[[0, 2]] + 8.0 * d])[0])
        wedge = img.copy()
        cv2.fillPoly(wedge, [np.array([c, rays[0], rays[1]]).astype(np.int32)], color)
        cv2.addWeighted(wedge, 0.18, img, 0.82, 0, img)
        if v.get("target") is not None:
            tg = view([np.asarray(v["target"])[[0, 2]]])[0]
            cv2.line(img, pt(c), pt(tg), color, 1)
            cv2.circle(img, pt(tg), 9, color, 2)
        cv2.circle(img, pt(c), 7, color, -1)
        draw_text(img, f"{name}  {np.hypot(c3[0], c3[2]):.1f} m, {c3[1]:.2f} m high", pt(c + [10, -8]), color, 0.45)
    return img
