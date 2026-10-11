"""Refine both camera poses from body observations, pinned to the floor markers.

Parameters: each camera's pose (Rodrigues vector + translation, mat -> camera) and one 3D point
per selected (frame, joint), plus each camera's focal-length scale. Residuals: pinhole reprojection of
the body points in both views and of the fixed marker corners (weighted), on undistorted pixel
observations. The principal point and distortion are fixed.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix

from vision.config import CameraConfig, MatConfig
from vision.frames import cam_center, rotation_angle_deg
from vision.markers import MEASURED_FOCAL_STD
from vision.triangulate import triangulate_points

BA_JOINTS = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32]
NAMES = ("A", "B")
MIN_POINTS = 50
N_CAM = 7  # Rodrigues vector, translation, focal scale


@dataclass
class BundleReport:
    ok: bool
    reasons: list[str]
    R: dict[str, np.ndarray]
    t: dict[str, np.ndarray]
    body_rms_before: float
    body_rms_after: float
    marker_rms_before: dict[str, float]
    marker_rms_after: dict[str, float]
    centers: dict[str, np.ndarray]
    moved_m: dict[str, float]
    rotated_deg: dict[str, float]
    K: dict[str, np.ndarray]
    focal_before: dict[str, float]
    focal_after: dict[str, float]
    baseline_m: float
    median_angle_deg: float
    n_frames: int
    n_points: int

    def summary(self) -> str:
        lines = [
            f"frames used: {self.n_frames}, joint observations: {self.n_points}",
            f"body reprojection RMS: {self.body_rms_before:.2f} px -> {self.body_rms_after:.2f} px",
        ]
        for n in NAMES:
            c = self.centers[n]
            mb, ma = self.marker_rms_before.get(n), self.marker_rms_after.get(n)
            markers = f"marker RMS {mb:.2f} -> {ma:.2f} px, " if mb is not None else "no markers, "
            lines.append(f"camera {n}: {markers}centre ({c[0]:+.3f}, {c[1]:.3f}, {c[2]:+.3f}) m, "
                         f"moved {self.moved_m[n] * 100:.1f} cm, rotated {self.rotated_deg[n]:.2f}°, "
                         f"focal {self.focal_before[n]:.0f} -> {self.focal_after[n]:.0f} px")
        lines.append(f"baseline {self.baseline_m:.3f} m, median triangulation angle {self.median_angle_deg:.1f}°")
        lines.append("OK" if self.ok else "REJECTED: " + "; ".join(self.reasons))
        return "\n".join(lines)


def rodrigues(r) -> np.ndarray:
    r = np.asarray(r, float)
    theta = np.linalg.norm(r)
    if theta < 1e-12:
        return np.eye(3)
    k = r / theta
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * Kx + (1 - np.cos(theta)) * Kx @ Kx


def _scaled(K, s: float) -> np.ndarray:
    K = np.array(K, float)
    K[0, 0] *= s
    K[1, 1] *= s
    return K


def _pinhole(P, K, R, t) -> np.ndarray:
    Xc = P @ R.T + t
    uv = Xc[:, :2] / Xc[:, 2:3]
    return uv @ K[:2, :2].T + K[:2, 2]


def _undistort(uv, cam: CameraConfig) -> np.ndarray:
    return cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K).reshape(-1, 2)


def _rms(err2d: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(err2d.reshape(-1, 2) ** 2, axis=1)))) if err2d.size else float("nan")


def evaluate_guards(body_before, body_after, moved_m, rotated_deg, marker_before, marker_after,
                    focal_scale: dict[str, float] | None = None, max_move_m: float = 0.5,
                    max_rot_deg: float = 15.0, max_focal_change: float = 0.15) -> list[str]:
    reasons = []
    if not body_after < body_before:
        reasons.append(f"body reprojection did not improve ({body_before:.2f} -> {body_after:.2f} px)")
    for n, m in moved_m.items():
        if m > max_move_m:
            reasons.append(f"camera {n} moved {m:.2f} m from the marker solution (limit {max_move_m} m)")
    for n, r in rotated_deg.items():
        if r > max_rot_deg:
            reasons.append(f"camera {n} rotated {r:.1f}° from the marker solution (limit {max_rot_deg}°)")
    for n, after in marker_after.items():
        limit = max(2 * marker_before[n], 1.0)
        if after > limit:
            reasons.append(f"camera {n} marker RMS grew to {after:.2f} px (limit {limit:.2f} px)")
    for n, s in (focal_scale or {}).items():
        if abs(s - 1) > max_focal_change:
            reasons.append(f"camera {n} focal length changed {abs(s - 1) * 100:.0f}% (limit {max_focal_change * 100:.0f}%)")
    return reasons


def _collect(pairs, min_vis, max_frames):
    both = [(a, b) for a, b in pairs if a is not None and b is not None and a.detected and b.detected]
    if len(both) > max_frames:
        both = [both[i] for i in np.linspace(0, len(both) - 1, max_frames).astype(int)]
    uv_a, uv_b, frame = [], [], []
    for f, (a, b) in enumerate(both):
        for j in BA_JOINTS:
            if a.vis[j] >= min_vis and b.vis[j] >= min_vis:
                uv_a.append(a.lm2d[j])
                uv_b.append(b.lm2d[j])
                frame.append(f)
    return np.array(uv_a, float).reshape(-1, 2), np.array(uv_b, float).reshape(-1, 2), np.array(frame, int)


def _sparsity(n_pts: int, marker_rows: dict[str, int]) -> coo_matrix:
    rows, cols = [], []
    i = np.arange(n_pts)
    first = 2 * N_CAM
    for k in range(2):  # body residuals of camera A then B
        off, base = k * N_CAM, k * 2 * n_pts
        for r in (0, 1):
            rr = base + 2 * i + r
            for c in range(N_CAM):
                rows.append(rr)
                cols.append(np.full(n_pts, off + c))
            for c in range(3):
                rows.append(rr)
                cols.append(first + 3 * i + c)
    row = 4 * n_pts
    for k, n in enumerate(NAMES):
        m = marker_rows.get(n, 0)
        if m:
            off = k * N_CAM
            rows.append(np.repeat(np.arange(row, row + m), N_CAM))
            cols.append(np.tile(np.arange(off, off + N_CAM), m))
            row += m
    r, c = np.concatenate(rows), np.concatenate(cols)
    return coo_matrix((np.ones(len(r)), (r, c)), shape=(row, first + 3 * n_pts))


def refine_extrinsics(cams: dict[str, CameraConfig], mat: MatConfig, pairs, marker_weight: float = 5.0,
                      min_vis: float = 0.8, max_frames: int = 400, drop_initial_px: float = 40.0) -> BundleReport:
    for n in NAMES:
        if n not in cams or not cams[n].calibrated:
            raise ValueError(f"camera {n} needs lens and marker calibration before refinement")
    ca, cb = cams["A"], cams["B"]
    corners3d = mat.marker_corners()
    markers = {}
    for n in NAMES:
        obs = cams[n].marker_obs or {}
        ids = sorted(set(obs) & set(corners3d))
        if ids:
            markers[n] = (np.concatenate([corners3d[i] for i in ids]),
                          _undistort(np.concatenate([obs[i] for i in ids]), cams[n]))
    if not markers:
        raise ValueError("no marker observations stored: run python -m vision.calib_extrinsics first")

    uv_a, uv_b, frame = _collect(pairs, min_vis, max_frames)
    X0, err0, _ = triangulate_points(uv_a, uv_b, ca, cb)
    keep = np.isfinite(err0) & (err0 <= drop_initial_px)
    for f in np.unique(frame):
        m = frame == f
        if not np.isfinite(err0[m]).any() or np.nanmedian(err0[m]) > drop_initial_px:
            keep[m] = False
    uv_a, uv_b, X0, frame = uv_a[keep], uv_b[keep], X0[keep], frame[keep]
    n_pts = len(X0)
    if n_pts < MIN_POINTS:
        raise ValueError(f"not enough body observations ({n_pts}; need ≥ {MIN_POINTS} joints seen well by both "
                         f"cameras). Stay in view of both cameras and move around the mat.")

    obs = {"A": _undistort(uv_a, ca), "B": _undistort(uv_b, cb)}
    K = {n: cams[n].K for n in NAMES}
    x0 = np.concatenate([cv2.Rodrigues(ca.R)[0].ravel(), np.ravel(ca.t), [1.0],
                         cv2.Rodrigues(cb.R)[0].ravel(), np.ravel(cb.t), [1.0], X0.ravel()])

    def pieces(x):
        cam = {n: x[k * N_CAM:(k + 1) * N_CAM] for k, n in enumerate(NAMES)}
        poses = {n: (rodrigues(c[0:3]), c[3:6]) for n, c in cam.items()}
        Ks = {n: _scaled(K[n], c[6]) for n, c in cam.items()}
        P = x[2 * N_CAM:].reshape(-1, 3)
        body = {n: _pinhole(P, Ks[n], *poses[n]) - obs[n] for n in NAMES}
        mark = {n: _pinhole(M3, Ks[n], *poses[n]) - m2 for n, (M3, m2) in markers.items()}
        return body, mark, poses

    def residuals(x):
        body, mark, _ = pieces(x)
        return np.concatenate([body["A"].ravel(), body["B"].ravel()] +
                              [marker_weight * mark[n].ravel() for n in NAMES if n in mark])

    body0, mark0, poses0 = pieces(x0)
    sparsity = _sparsity(n_pts, {n: 2 * len(m[0]) for n, m in markers.items()})
    # a zoom already measured well (e.g. refined before) is held: a webcam can't zoom, and freeing it lets
    # "zoom in + move back" fit the body almost as well (the cameras then run away by metres)
    lower, upper = np.full(len(x0), -np.inf), np.full(len(x0), np.inf)
    for k, n in enumerate(NAMES):
        zs = cams[n].zoom_std
        if zs is not None and zs <= MEASURED_FOCAL_STD:
            lower[k * N_CAM + 6], upper[k * N_CAM + 6] = 1.0 - 1e-9, 1.0 + 1e-9
    result = least_squares(residuals, x0, jac_sparsity=sparsity, loss="soft_l1", f_scale=2.0,
                           x_scale="jac", method="trf", max_nfev=200, bounds=(lower, upper))
    body1, mark1, poses1 = pieces(result.x)

    body_before = _rms(np.concatenate([body0["A"], body0["B"]]))
    body_after = _rms(np.concatenate([body1["A"], body1["B"]]))
    marker_before = {n: _rms(v) for n, v in mark0.items()}
    marker_after = {n: _rms(v) for n, v in mark1.items()}
    R = {n: poses1[n][0] for n in NAMES}
    t = {n: np.asarray(poses1[n][1], float).copy() for n in NAMES}
    centers = {n: cam_center(R[n], t[n]) for n in NAMES}
    moved = {n: float(np.linalg.norm(centers[n] - cam_center(*poses0[n]))) for n in NAMES}
    rotated = {n: rotation_angle_deg(R[n], poses0[n][0]) for n in NAMES}
    scale = {n: float(result.x[k * N_CAM + 6]) for k, n in enumerate(NAMES)}
    P = result.x[2 * N_CAM:].reshape(-1, 3)
    ra, rb = P - centers["A"], P - centers["B"]
    cos = np.sum(ra * rb, axis=1) / (np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1))
    reasons = evaluate_guards(body_before, body_after, moved, rotated, marker_before, marker_after, scale)
    return BundleReport(
        ok=not reasons, reasons=reasons, R=R, t=t,
        body_rms_before=body_before, body_rms_after=body_after,
        marker_rms_before=marker_before, marker_rms_after=marker_after,
        centers=centers, moved_m=moved, rotated_deg=rotated,
        K={n: _scaled(K[n], scale[n]) for n in NAMES},
        focal_before={n: float(K[n][0, 0]) for n in NAMES},
        focal_after={n: float(K[n][0, 0] * scale[n]) for n in NAMES},
        baseline_m=float(np.linalg.norm(centers["A"] - centers["B"])),
        median_angle_deg=float(np.degrees(np.median(np.arccos(np.clip(cos, -1, 1))))),
        n_frames=int(len(np.unique(frame))), n_points=n_pts,
    )
