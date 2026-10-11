"""Low-poly 3D person for the debug view, software-rendered with OpenCV.

The body is rebuilt every frame from the 33 mat-frame joints: a faceted head, a box torso, a neck,
six-sided tapered limbs, block hands and wedge feet. The person's left is tinted orange and right
blue (the skeleton colours), so a left/right flip is obvious. Faces are flat shaded, back faces are
culled and the rest are painted far to near. The floor grid, mat, markers and the body's shadow are
drawn underneath so foot contact can be judged. Orbit with the mouse or the keys in `legend_lines`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from vision.config import MatConfig
from vision.draw import GREEN, RED, WHITE, draw_text
from vision.frames import look_at_pose
from vision.rig import BodyRig

SIZE = (640, 360)
HFOV_DEG = 60.0
LEFT_TINT = (40, 140, 240)  # BGR orange: the person's left
RIGHT_TINT = (235, 170, 40)  # BGR blue: the person's right
BODY_TINT = (170, 170, 180)
SKIN_TINT = (150, 175, 205)
EDGE = (40, 40, 40)
LIGHT = np.array([-0.3, 1.0, 0.6]) / np.linalg.norm([-0.3, 1.0, 0.6])
BG = (32, 32, 32)
FLOOR_GRID = (60, 60, 60)
MAT_FILL = (110, 60, 90)
SHADOW_Y = 0.001

# name, joint a, joint b, radius at a, radius at b, sides, tint
LIMBS = [
    ("upper_arm_l", 11, 13, 0.050, 0.040, 6, LEFT_TINT), ("upper_arm_r", 12, 14, 0.050, 0.040, 6, RIGHT_TINT),
    ("forearm_l", 13, 15, 0.040, 0.030, 6, LEFT_TINT), ("forearm_r", 14, 16, 0.040, 0.030, 6, RIGHT_TINT),
    ("thigh_l", 23, 25, 0.075, 0.055, 6, LEFT_TINT), ("thigh_r", 24, 26, 0.075, 0.055, 6, RIGHT_TINT),
    ("shin_l", 25, 27, 0.055, 0.035, 6, LEFT_TINT), ("shin_r", 26, 28, 0.055, 0.035, 6, RIGHT_TINT),
    ("foot_l", 29, 31, 0.035, 0.025, 4, LEFT_TINT), ("foot_r", 30, 32, 0.035, 0.025, 4, RIGHT_TINT),
]
HANDS = [("hand_l", 15, (17, 19), LEFT_TINT), ("hand_r", 16, (18, 20), RIGHT_TINT)]


@dataclass
class Part:
    name: str
    faces: list[np.ndarray]  # each (k, 3) mat-frame vertices of one convex polygon
    tint: tuple
    outline: tuple | None = None  # edge colour override (e.g. white for an anchored foot)
    centre: np.ndarray = field(default=None)

    def __post_init__(self):
        if self.centre is None:
            self.centre = np.mean(np.vstack(self.faces), axis=0)


def _ok(*pts) -> bool:
    return all(np.isfinite(p).all() for p in pts)


def _prism(a, b, ra, rb, sides) -> list[np.ndarray]:
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-6:
        return []
    d = d / n
    helper = np.array([0.0, 1.0, 0.0]) if abs(d[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, helper)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring_a = [a + ra * (np.cos(t) * u + np.sin(t) * v) for t in ang]
    ring_b = [b + rb * (np.cos(t) * u + np.sin(t) * v) for t in ang]
    faces = [np.array([ring_a[k], ring_a[(k + 1) % sides], ring_b[(k + 1) % sides], ring_b[k]]) for k in range(sides)]
    return faces + [np.array(ring_a[::-1]), np.array(ring_b)]


def _icosphere(c, r) -> list[np.ndarray]:
    p = (1 + 5 ** 0.5) / 2
    v = np.array([[-1, p, 0], [1, p, 0], [-1, -p, 0], [1, -p, 0], [0, -1, p], [0, 1, p],
                  [0, -1, -p], [0, 1, -p], [p, 0, -1], [p, 0, 1], [-p, 0, -1], [-p, 0, 1]], float)
    v = v / np.linalg.norm(v, axis=1, keepdims=True) * r + c
    tris = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
            (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
            (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    return [v[list(t)] for t in tris]


def build_mesh(joints, feet_locked: dict[str, bool] | None = None) -> list[Part]:
    """Low-poly body parts from (33, 3) mat-frame joints; parts with a missing joint are left out."""
    J = np.asarray(joints, float)
    parts: list[Part] = []
    sl, sr, hl, hr = J[11], J[12], J[23], J[24]
    shoulders = (sl + sr) / 2 if _ok(sl, sr) else None
    if _ok(sl, sr, hl, hr):
        hips = (hl + hr) / 2
        fwd = np.cross(hl - hr, shoulders - hips)  # left x up = forward
        if np.linalg.norm(fwd) > 1e-6:
            fwd = fwd / np.linalg.norm(fwd)
            corners = {"sl": (sl, 0.09), "sr": (sr, 0.09), "hl": (hl, 0.08), "hr": (hr, 0.08)}  # half depth, m
            v = {k: p + fwd * dz for k, (p, dz) in corners.items()}  # front of the torso
            b = {k: p - fwd * dz for k, (p, dz) in corners.items()}  # back
            faces = [np.array([v["sl"], v["sr"], v["hr"], v["hl"]]), np.array([b["sl"], b["hl"], b["hr"], b["sr"]]),
                     np.array([v["sl"], v["hl"], b["hl"], b["sl"]]), np.array([v["sr"], b["sr"], b["hr"], v["hr"]]),
                     np.array([v["sl"], b["sl"], b["sr"], v["sr"]]), np.array([v["hl"], v["hr"], b["hr"], b["hl"]])]
            parts.append(Part("torso", faces, BODY_TINT))
    ears = [J[7], J[8]]
    head = (ears[0] + ears[1]) / 2 if _ok(*ears) else (J[0] if _ok(J[0]) else None)
    if head is not None:
        r = float(np.clip(0.6 * np.linalg.norm(ears[0] - ears[1]), 0.08, 0.13)) if _ok(*ears) else 0.1
        parts.append(Part("head", _icosphere(head, r), SKIN_TINT))
        if shoulders is not None:
            neck = _prism(shoulders, head - np.array([0.0, 0.7 * r, 0.0]), 0.05, 0.045, 6)
            if neck:
                parts.append(Part("neck", neck, SKIN_TINT))
    for name, a, b, ra, rb, sides, tint in LIMBS:
        if _ok(J[a], J[b]):
            faces = _prism(J[a], J[b], ra, rb, sides)
            if faces:
                side = name[-1]
                outline = WHITE if name.startswith("foot") and (feet_locked or {}).get(side) else None
                parts.append(Part(name, faces, tint, outline))
    for name, wrist, tips, tint in HANDS:
        pts = [J[k] for k in tips if _ok(J[k])]
        if _ok(J[wrist]) and pts:
            faces = _prism(J[wrist], np.mean(pts, axis=0), 0.03, 0.025, 4)
            if faces:
                parts.append(Part(name, faces, tint))
    return parts


def shadow_points(parts: list[Part]) -> np.ndarray:
    """Every body vertex dropped onto the floor (a flat shadow from straight above)."""
    if not parts:
        return np.empty((0, 3))
    pts = np.vstack([np.vstack(p.faces) for p in parts]).copy()
    pts[:, 1] = SHADOW_Y
    return pts


@dataclass
class AvatarView:
    """Orbit camera around a point above the mat centre. yaw 0 = in front of the person (toward Mira)."""

    yaw_deg: float = -35.0  # negative: toward the person's right (-x), i.e. front-right
    pitch_deg: float = 15.0
    dist_m: float = 4.6
    target: tuple = (0.0, 0.95, 0.0)
    show_3d: bool = True
    rig: BodyRig = field(default_factory=BodyRig)  # fixed body proportions for the drawn person
    _defaults: tuple = field(default=None, repr=False)

    def __post_init__(self):
        self._defaults = (self.yaw_deg, self.pitch_deg, self.dist_m)

    def eye(self) -> np.ndarray:
        y, p = np.radians(self.yaw_deg), np.radians(self.pitch_deg)
        return np.asarray(self.target) + self.dist_m * np.array([np.sin(y) * np.cos(p), np.sin(p), np.cos(y) * np.cos(p)])

    def pose(self):
        return look_at_pose(self.eye(), np.asarray(self.target, float))

    def K(self, size=SIZE) -> np.ndarray:
        w, h = size
        f = w / 2 / np.tan(np.radians(HFOV_DEG) / 2)
        return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])

    def _clamp(self):
        self.pitch_deg = float(np.clip(self.pitch_deg, -5.0, 85.0))
        self.dist_m = float(np.clip(self.dist_m, 1.5, 12.0))

    def drag(self, dx: float, dy: float) -> None:
        self.yaw_deg -= 0.4 * dx
        self.pitch_deg += 0.3 * dy
        self._clamp()

    def handle_key(self, key: int) -> bool:
        """Apply a view key; False if the key isn't one of ours."""
        c = chr(key) if 0 <= key < 256 else ""
        if c == "j":
            self.yaw_deg -= 10
        elif c == "l":
            self.yaw_deg += 10
        elif c == "i":
            self.pitch_deg += 5
        elif c == "k":
            self.pitch_deg -= 5
        elif c in "+=":
            self.dist_m /= 1.15
        elif c in "-_":
            self.dist_m *= 1.15
        elif c == "0":
            self.yaw_deg, self.pitch_deg, self.dist_m = self._defaults
        elif c == "v":
            self.show_3d = not self.show_3d
        elif c == "b":
            self.rig.reset()
        else:
            return False
        self._clamp()
        return True


