"""Model-based tracker: fit a body with measured proportions to what both cameras see.

Each frame, a kinematic skeleton (pelvis position and rotation, spine, neck, shoulder and hip ball
joints, elbow and knee hinges, foot direction: 32 numbers) is posed to minimise the mismatch between
its projected landmarks and both cameras' MediaPipe 2D points, weighted by visibility and per-landmark
trust, with a robust loss so far-off points count for less. Soft priors keep it smooth (close to the
previous frame), inside joint ranges, above the floor, and pulled to planted feet. Nothing is thrown
away when the cameras disagree, and one camera alone still gives a full body.

Segment frames: x = the body's left, y = up along the bone's parent-to-child axis (bones point along
-y), z = x × y = forward. Elbows bend the forearm toward +z, knees bend the shin toward -z.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from vision.config import CameraConfig
from vision.filters import OneEuroFilter
from vision.types import PoseResult

FRAMES_NEEDED = 40
FORGET_S = 5.0
FLEX_MAX = np.radians(150.0)

# parameter layout
ROOT, PELVIS, SPINE, NECK = slice(0, 3), slice(3, 6), slice(6, 9), slice(9, 12)
ARM = {"l": (slice(12, 15), 15), "r": (slice(16, 19), 19)}  # shoulder rotvec, elbow flexion
LEG = {"l": (slice(20, 23), 23, slice(24, 26)), "r": (slice(26, 29), 29, slice(30, 32))}  # hip, knee, foot
N_PARAMS = 32
SIDE = {"l": dict(sh=11, el=13, wr=15, hand=(17, 19, 21), hip=23, kn=25, an=27, he=29, to=31, sign=1.0),
        "r": dict(sh=12, el=14, wr=16, hand=(18, 20, 22), hip=24, kn=26, an=28, he=30, to=32, sign=-1.0)}
FACE = list(range(11))
NEEDED = [7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32]

# per-landmark trust: semantic points whose definition varies with viewpoint count for less
TRUST = np.ones(33)
TRUST[FACE] = 0.3
TRUST[[11, 12]] = 0.6
MIN_VIS = 0.3
ROBUST_PX = 10.0
TEMPORAL_PX = 3.0  # cost of moving one "sigma" away from the previous frame, in pixels
SIGMA_POS_M, SIGMA_ROT = 0.04, 0.25
LIMIT_PX_PER_RAD = 200.0
FLOOR_PX_PER_M = 400.0
ANCHOR_PX_PER_M = 200.0
WORLD_PX_PER_M = 150.0  # one camera: pull toward MediaPipe's own 3D shape (the depth cue), px per metre
CONTACT_PX_PER_M = 300.0  # one camera: the lowest foot point is pulled onto the floor


def _unit(v, fallback=None):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else fallback


def _rx(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rv(v) -> np.ndarray:
    v = np.asarray(v, float)
    t = float(np.linalg.norm(v))
    if t < 1e-12:
        return np.eye(3)
    k = v / t
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(t) * K + (1 - np.cos(t)) * (K @ K)


def _frame(left, up) -> np.ndarray | None:
    """Rotation whose columns are (left, up, forward) with up exact and left made perpendicular."""
    y = _unit(np.asarray(up, float))
    if y is None:
        return None
    x = _unit(np.asarray(left, float) - (np.asarray(left, float) @ y) * y)
    if x is None:
        return None
    return np.column_stack([x, y, np.cross(x, y)])


def _bone_frame(bone_dir, bend_dir, fallback_z) -> np.ndarray:
    """Frame for a bone pointing along bone_dir (= -y), with z toward bend_dir (perpendicular part)."""
    y = -bone_dir
    z = _unit(bend_dir - (bend_dir @ y) * y) if bend_dir is not None else None
    if z is None:
        z = _unit(fallback_z - (fallback_z @ y) * y, _unit(np.cross(y, [1.0, 0.0, 0.0])))
    return np.column_stack([np.cross(y, z), y, z])


def _rotvec(R) -> np.ndarray:
    return Rotation.from_matrix(R).as_rotvec()


@dataclass
class BodyShape:
    lengths: dict
    face: np.ndarray  # (11, 3) offsets from the ear midpoint in the head frame
    hand: dict  # side -> (3, 3) offsets from the wrist in the forearm frame
    foot: dict  # side -> (2, 3) heel, toe offsets from the ankle in the shin frame

    def scaled(self, k: float) -> "BodyShape":
        return BodyShape({n: v * k for n, v in self.lengths.items()}, self.face * k,
                         {s: v * k for s, v in self.hand.items()}, {s: v * k for s, v in self.foot.items()})


def segment_frames(J: np.ndarray) -> dict | None:
    """Rotation of each segment for joints J (None if a needed joint is missing)."""
    if not np.isfinite(J[NEEDED]).all():
        return None
    hip_mid, sh_mid = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2
    spine = sh_mid - hip_mid
    F = {"pelvis": _frame(J[23] - J[24], spine), "chest": _frame(J[11] - J[12], spine),
         "head": _frame(J[7] - J[8], (J[7] + J[8]) / 2 - sh_mid)}
    if any(v is None for v in F.values()):
        return None
    for s, d in SIDE.items():
        u, f = _unit(J[d["el"]] - J[d["sh"]]), _unit(J[d["wr"]] - J[d["el"]])
        t, k = _unit(J[d["kn"]] - J[d["hip"]]), _unit(J[d["an"]] - J[d["kn"]])
        if u is None or f is None or t is None or k is None:
            return None
        F[f"ua_{s}"] = _bone_frame(u, f, F["chest"][:, 2])
        F[f"fa_{s}"] = _bone_frame(f, None, F[f"ua_{s}"][:, 2]) if np.allclose(u, f) else \
            np.column_stack([F[f"ua_{s}"][:, 0], -f, np.cross(F[f"ua_{s}"][:, 0], -f)])
        F[f"th_{s}"] = _bone_frame(t, -k, F["pelvis"][:, 2])
        F[f"sh_{s}"] = np.column_stack([F[f"th_{s}"][:, 0], -k, np.cross(F[f"th_{s}"][:, 0], -k)])
    return F


def measure(frames: list[np.ndarray]) -> BodyShape | None:
    """Bone lengths and rigid offsets (medians over frames with all needed joints)."""
    L = {k: [] for k in ("hip_width", "shoulder_width", "torso", "neck", "upper_arm", "forearm", "thigh", "shin")}
    face, hand, foot = [], {"l": [], "r": []}, {"l": [], "r": []}
    for J in frames:
        F = segment_frames(J)
        if F is None:
            continue
        hip_mid, sh_mid, ears = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2, (J[7] + J[8]) / 2
        L["hip_width"].append(np.linalg.norm(J[23] - J[24]))
        L["shoulder_width"].append(np.linalg.norm(J[11] - J[12]))
        L["torso"].append(np.linalg.norm(sh_mid - hip_mid))
        L["neck"].append(np.linalg.norm(ears - sh_mid))
        for s, d in SIDE.items():
            L["upper_arm"].append(np.linalg.norm(J[d["el"]] - J[d["sh"]]))
            L["forearm"].append(np.linalg.norm(J[d["wr"]] - J[d["el"]]))
            L["thigh"].append(np.linalg.norm(J[d["kn"]] - J[d["hip"]]))
            L["shin"].append(np.linalg.norm(J[d["an"]] - J[d["kn"]]))
            if np.isfinite(J[list(d["hand"])]).all():
                hand[s].append((J[list(d["hand"])] - J[d["wr"]]) @ F[f"fa_{s}"])
            foot[s].append((J[[d["he"], d["to"]]] - J[d["an"]]) @ F[f"sh_{s}"])
        if np.isfinite(J[FACE]).all():
            face.append((J[FACE] - ears) @ F["head"])
    if len(L["torso"]) < 10 or not face:
        return None
    lengths = {k: float(np.median(v)) for k, v in L.items()}
    hand_off = {s: np.median(hand[s], axis=0) if hand[s] else np.zeros((3, 3)) for s in SIDE}
    # left and right mirrored (x flipped) and averaged: a body is close to symmetric
    foot_off = {s: np.median(foot[s], axis=0) for s in SIDE}
    return BodyShape(lengths, np.median(face, axis=0), hand_off, foot_off)


def params_from_joints(J: np.ndarray, shape: BodyShape) -> np.ndarray | None:
    """Parameters that reproduce joints J as closely as the skeleton allows (initialisation)."""
    F = segment_frames(J)
    if F is None:
        return None
    p = np.zeros(N_PARAMS)
    p[ROOT] = (J[23] + J[24]) / 2
    p[PELVIS] = _rotvec(F["pelvis"])
    p[SPINE] = _rotvec(F["pelvis"].T @ F["chest"])
    p[NECK] = _rotvec(F["chest"].T @ F["head"])
    for s, d in SIDE.items():
        sh, el = ARM[s]
        p[sh] = _rotvec(F["chest"].T @ F[f"ua_{s}"])
        u, f = _unit(J[d["el"]] - J[d["sh"]]), _unit(J[d["wr"]] - J[d["el"]])
        p[el] = float(np.arccos(np.clip(u @ f, -1, 1)))
        hip, kn, _ = LEG[s]
        p[hip] = _rotvec(F["pelvis"].T @ F[f"th_{s}"])
        t, k = _unit(J[d["kn"]] - J[d["hip"]]), _unit(J[d["an"]] - J[d["kn"]])
        p[kn] = float(np.arccos(np.clip(t @ k, -1, 1)))
    return p


def forward(p: np.ndarray, shape: BodyShape) -> np.ndarray:
    """All 33 landmarks (mat frame) for parameters p."""
    L = shape.lengths
    X = np.zeros((33, 3))
    root = p[ROOT]
    Rp = _rv(p[PELVIS])
    Rc = Rp @ _rv(p[SPINE])
    Rh = Rc @ _rv(p[NECK])
    X[23], X[24] = root + Rp[:, 0] * L["hip_width"] / 2, root - Rp[:, 0] * L["hip_width"] / 2
    sh_mid = root + Rc[:, 1] * L["torso"]
    X[11], X[12] = sh_mid + Rc[:, 0] * L["shoulder_width"] / 2, sh_mid - Rc[:, 0] * L["shoulder_width"] / 2
    ears = sh_mid + Rh[:, 1] * L["neck"]
    X[FACE] = ears + shape.face @ Rh.T
    for s, d in SIDE.items():
        sh, el = ARM[s]
        Ru = Rc @ _rv(p[sh])
        X[d["el"]] = X[d["sh"]] - Ru[:, 1] * L["upper_arm"]
        Rf = Ru @ _rx(-p[el])
        X[d["wr"]] = X[d["el"]] - Rf[:, 1] * L["forearm"]
        X[list(d["hand"])] = X[d["wr"]] + shape.hand[s] @ Rf.T
        hip, kn, ft = LEG[s]
        Rt = Rp @ _rv(p[hip])
        X[d["kn"]] = X[d["hip"]] - Rt[:, 1] * L["thigh"]
        Rs = Rt @ _rx(p[kn])
        X[d["an"]] = X[d["kn"]] - Rs[:, 1] * L["shin"]
        Rf2 = Rs @ _ry(p[ft][0]) @ _rx(p[ft][1])
        X[[d["he"], d["to"]]] = X[d["an"]] + shape.foot[s] @ Rf2.T
    return X


def _dependency() -> np.ndarray:
    """(33, N_PARAMS) bool: which parameters move which landmark (for the Jacobian's sparsity)."""
    D = np.zeros((33, N_PARAMS), bool)
    trunk = list(range(0, 6))
    D[:, trunk] = True
    for k in FACE + [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]:
        D[k, 6:9] = True
    D[FACE, 9:12] = True
    for s, d in SIDE.items():
        sh, el = ARM[s]
        for k in [d["el"], d["wr"], *d["hand"]]:
            D[k, sh] = True
        for k in [d["wr"], *d["hand"]]:
            D[k, el] = True
        hip, kn, ft = LEG[s]
        for k in [d["kn"], d["an"], d["he"], d["to"]]:
            D[k, hip] = True
        for k in [d["an"], d["he"], d["to"]]:
            D[k, kn] = True
        for k in [d["he"], d["to"]]:
            D[k, ft] = True
    return D


DEPENDS = _dependency()
SIGMA = np.r_[[SIGMA_POS_M] * 3, [SIGMA_ROT] * (N_PARAMS - 3)]
# finite-difference groups: trunk parameters one by one; the i-th parameter of each limb together
# (left arm, right arm, left leg and right leg never move the same landmark)
_LIMBS = [list(range(12, 16)), list(range(16, 20)), list(range(20, 26)), list(range(26, 32))]
FD_GROUPS = [[j] for j in range(12)] + [[limb[i] for limb in _LIMBS if i < len(limb)] for i in range(6)]
ITERATIONS = 3
RECOVER_PX = 20.0  # a fit whose median 2D residual is above this also tries a fresh start from triangulation
SMOOTH_MIN_CUTOFF = 0.7  # Hz: One Euro on the pose numbers (0 = off)
SMOOTH_BETA = 6.0  # two cameras: how much faster it follows when the pose changes quickly
ROOT_MIN_CUTOFF = 3.0  # Hz: two cameras pin the body's position well, so it is smoothed less
SINGLE_SMOOTH_TOO = True  # one camera: One Euro on the pose numbers before the 2D-motion gate
GATE_FULL_PX = 8.0  # one camera: a landmark moving this many px per frame passes its change through fully
GATE_MIN_GAIN = 0.08  # ... a landmark still in 2D lets only this fraction of a depth change through per frame
GATE_GAP_S = 0.2  # ... after a gap longer than this (s) the gate and smoother start afresh
SINGLE_BETA = 2.0  # one camera: noisier depth, so follow speed-ups more cautiously
SINGLE_ROOT_MIN_CUTOFF = 1.0  # one camera: the floor position wobbles ~2 cm, smooth it more


class _Cam:
    def __init__(self, cfg: CameraConfig):
        self.R, self.t, self.K = np.asarray(cfg.R, float), np.ravel(cfg.t).astype(float), np.asarray(cfg.K, float)

    def project(self, X):
        Xc = X @ self.R.T + self.t
        z = np.where(Xc[:, 2] > 1e-3, Xc[:, 2], 1e-3)
        return (Xc[:, :2] / z[:, None]) @ self.K[:2, :2].T + self.K[:2, 2]


def _undistorted(pose: PoseResult, cfg: CameraConfig) -> np.ndarray:
    if cfg.dist is None or not np.any(cfg.dist):
        return pose.lm2d
    import cv2

    return cv2.undistortPoints(pose.lm2d.reshape(-1, 1, 2).astype(np.float64), cfg.K, cfg.dist, P=cfg.K).reshape(-1, 2)


class ModelTracker:
    def __init__(self, cams: dict[str, CameraConfig], frames_needed: int = FRAMES_NEEDED, max_nfev: int = 6,
                 height_m: float | None = None):
        """height_m: the person's height; with one camera it fixes the body's scale (MediaPipe's world
        landmarks are only roughly metric)."""
        self.cams = {n: _Cam(c) for n, c in cams.items() if c is not None and c.calibrated}
        self.cfgs = cams
        self.single = len(self.cams) == 1
        self.height_m = height_m
        self.frames_needed, self.max_nfev = frames_needed, max_nfev
        self.reset()

    def reset(self) -> None:
        self.shape: BodyShape | None = None
        self.world_scale = 1.0  # a new person or session is measured afresh, at MediaPipe's own scale
        self.upright: np.ndarray | None = None  # one camera: turns MediaPipe's leaning world shape upright
        self._samples: list[np.ndarray] = []
        self._p: np.ndarray | None = None
        self._last_seen: float | None = None
        self.last_ms = 0.0
        self.last_cost = np.nan
        self.recoveries = 0
        root, beta = (SINGLE_ROOT_MIN_CUTOFF, SINGLE_BETA) if self.single else (ROOT_MIN_CUTOFF, SMOOTH_BETA)
        cutoff = np.r_[[root] * 3, [SMOOTH_MIN_CUTOFF] * (N_PARAMS - 3)]
        self._smooth = OneEuroFilter(cutoff, beta) if SMOOTH_MIN_CUTOFF else None
        self._gate_uv = self._gate_w = self._gate_out = None
        self._gate_t: float | None = None  # time of the last gated frame

    @property
    def progress(self) -> float:
        return 1.0 if self.shape is not None else min(len(self._samples) / self.frames_needed, 1.0)

    def _residuals(self, p, obs, prev, anchors, world=None):
        X = forward(p, self.shape)
        parts = []
        for cam, uv, w in obs:
            parts.append(((cam.project(X) - uv) * w[:, None]).ravel())
        if world is not None:  # one camera: MediaPipe's 3D shape, relative to the hips, is the depth cue
            W, ww = world
            rel = X - (X[23] + X[24]) / 2
            parts.append((WORLD_PX_PER_M * (rel - W) * ww[:, None]).ravel())
            parts.append(np.array([CONTACT_PX_PER_M * np.min(X[[29, 30, 31, 32], 1])]))
        parts.append((p - prev) / SIGMA * TEMPORAL_PX)
        lim = []
        for s in SIDE:
            for q in (p[ARM[s][1]], p[LEG[s][1]]):
                lim.append(LIMIT_PX_PER_RAD * (min(q, 0.0) + max(q - FLEX_MAX, 0.0)))
        parts.append(np.array(lim))
        parts.append(FLOOR_PX_PER_M * np.minimum(X[[29, 30, 31, 32], 1], 0.0))
        for k, pos in anchors:
            parts.append(ANCHOR_PX_PER_M * (X[k] - pos))
        return np.concatenate(parts)

    def _world(self, views):
        """(hip-relative world shape scaled to the body, weights) from the one camera, or None."""
        if not self.single:
            return None
        from vision.mono import world_shape

        n = next(iter(self.cams))
        v = views.get(n)
        W = world_shape(v, self.cfgs[n]) if v is not None else None
        if W is None:
            return None
        if self.upright is not None:
            W = W @ self.upright.T
        ww = np.where(v.vis >= 0.5, 1.0, 0.2) * np.where(np.isfinite(W).all(axis=1), 1.0, 0.0)
        return np.nan_to_num(W * self.world_scale), ww

    def _median_residual(self, p, obs) -> float:
        """Median 2D distance (px) between the posed model and the detections it is fitted to."""
        X = forward(p, self.shape)
        d = [np.linalg.norm(cam.project(X) - uv, axis=1)[w > 0] for cam, uv, w in obs]
        d = np.concatenate(d) if d else np.empty(0)
        return float(np.median(d)) if d.size else 0.0

    def _solve(self, p0, obs, prev, anchors, S: np.ndarray, world=None) -> tuple[np.ndarray, float]:
        """Robust (soft-L1) Levenberg-Marquardt from p0, Jacobian by grouped finite differences."""
        p = p0.copy()
        lam = 1e-3
        r = self._residuals(p, obs, prev, anchors, world)

        def robust(res):
            z = (res / ROBUST_PX) ** 2
            return np.sum(2 * (np.sqrt(1 + z) - 1)) * ROBUST_PX ** 2 / 2, 1 / np.sqrt(np.sqrt(1 + z))

        cost, sw = robust(r)
        h = SIGMA * 1e-3
        for _ in range(ITERATIONS):
            J = np.zeros((len(r), N_PARAMS))
            for g in FD_GROUPS:
                dp = np.zeros(N_PARAMS)
                dp[g] = h[g]
                dr = (self._residuals(p + dp, obs, prev, anchors, world) - r)
                for j in g:
                    J[:, j] = np.where(S[:, j], dr / h[j], 0.0)
            Jw, rw = J * sw[:, None] * SIGMA, r * sw  # scaled parameters: steps measured in sigmas
            A, g_ = Jw.T @ Jw, Jw.T @ rw
            for _ in range(4):
                step = -np.linalg.solve(A + lam * (np.diag(np.diag(A)) + 1e-6 * np.eye(N_PARAMS)), g_) * SIGMA
                r_new = self._residuals(p + step, obs, prev, anchors, world)
                cost_new, sw_new = robust(r_new)
                if cost_new < cost:
                    p, r, cost, sw = p + step, r_new, cost_new, sw_new
                    lam = max(lam / 3, 1e-6)
                    break
                lam *= 5
            else:
                break
        return p, cost

    def _sparsity(self, obs, anchors, world=None):
        rows = []
        for _ in obs:
            rows.append(np.repeat(DEPENDS, 2, axis=0))
        if world is not None:
            rel = DEPENDS | DEPENDS[[23]] | DEPENDS[[24]]
            rows.append(np.repeat(rel, 3, axis=0))
            rows.append(DEPENDS[[29, 30, 31, 32]].any(axis=0, keepdims=True))
        rows.append(np.eye(N_PARAMS, dtype=bool))
        lim = np.zeros((4, N_PARAMS), bool)
        for i, s in enumerate(SIDE):
            lim[2 * i, ARM[s][1]] = True
            lim[2 * i + 1, LEG[s][1]] = True
        rows.append(lim)
        rows.append(DEPENDS[[29, 30, 31, 32]])
        for k, _ in anchors:
            rows.append(np.repeat(DEPENDS[[k]], 3, axis=0))
        S = np.vstack(rows)
        return lil_matrix(S.astype(np.int8))

    def update(self, t: float, joints: np.ndarray | None, tracking: str, views: dict[str, PoseResult | None],
               anchors: dict[str, np.ndarray] | None = None) -> np.ndarray | None:
        """Model landmarks for this frame, or None (use the triangulated joints) while measuring or lost.

        joints: the triangulated/cleaned joints (for measuring and initialisation); views: the selected,
        correctly labelled person per camera; anchors: side -> planted ankle position."""
        produced_at = self.world_scale  # the scale the pipeline built this frame's one-camera joints at
        if self._last_seen is not None and t - self._last_seen > FORGET_S:
            self.reset()
        if tracking == "lost" or joints is None:
            return None
        self._last_seen = t
        if self.shape is None:
            usable = tracking == "3d" or (self.single and tracking.startswith("single_cam"))
            if usable and joints is not None and np.isfinite(joints[NEEDED]).all():
                # measured at scale 1: one-camera joints come scaled by world_scale (1 until measured)
                self._samples.append(np.array(joints, float) / (produced_at if self.single else 1.0))
                if len(self._samples) >= self.frames_needed:
                    self.shape = measure(self._samples)
                    if self.shape is not None and self.single:
                        from vision.mono import stature, upright_rotation

                        self.upright = upright_rotation(self._samples)
                        if self.height_m:
                            U = np.eye(3) if self.upright is None else self.upright
                            est = float(np.median([stature((J - (J[23] + J[24]) / 2) @ U.T) for J in self._samples]))
                            self.world_scale = self.height_m / est
                            self.shape = self.shape.scaled(self.world_scale)
            if self.shape is None:
                return None
        obs = []
        for n, cam in self.cams.items():
            v = views.get(n)
            if v is None or not v.detected:
                continue
            w = np.sqrt(np.clip(v.vis, 0, 1) * TRUST)
            w[v.vis < MIN_VIS] = 0.0
            uv = _undistorted(v, self.cfgs[n])
            bad = ~np.isfinite(uv).all(axis=1)
            w[bad] = 0.0
            obs.append((cam, np.where(bad[:, None], 0.0, uv), w))
        if not obs:
            return None
        if self._p is None:
            p0 = params_from_joints(np.asarray(joints, float), self.shape)
            if p0 is None:
                return None
            self._p = p0
        prev = self._p
        anc = [(SIDE[s]["an"], pos) for s, pos in (anchors or {}).items() if pos is not None and np.isfinite(pos).all()]
        import time

        t0 = time.perf_counter()
        world = self._world(views)
        S = self._sparsity(obs, anc, world).toarray().astype(bool)
        x, cost = self._solve(prev, obs, prev, anc, S, world)
        # stuck in a wrong pose? restart from this frame's triangulated joints and keep the better fit
        if self._median_residual(x, obs) > RECOVER_PX:
            fresh = params_from_joints(np.asarray(joints, float), self.shape)
            if fresh is not None:
                x2, _ = self._solve(fresh, obs, fresh, anc, S, world)
                if self._median_residual(x2, obs) < self._median_residual(x, obs):
                    x = x2
                    self._smooth.reset() if self._smooth is not None else None
                    self._gate_out = None  # a recovered pose snaps into place instead of being gated
                    self.recoveries += 1
        self.last_ms = (time.perf_counter() - t0) * 1000
        if not np.isfinite(x).all():
            return None
        self._p = x
        self.last_cost = float(cost)
        # smooth the pose numbers (not the joints): steady when still, quick when moving, bones exact
        if self.single:  # adaptive smoothing first, then the 2D-motion gate
            if self._gate_t is not None and t - self._gate_t > GATE_GAP_S:  # back after a gap: start afresh
                self._gate_uv = self._gate_w = self._gate_out = None
                self._smooth.reset() if self._smooth is not None else None
            self._gate_t = t
            smoothed = self._smooth(x, t) if (self._smooth is not None and SINGLE_SMOOTH_TOO) else x
            return forward(self._gate(smoothed, obs[0]), self.shape)
        return forward(self._smooth(x, t) if self._smooth is not None else x, self.shape)

    def _gate(self, x: np.ndarray, ob) -> np.ndarray:
        """One camera: a pose number may only change as fast as the landmarks it moves are moving in the
        camera's 2D view. The camera sees side-to-side and up-down motion precisely; only depth is noisy,
        so joints that are still in 2D are held still in 3D, and real movement (which shows in 2D) passes."""
        _, uv, w = ob
        prev_uv, prev_w, prev_out = self._gate_uv, self._gate_w, self._gate_out
        self._gate_uv, self._gate_w = uv.copy(), w.copy()
        if prev_out is None or prev_uv is None:
            self._gate_out = x.copy()
            return x
        ok = (w > 0) & (prev_w > 0)
        motion = np.where(ok, np.linalg.norm(uv - prev_uv, axis=1), np.nan)
        gain = np.ones(N_PARAMS)
        for j in range(N_PARAMS):
            m = motion[DEPENDS[:, j]]
            m = m[np.isfinite(m)]
            if m.size:
                gain[j] = float(np.clip(np.median(m) / GATE_FULL_PX, GATE_MIN_GAIN, 1.0))
        self._gate_out = prev_out + gain * (x - prev_out)
        return self._gate_out
