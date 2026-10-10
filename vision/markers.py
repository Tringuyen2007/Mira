"""Floor-marker detection and camera pose from markers."""
from __future__ import annotations

import cv2
import numpy as np

from vision.config import MatConfig
from vision.frames import angle_between_deg, cam_center, project


def aruco_dictionary(name: str = "DICT_4X4_50"):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


class MarkerDetector:
    def __init__(self, mat: MatConfig):
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(aruco_dictionary(mat.aruco_dictionary), params)
        self._ids = set(mat.marker_corners())

    def detect(self, image: np.ndarray) -> dict[int, np.ndarray]:
        """Mat marker id -> (4, 2) pixel corners in ArUco order TL, TR, BR, BL."""
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2).astype(float) for c, i in zip(corners, ids.ravel()) if int(i) in self._ids}


def average_detections(history: list[dict[int, np.ndarray]], min_fraction: float = 0.5) -> dict[int, np.ndarray]:
    """Median corners per marker over a window; markers seen in fewer than min_fraction of frames are dropped."""
    if not history:
        return {}
    seen: dict[int, list[np.ndarray]] = {}
    for det in history:
        for i, c in det.items():
            seen.setdefault(i, []).append(c)
    return {i: np.median(np.stack(cs), axis=0) for i, cs in seen.items() if len(cs) >= min_fraction * len(history)}


def solve_camera_pose(obs: dict[int, np.ndarray], mat: MatConfig, K, dist):
    """Camera pose (R, t, reprojection RMS px) from marker corners; None if no markers."""
    corners = mat.marker_corners()
    ids = [i for i in sorted(obs) if i in corners]
    if not ids:
        return None
    obj = np.concatenate([corners[i] for i in ids]).astype(np.float64)
    img = np.concatenate([obs[i] for i in ids]).astype(np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    R, _ = cv2.Rodrigues(rvec)
    t = tvec.ravel()
    rms = float(np.sqrt(np.mean(np.sum((project(obj, K, dist, R, t) - img) ** 2, axis=1))))
    return R, t, rms


def _floor_unit(v) -> np.ndarray:
    v = np.array(v, float)
    v[1] = 0.0
    return v / np.linalg.norm(v)


def placement_hint(R, t, target_eye, aim, tol_position_m: float, tol_angle_deg: float) -> dict:
    """How to move/turn a camera to reach its target, in the camera's own terms (as if standing behind it)."""
    C = cam_center(R, t)
    fwd = np.asarray(R[2], float)
    right_f = _floor_unit(R[0])
    aim_dir = (np.asarray(aim, float) - C) / np.linalg.norm(np.asarray(aim, float) - C)
    aim_f = _floor_unit(aim_dir)
    fwd_f = _floor_unit(fwd)
    delta = np.asarray(target_eye, float) - C
    dx, dy, dfwd = float(delta @ right_f), float(delta[1]), float(delta @ aim_f)
    yaw = float(np.degrees(np.arctan2(aim_f @ right_f, aim_f @ fwd_f)))
    pitch = float(np.degrees(np.arcsin(np.clip(aim_dir[1], -1, 1)) - np.arcsin(np.clip(fwd[1], -1, 1))))
    ok = np.linalg.norm(delta) <= tol_position_m and angle_between_deg(fwd, aim_dir) <= tol_angle_deg
    parts = []
    if abs(dx) >= 0.02:
        parts.append(f"move {abs(dx) * 100:.0f} cm {'right' if dx > 0 else 'left'}")
    if abs(dfwd) >= 0.02:
        parts.append(f"move {abs(dfwd) * 100:.0f} cm {'closer' if dfwd > 0 else 'back'}")
    if abs(dy) >= 0.02:
        parts.append(f"{'raise' if dy > 0 else 'lower'} {abs(dy) * 100:.0f} cm")
    if abs(yaw) >= 1:
        parts.append(f"turn {'right' if yaw > 0 else 'left'} {abs(yaw):.0f}°")
    if abs(pitch) >= 1:
        parts.append(f"tilt {'up' if pitch > 0 else 'down'} {abs(pitch):.0f}°")
    if ok or not parts:
        msg = "OK"
    else:
        msg = ", ".join(parts)
        msg = msg[0].upper() + msg[1:]
    return {"ok": bool(ok), "dx_cm": round(dx * 100), "dy_cm": round(dy * 100), "dist_cm": round(dfwd * 100),
            "yaw_deg": round(yaw, 1), "pitch_deg": round(pitch, 1), "msg": msg}


def camera_warnings(R, t) -> list[str]:
    C = cam_center(R, t)
    warnings = []
    if C[1] < 0:
        warnings.append("camera is below the floor: check marker IDs and that their top edges face Mira")
    if C[2] < 0:
        warnings.append("camera is behind the mat centre: cameras belong on the Mira (+z) side")
    return warnings