def legend_lines() -> list[str]:
    return ["b: re-measure body   v: 3D <-> front view",
            "drag / j/l: orbit   i/k: tilt   +/-: zoom   0: reset view"]


def _project(X, K, R, t):
    Xc = np.asarray(X, float) @ R.T + t
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = Xc[:, :2] / Xc[:, 2:3] @ K[:2, :2].T + K[:2, 2]
    return uv, Xc[:, 2]


def _draw_floor(img, mat: MatConfig, K, R, t) -> None:
    def line(a, b, color, w=1):
        (pa, pb), z = _project(np.array([a, b]), K, R, t)
        if (z > 0.1).all():
            cv2.line(img, tuple(np.round(pa).astype(int)), tuple(np.round(pb).astype(int)), color, w, cv2.LINE_AA)

    for x in np.arange(-2.0, 2.01, 0.5):
        line([x, 0, -2.0], [x, 0, 2.5], FLOOR_GRID)
    for z in np.arange(-2.0, 2.51, 0.5):
        line([-2.0, 0, z], [2.0, 0, z], FLOOR_GRID)
    poly = np.array([[x, 0.0, z] for x, z in mat.mat_polygon()])
    uv, z = _project(poly, K, R, t)
    if (z > 0.1).all():
        cv2.fillConvexPoly(img, np.round(uv).astype(np.int32), MAT_FILL, cv2.LINE_AA)
    for corners in mat.marker_corners().values():
        uv, z = _project(corners + [0, 0.0005, 0], K, R, t)
        if (z > 0.1).all():
            cv2.fillConvexPoly(img, np.round(uv).astype(np.int32), (225, 225, 225), cv2.LINE_AA)
    uv, z = _project(np.array([[0.0, 0.0, mat.length_m / 2 + 0.5]]), K, R, t)
    if z[0] > 0.1:
        draw_text(img, "Mira", tuple(np.round(uv[0]).astype(int)), GREEN, 0.45)


