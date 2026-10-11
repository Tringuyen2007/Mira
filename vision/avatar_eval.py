"""Measure how body-like the 3D avatar is on recordings.

    python -m vision.avatar_eval data/recordings/<file>.jsonl [...] [--plain] [--current-calibration]

For each recording, replays it through the pipeline (the calibration saved in the recording) and the avatar rig, and
prints: arm (elbow/wrist) inside the torso %, elbow/knee hinge violations %, foot yaw change per frame
(median/p95/max), bone-length std, how far confidently tracked wrists/ankles end up from tracking,
and rig time per frame. --plain evaluates the rig without anatomical constraints, for comparison;
--current-calibration replays with config/cameras.yaml instead.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

ARM_POINTS = (13, 14, 15, 16)
LIMBS = {"knee_l": (23, 25, 27), "knee_r": (24, 26, 28), "elbow_l": (11, 13, 15), "elbow_r": (12, 14, 16)}
FEET = {"l": (29, 31), "r": (30, 32)}
ENDS = (15, 16, 27, 28)
BONES = ((11, 13), (13, 15), (23, 25), (25, 27), (11, 12), (23, 24))
NEEDED = (11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)


@dataclass
class Report:
    frames: int
    arm_in_torso_pct: float
    hinge_pct: float
    foot_yaw_p50: float
    foot_yaw_p95: float
    foot_yaw_max: float
    bone_std_mm: float
    truth_mean_cm: float
    truth_p95_cm: float
    ms_per_frame: float

    def row(self, name: str) -> str:
        return (f"{name:32s} {self.frames:6d}  {self.arm_in_torso_pct:5.1f}%  {self.hinge_pct:5.1f}%  "
                f"{self.foot_yaw_p50:4.0f}/{self.foot_yaw_p95:4.0f}/{self.foot_yaw_max:4.0f}°  "
                f"{self.bone_std_mm:6.2f}  {self.truth_mean_cm:5.1f}/{self.truth_p95_cm:5.1f}  {self.ms_per_frame:5.2f}")


HEADER = (f"{'recording':32s} {'frames':>6s}  {'arm∈torso':>6s} {'hinge':>6s}  {'foot yaw p50/p95/max':>18s}  "
          f"{'bone mm':>6s}  {'truth cm':>11s}  {'ms':>5s}")


def torso_axes(J):
    hip, sh = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2
    up = sh - hip
    up /= np.linalg.norm(up)
    left = J[23] - J[24]
    left -= (left @ up) * up
    left /= np.linalg.norm(left)
    return hip, sh, up, left, np.cross(left, up)


def inside_torso(J, p) -> bool:
    hip, sh, up, left, fwd = torso_axes(J)
    r = p - hip
    h = r @ up
    return (0.05 < h < np.linalg.norm(sh - hip) - 0.03 and abs(r @ left) < 0.85 * np.linalg.norm(J[11] - J[12]) / 2
            and abs(r @ fwd) < 0.09)


def hinge_violation(J, a, b, c, is_knee: bool) -> bool:
    u, v = J[a] - J[b], J[c] - J[b]
    interior = np.degrees(np.arccos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)), -1, 1)))
    if interior < 30 - 1e-6:  # flexed past 150°
        return True
    if not is_knee:
        return False
    *_, fwd = torso_axes(J)
    d = (J[c] - J[a]) / np.linalg.norm(J[c] - J[a])
    off = (J[b] - J[a]) - ((J[b] - J[a]) @ d) * d
    return off @ fwd < -0.02  # knee behind the hip-ankle line: bending backwards


def foot_yaw(J, heel, toe) -> float:
    v = J[toe] - J[heel]
    return float(np.degrees(np.arctan2(v[0], v[2])))


def evaluate(outputs, rig) -> Report:
    n = inside = hinge = 0
    yaw_steps, bones, truth = [], [], []
    prev_yaw = None
    spent = 0.0
    for o in outputs:
        t0 = time.perf_counter()
        P = rig.update(o.frame.t, o.frame.joints, o.frame.tracking, o.frame.conf, o.feet_locked)  # as debug_view does
        spent += time.perf_counter() - t0
        if o.frame.tracking != "3d" or rig.lengths is None or P is None or not np.isfinite(P[list(NEEDED)]).all():
            prev_yaw = None
            continue
        n += 1
        inside += any(inside_torso(P, P[k]) for k in ARM_POINTS)
        hinge += any(hinge_violation(P, *idx, name.startswith("knee")) for name, idx in LIMBS.items())
        y = np.array([foot_yaw(P, *FEET[s]) for s in FEET])
        if prev_yaw is not None:
            yaw_steps.append(float(np.abs((y - prev_yaw + 180) % 360 - 180).max()))
        prev_yaw = y
        bones.append([np.linalg.norm(P[a] - P[b]) for a, b in BONES])
        J, conf = o.frame.joints, o.frame.conf
        truth += [float(np.linalg.norm(P[k] - J[k])) for k in ENDS if conf[k] >= 0.8 and np.isfinite(J[k]).all()]
    yaw = np.array(yaw_steps) if yaw_steps else np.zeros(1)
    tr = np.array(truth) * 100 if truth else np.zeros(1)
    return Report(n, 100 * inside / max(n, 1), 100 * hinge / max(n, 1), float(np.median(yaw)),
                  float(np.percentile(yaw, 95)), float(yaw.max()),
                  float(np.array(bones).std(axis=0).max() * 1000) if bones else 0.0,
                  float(tr.mean()), float(np.percentile(tr, 95)), 1000 * spent / max(n, 1))


def recording_outputs(path, current_calibration: bool = False):
    """Replay a recording through the pipeline with the calibration it was recorded with
    (or the current config/cameras.yaml when current_calibration=True)."""
    from vision.config import load_cameras
    from vision.replay import cams_from_header, read_header, replay_outputs

    cams = load_cameras() if current_calibration else cams_from_header(read_header(path))
    return replay_outputs(path, retriangulate=True, cams=cams, realtime=False)


def main() -> int:
    from vision.rig import BodyRig

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    p.add_argument("--plain", action="store_true", help="rig without anatomical constraints")
    p.add_argument("--current-calibration", action="store_true",
                   help="replay with config/cameras.yaml instead of the calibration saved in each recording")
    args = p.parse_args()
    print(HEADER)
    for path in args.paths:
        outs = recording_outputs(path, args.current_calibration)
        print(evaluate(outs, BodyRig(constraints=not args.plain)).row(path.split("/")[-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
