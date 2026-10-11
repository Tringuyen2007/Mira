"""Floor-marker detection and camera pose from markers."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares

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


NOMINAL_DFOV_DEG = 55.0  # starting guess / fallback: typical narrow webcam diagonal field of view
MAX_FOCAL_STD = 0.03
FOV_RANGE_DEG = (30.0, 100.0)
NEAR_IDS, FAR_IDS = {0, 1}, {2, 3}


def nominal_K(width: int, height: int, dfov_deg: float = NOMINAL_DFOV_DEG) -> np.ndarray:
    f = np.hypot(width, height) / 2 / np.tan(np.radians(dfov_deg) / 2)
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1.0]])


@dataclass
class FocalEstimate:
    K: np.ndarray
    f: float
    f_std: float
    hfov_deg: float
    vfov_deg: float
    rms: float


def estimate_focal(obs: dict[int, np.ndarray], mat: MatConfig, image_size) -> tuple[FocalEstimate | None, str | None]:
    """Focal length (fx = fy) from one averaged marker view, principal point at the image centre, no distortion.

    Returns (estimate, None) or (None, reason).
    """
    corners = mat.marker_corners()
    ids = sorted(i for i in obs if i in corners)
    if not (set(ids) & NEAR_IDS and set(ids) & FAR_IDS):
        return None, "need markers at both ends of the mat to measure zoom"
    w, h = image_size
    obj = np.concatenate([corners[i] for i in ids]).astype(np.float64)
    img = np.concatenate([obs[i] for i in ids]).astype(np.float64)
    K0 = nominal_K(w, h)
    c = K0[:2, 2]
    ok, rvec, tvec = cv2.solvePnP(obj, img, K0, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None, "marker pose could not be solved"

    def residuals(p):
        K = np.array([[p[0], 0, c[0]], [0, p[0], c[1]], [0, 0, 1.0]])
        px, _ = cv2.projectPoints(obj, p[1:4], p[4:7], K, None)
        return (px.reshape(-1, 2) - img).ravel()

    x0 = np.r_[K0[0, 0], rvec.ravel(), tvec.ravel()]
    r = least_squares(residuals, x0, x_scale=np.r_[100.0, [0.1] * 6])
    f = float(r.x[0])
    dof = max(len(r.fun) - len(r.x), 1)
    cov = np.linalg.pinv(r.jac.T @ r.jac) * (np.sum(r.fun ** 2) / dof)
    f_std = float(np.sqrt(max(cov[0, 0], 0.0)))
    if f <= 0:
        return None, "zoom estimate failed"
    hfov = float(np.degrees(2 * np.arctan(w / 2 / f)))
    vfov = float(np.degrees(2 * np.arctan(h / 2 / f)))
    if f_std / f > MAX_FOCAL_STD:
        return None, f"zoom too uncertain (±{100 * f_std / f:.0f}%): get more of the mat in view"
    if not FOV_RANGE_DEG[0] <= hfov <= FOV_RANGE_DEG[1]:
        return None, f"zoom estimate {hfov:.0f}° is implausible: check marker IDs and orientation"
    rms = float(np.sqrt(np.mean(r.fun.reshape(-1, 2) ** 2) * 2))
    K = np.array([[f, 0, c[0]], [0, f, c[1]], [0, 0, 1.0]])
    return FocalEstimate(K=K, f=f, f_std=f_std, hfov_deg=hfov, vfov_deg=vfov, rms=rms), None


@dataclass
class ZoomSolve:
    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    rms: float
    guessed: bool  # True: K is a fallback, not measured this time; the pose is for placement only
    zoom_msg: str


def solve_with_zoom(obs: dict[int, np.ndarray], mat: MatConfig, image_size,
                    last_good: FocalEstimate | None = None) -> tuple[ZoomSolve | None, FocalEstimate | None]:
    """Zoom then pose from markers. Returns (solve or None, the focal estimate to remember)."""
    est, reason = estimate_focal(obs, mat, image_size)
    if est is not None:
        K, guessed, msg, last_good = est.K, False, f"zoom {est.hfov_deg:.1f}° x {est.vfov_deg:.1f}°", est
    elif last_good is not None:
        K, guessed, msg = last_good.K, True, f"zoom from earlier ({reason})"
    else:
        K, guessed, msg = nominal_K(*image_size), True, f"zoom guessed ({reason})"
    sol = solve_camera_pose(obs, mat, K, np.zeros(5))
    if sol is None:
        return None, last_good
    R, t, rms = sol
    return ZoomSolve(K=K, R=R, t=t, rms=rms, guessed=guessed, zoom_msg=msg), last_good


def _floor_unit(v) -> np.ndarray:
    v = np.array(v, float)
    v[1] = 0.0
    return v / np.linalg.norm(v)


def _sentence(parts: list[str], ok: bool, ok_msg: str) -> str:
    if ok or not parts:
        return ok_msg
    msg = ", ".join(parts)
    return msg[0].upper() + msg[1:]


def placement_hint(R, t, target_eye, aim, tol_position_m: float, tol_angle_deg: float) -> dict:
    """How to move/turn a camera to reach its target, in the camera's own terms (as if standing behind it).

    Position first ("Move 20 cm back"), then aim ("Turn right 2°, tilt down 3°, rotate clockwise 1°").
    Roll is the tilt of the image's horizontal axis; "clockwise" is as seen from behind the camera.
    """
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
    roll = float(np.degrees(np.arcsin(np.clip(R[0][1], -1, 1))))  # > 0: image right edge raised
    position_ok = bool(np.linalg.norm(delta) <= tol_position_m)
    aim_ok = bool(angle_between_deg(fwd, aim_dir) <= tol_angle_deg and abs(roll) <= tol_angle_deg)
    pos = []
    if abs(dx) >= 0.02:
        pos.append(f"move {abs(dx) * 100:.0f} cm {'right' if dx > 0 else 'left'}")
    if abs(dfwd) >= 0.02:
        pos.append(f"move {abs(dfwd) * 100:.0f} cm {'closer' if dfwd > 0 else 'back'}")
    if abs(dy) >= 0.02:
        pos.append(f"{'raise' if dy > 0 else 'lower'} {abs(dy) * 100:.0f} cm")
    turn = []
    if abs(yaw) >= 0.5:
        turn.append(f"turn {'right' if yaw > 0 else 'left'} {abs(yaw):.1f}°")
    if abs(pitch) >= 0.5:
        turn.append(f"tilt {'up' if pitch > 0 else 'down'} {abs(pitch):.1f}°")
    if abs(roll) >= 0.5:
        turn.append(f"rotate {'clockwise' if roll > 0 else 'counter-clockwise'} {abs(roll):.1f}°")
    position_msg = _sentence(pos, position_ok, "Position OK")
    aim_msg = _sentence(turn, aim_ok, "Aim OK")
    ok = position_ok and aim_ok
    return {"ok": ok, "position_ok": position_ok, "aim_ok": aim_ok,
            "dx_cm": round(dx * 100), "dy_cm": round(dy * 100), "dist_cm": round(dfwd * 100),
            "yaw_deg": round(yaw, 1), "pitch_deg": round(pitch, 1), "roll_deg": round(roll, 1),
            "position_msg": position_msg, "aim_msg": aim_msg,
            "msg": "OK" if ok else f"{position_msg}; {aim_msg}"}


def camera_warnings(R, t) -> list[str]:
    C = cam_center(R, t)
    warnings = []
    if C[1] < 0:
        warnings.append("camera is below the floor: check marker IDs and that their top edges face Mira")
    if C[2] < 0:
        warnings.append("camera is behind the mat centre: cameras belong on the Mira (+z) side")
    return warnings