def render_avatar(joints, mat: MatConfig, view: AvatarView, size=SIZE, feet_locked=None,
                  hud: list[str] | None = None) -> np.ndarray:
    w, h = size
    img = np.full((h, w, 3), BG, np.uint8)
    K, (R, t) = view.K(size), view.pose()
    _draw_floor(img, mat, K, R, t)
    parts = build_mesh(joints, feet_locked) if joints is not None else []
    if parts:
        uv, z = _project(shadow_points(parts), K, R, t)
        ok = z > 0.1
        if ok.sum() >= 3:
            hull = cv2.convexHull(np.round(uv[ok]).astype(np.int32))
            shade = img.copy()
            cv2.fillConvexPoly(shade, hull, (15, 15, 15), cv2.LINE_AA)
            cv2.addWeighted(shade, 0.55, img, 0.45, 0, img)
        eye = view.eye()
        polys = []
        for part in parts:
            for face in part.faces:
                c = face.mean(axis=0)
                n = np.cross(face[1] - face[0], face[2] - face[0])
                if np.linalg.norm(n) < 1e-12:
                    continue
                n = n / np.linalg.norm(n)
                if n @ (c - part.centre) < 0:  # make the normal point out of the part
                    n = -n
                if n @ (eye - c) <= 0:  # facing away from the viewer
                    continue
                fuv, fz = _project(face, K, R, t)
                if (fz <= 0.1).any():
                    continue
                shade = 0.35 + 0.65 * max(0.0, float(n @ LIGHT))
                color = tuple(int(min(255, ch * shade)) for ch in part.tint)
                polys.append((float(fz.mean()), np.round(fuv).astype(np.int32), color, part.outline or EDGE))
        for _, poly, color, edge in sorted(polys, key=lambda p: -p[0]):  # far to near
            cv2.fillConvexPoly(img, poly, color, cv2.LINE_AA)
            cv2.polylines(img, [poly], True, edge, 1, cv2.LINE_AA)
    for k, line in enumerate(hud or []):
        draw_text(img, line, (8, 20 + 18 * k), RED if line.startswith("REC") else WHITE, 0.45)
    for k, line in enumerate(legend_lines()):
        draw_text(img, line, (8, h - 10 - 18 * k), WHITE, 0.42)
    return img
