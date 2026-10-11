"""Soft form mannequin, software-rendered with OpenCV from the existing body rig.

Rounded sage limbs and torso, neutral oval head, and optional left/right accents.
Geometry is visual only: tracked joints, body constraints and rep counting are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

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
BODY_TINT = (195, 212, 199)
SKIN_TINT = (199, 216, 226)
LIGHT = np.array([-0.3, 1.0, 0.6]) / np.linalg.norm([-0.3, 1.0, 0.6])
BG = (34, 37, 32)
FLOOR_GRID = (60, 60, 60)
MAT_FILL = (60, 72, 51)
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


@lru_cache(maxsize=8)
def _sphere_faces(lat=6, lon=10):
    """Cached unit topology, with non-degenerate triangles at the poles."""
    rings = []
    for y in range(1, lat):
        p = y * np.pi / lat
        rings.append(np.array([[np.sin(p)*np.cos(t), np.cos(p), np.sin(p)*np.sin(t)]
                               for t in np.arange(lon)*2*np.pi/lon]))
    faces = []
    for i in range(lon):
        k = (i + 1) % lon
        faces.append(np.array([[0, 1, 0], rings[0][i], rings[0][k]]))
        for a, b in zip(rings, rings[1:]):
            faces.append(np.array([a[i], b[i], b[k], a[k]]))
        faces.append(np.array([rings[-1][i], [0, -1, 0], rings[-1][k]]))
    return tuple(faces)


def _ellipsoid(center, radii, basis=None, lat=6, lon=10):
    basis = np.eye(3) if basis is None else basis
    return [(f * radii) @ basis.T + center for f in _sphere_faces(lat, lon)]


def _axis_basis(a, b):
    up = b - a
    length = np.linalg.norm(up)
    if length < 1e-6:
        return None
    up = up / length
    helper = np.array([0., 1., 0.]) if abs(up[1]) < .9 else np.array([1., 0., 0.])
    side = np.cross(up, helper)
    side /= np.linalg.norm(side)
    return np.column_stack((side, up, np.cross(side, up)))


def build_mesh(joints, feet_locked: dict[str, bool] | None = None,
               side_accents: bool = False) -> list[Part]:
    """Rounded body from (33, 3) joints; omit only parts with missing endpoints."""
    J = np.asarray(joints, float)
    parts: list[Part] = []
    sl, sr, hl, hr = J[11], J[12], J[23], J[24]
    shoulders = (sl + sr) / 2 if _ok(sl, sr) else None
    basis = np.eye(3)
    if _ok(sl, sr, hl, hr):
        hips = (hl + hr) / 2
        up = shoulders - hips
        forward = np.cross(sl - sr, up)
        if np.linalg.norm(forward) > 1e-6:
            up /= np.linalg.norm(up)
            forward /= np.linalg.norm(forward)
            side = np.cross(up, forward)
            basis = np.column_stack((side, up, forward))
            sw = np.linalg.norm(sl - sr) / 2
            hw = np.linalg.norm(hl - hr) / 2 + .025
            levels = [(0, hw, .09), (.18, hw*1.06, .10),
                      (.48, sw*.78, .103), (.80, sw*1.02, .112), (1, sw*.88, .09)]
            angles = np.arange(16)*2*np.pi/16
            rings = [hips + (shoulders-hips)*t + np.cos(angles)[:, None]*w*side
                     + np.sin(angles)[:, None]*d*forward for t, w, d in levels]
            faces = [np.array([a[i], a[(i+1)%16], b[(i+1)%16], b[i]])
                     for a, b in zip(rings, rings[1:]) for i in range(16)]
            faces += [rings[0][::-1], rings[-1]]
            parts.append(Part("torso", faces, BODY_TINT))
            parts.append(Part("pelvis", _ellipsoid(hips-.025*up, [hw+.018, .10, .10], basis), BODY_TINT))
    ears = [J[7], J[8]]
    head = (ears[0] + ears[1]) / 2 if _ok(*ears) else (J[0] if _ok(J[0]) else None)
    if head is not None:
        r = float(np.clip(.6*np.linalg.norm(ears[0]-ears[1]), .08, .13)) if _ok(*ears) else .1
        parts.append(Part("head", _ellipsoid(head, [r, r*1.30, r*1.02], basis, 8, 12), SKIN_TINT))
        if shoulders is not None:
            neck = _prism(shoulders, head-basis[:, 1]*r*.7, .039, .039, 12)
            if neck:
                parts.append(Part("neck", neck, BODY_TINT))
    for name, a, b, ra, rb, _, tint in LIMBS:
        if not _ok(J[a], J[b]):
            continue
        tint = tint if side_accents else BODY_TINT
        if name.startswith("foot"):
            # The long axis follows heel-to-toe; the rig still owns foot contact.
            fb = _axis_basis(J[a], J[b])
            if fb is None:
                continue
            length = np.linalg.norm(J[b]-J[a])
            faces = _ellipsoid((J[a]+J[b])/2, [.045, length/2+.018, .028], fb)
            outline = WHITE if (feet_locked or {}).get(name[-1]) else None
            parts.append(Part(name, faces, tint, outline))
        else:
            faces = _prism(J[a], J[b], ra, rb, 12)
            if faces:
                parts.append(Part(name, faces, tint))
    for offset, side in [(0, "l"), (1, "r")]:
        tint = (LEFT_TINT if side == "l" else RIGHT_TINT) if side_accents else BODY_TINT
        for joint, radius in [(11, .052), (13, .04), (15, .03), (23, .075), (25, .055), (27, .035)]:
            if _ok(J[joint+offset]):
                parts.append(Part(f"joint_{joint}_{side}", _ellipsoid(J[joint+offset], [radius]*3), tint))
    for name, wrist, tips, tint in HANDS:
        pts = [J[k] for k in tips if _ok(J[k])]
        if _ok(J[wrist]) and pts:
            end = np.mean(pts, axis=0)
            hb = _axis_basis(J[wrist], end)
            if hb is not None:
                faces = _ellipsoid((J[wrist]+end)/2, [.028, np.linalg.norm(end-J[wrist])/2+.01, .022], hb)
                parts.append(Part(name, faces, tint if side_accents else BODY_TINT))
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
    side_accents: bool = False
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
        elif c == "a":
            self.side_accents = not self.side_accents
        elif c == "b":
            self.rig.reset()
        elif c == "c":
            self.rig.constraints = not self.rig.constraints
        else:
            return False
        self._clamp()
        return True


def legend_lines() -> list[str]:
    return ["b: re-measure body   c: body rules on/off   v: 3D <-> front view",
            "drag / j/l: orbit   i/k: tilt   +/-: zoom   0: reset view   a: side colors"]


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
    parts = build_mesh(joints, feet_locked, view.side_accents) if joints is not None else []
    if parts:
        uv, z = _project(shadow_points(parts), K, R, t)
        ok = z > 0.1
        if ok.sum() >= 3:
            hull = cv2.convexHull(np.round(uv[ok]).astype(np.int32))
            shade = img.copy()
            cv2.fillConvexPoly(shade, hull, (15, 15, 15), cv2.LINE_AA)
            cv2.addWeighted(shade, 0.55, img, 0.45, 0, img)
        # Batch normals and projection so the rounder mesh stays inexpensive live.
        faces = [face for part in parts for face in part.faces]
        centers = np.array([face.mean(axis=0) for face in faces])
        origins = np.array([part.centre for part in parts for _ in part.faces])
        normals = np.cross(np.array([f[1]-f[0] for f in faces]),
                           np.array([f[2]-f[0] for f in faces]))
        lengths = np.linalg.norm(normals, axis=1)
        normals /= np.maximum(lengths[:, None], 1e-12)
        normals[np.einsum("ij,ij->i", normals, centers-origins) < 0] *= -1
        visible = (lengths > 1e-12) & (np.einsum("ij,ij->i", normals, view.eye()-centers) > 0)
        colors = np.array([part.tint for part in parts for _ in part.faces])
        colors = (colors * (.55+.45*np.maximum(0, normals @ LIGHT))[:, None]).astype(int)
        offsets = np.concatenate(([0], np.cumsum([len(f) for f in faces])))
        uv, depths = _project(np.vstack(faces), K, R, t)
        pixels = np.round(uv).astype(np.int32)
        polys = []
        for i in np.flatnonzero(visible):
            lo, hi = offsets[i:i+2]
            if (depths[lo:hi] > .1).all():
                polys.append((float(depths[lo:hi].mean()), i, pixels[lo:hi]))
        for _, i, poly in sorted(polys, key=lambda p: -p[0]):
            cv2.fillConvexPoly(img, poly, tuple(map(int, colors[i])), cv2.LINE_AA)
        # A single foot silhouette preserves the contact cue without wireframe facets.
        for part in parts:
            if part.outline is not None:
                uv, z = _project(np.vstack(part.faces), K, R, t)
                if (z > .1).all():
                    hull = cv2.convexHull(np.round(uv).astype(np.int32))
                    cv2.polylines(img, [hull], True, part.outline, 1, cv2.LINE_AA)
    for k, line in enumerate(hud or []):
        draw_text(img, line, (8, 20 + 18 * k), RED if line.startswith("REC") else WHITE, 0.45)
    for k, line in enumerate(legend_lines()):
        draw_text(img, line, (8, h - 10 - 18 * k), WHITE, 0.42)
    return img
