"""Exercise measurements from a 3D MediaPipe skeleton in the mat frame.

All angles are degrees. Knee, hip, ankle and elbow are interior angles: a straight
limb is 180; a standing ankle (shin vertical, foot forward) is 90. Shoulder is the
angle between upper arm and downward torso: 0 with the arm hanging, 180 overhead.
The hip uses the shoulder on the same side, so torso bends are included.

The mat has +y up, +z toward Mira and +x toward the patient's left. Trunk lean is
atan2(shoulder-midpoint minus hip-midpoint z, y): positive toward +z. Trunk side
lean uses x instead of z: positive toward +x. These signs are fixed to the mat,
not the person's facing direction. Pelvis rotation is atan2(left-hip minus
right-hip z, x), positive when the left hip is nearer +z; it assumes the person's
left hip is on the +x side. It is undefined if the hip axis is vertical or its
horizontal projection vanishes. The knee-to-ankle gap ratio divides signed knee
separation along the horizontal hip axis by positive ankle separation along that
axis. A ratio below 1 means the knees are closer together than the ankles; a
negative ratio means their left/right order is reversed. It is omitted when the
ankle gap is under 1 cm.

Missing, non-finite, low-confidence or coincident landmarks omit only dependent
measurements. Invalid measurements are absent from the dict, never NaN. This
module computes geometry only; thresholds and rep/form decisions belong to rules.
"""
from __future__ import annotations

import numpy as np

from vision.types import FrameResult, N_JOINTS, PoseResult

MIN_SEGMENT_M = 1e-6
MIN_ANKLE_GAP_M = 0.01
DEFAULT_MIN_CONF = 0.3


def image_squat_metrics(pose: PoseResult | None) -> dict[str, float]:
    """Knee bend and hip location directly in a selected camera image.

    These measurements are independent of the one-camera 3D body fit. The hip
    displacement is normalized later by the person's standing torso size.
    """
    if pose is None or not pose.detected or pose.vis is None:
        return {}
    needed = (11, 12, 23, 24, 25, 26, 27, 28)
    if not np.isfinite(pose.lm2d[list(needed)]).all() or any(pose.vis[j] < 0.5 for j in needed):
        return {}

    def knee(hip: int, joint: int, ankle: int) -> float | None:
        a = pose.lm2d[hip] - pose.lm2d[joint]
        b = pose.lm2d[ankle] - pose.lm2d[joint]
        n = np.linalg.norm(a) * np.linalg.norm(b)
        return None if n < 1e-6 else float(np.degrees(np.arccos(np.clip(a @ b / n, -1, 1))))

    left, right = knee(23, 25, 27), knee(24, 26, 28)
    if left is None or right is None:
        return {}
    hip = pose.lm2d[[23, 24]].mean(axis=0)
    shoulder = pose.lm2d[[11, 12]].mean(axis=0)
    torso_px = float(np.linalg.norm(hip - shoulder))
    if torso_px < 1:
        return {}
    return {"knee_mean": (left + right) / 2, "hip_y": float(hip[1]), "torso_px": torso_px}


def measure_angles(joints: np.ndarray, conf: np.ndarray | None = None,
                   min_conf: float = DEFAULT_MIN_CONF) -> dict[str, float]:
    """Return available angle and gap measurements from `(33, 3)` mat-frame joints.

    `conf`, when supplied, is a `(33,)` confidence array. A joint below
    `min_conf` is treated as missing. Each returned value is a finite float.
    """
    j = np.asarray(joints, dtype=float)
    if j.shape != (N_JOINTS, 3):
        raise ValueError(f"joints must have shape ({N_JOINTS}, 3)")
    if conf is not None:
        conf = np.asarray(conf, dtype=float)
        if conf.shape != (N_JOINTS,):
            raise ValueError(f"conf must have shape ({N_JOINTS},)")
    valid = np.isfinite(j).all(axis=1)
    if conf is not None:
        valid &= np.isfinite(conf) & (conf >= min_conf)
    result: dict[str, float] = {}

    def angle(a: np.ndarray, vertex: np.ndarray, b: np.ndarray) -> float | None:
        u, v = a - vertex, b - vertex
        nu, nv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
        if min(nu, nv) < MIN_SEGMENT_M:
            return None
        cosine = float(np.clip(np.dot(u, v) / (nu * nv), -1, 1))
        return float(np.degrees(np.arccos(cosine)))

    def at(key: str, a: int, vertex: int, b: int) -> None:
        if valid[[a, vertex, b]].all():
            value = angle(j[a], j[vertex], j[b])
            if value is not None:
                result[key] = value

    for side, offset in (("left", 0), ("right", 1)):
        shoulder, elbow, wrist = 11 + offset, 13 + offset, 15 + offset
        hip, knee, ankle, toe = 23 + offset, 25 + offset, 27 + offset, 31 + offset
        at(f"{side}_knee", hip, knee, ankle)
        at(f"{side}_hip", shoulder, hip, knee)
        at(f"{side}_ankle", knee, ankle, toe)
        at(f"{side}_shoulder", elbow, shoulder, hip)
        at(f"{side}_elbow", shoulder, elbow, wrist)

    if valid[[11, 12, 23, 24]].all():
        trunk = (j[11] + j[12] - j[23] - j[24]) / 2
        if np.linalg.norm(trunk) >= MIN_SEGMENT_M:
            result["trunk_lean"] = float(np.degrees(np.arctan2(trunk[2], trunk[1])))
            result["trunk_side_lean"] = float(np.degrees(np.arctan2(trunk[0], trunk[1])))

    if valid[[23, 24]].all():
        hip_axis = j[23, [0, 2]] - j[24, [0, 2]]
        hip_span = float(np.linalg.norm(hip_axis))
        if hip_span >= MIN_SEGMENT_M:
            result["pelvis_rotation"] = float(np.degrees(np.arctan2(hip_axis[1], hip_axis[0])))
            if valid[[25, 26, 27, 28]].all():
                lateral = hip_axis / hip_span
                knee_gap = float(np.dot(j[25, [0, 2]] - j[26, [0, 2]], lateral))
                ankle_gap = float(np.dot(j[27, [0, 2]] - j[28, [0, 2]], lateral))
                if ankle_gap >= MIN_ANKLE_GAP_M:
                    result["knee_to_ankle_gap_ratio"] = knee_gap / ankle_gap
    return {key: value for key, value in result.items() if np.isfinite(value)}


def populate_angles(frame: FrameResult, min_conf: float = DEFAULT_MIN_CONF) -> dict[str, float]:
    """Replace `frame.angles`; skip frames unsuitable for exercise decisions.

    The caller should invoke this after tracking and before rules. The result is
    empty while the one-camera body is being measured, tracking is lost, or a
    previous pose was held through an occlusion.
    """
    if frame.body != "ready" or frame.tracking == "lost" or any(
            flag in frame.flags for flag in ("pose_held", "camera_moved")):
        frame.angles = {}
    else:
        frame.angles = measure_angles(frame.joints, frame.conf, min_conf)
    return frame.angles
