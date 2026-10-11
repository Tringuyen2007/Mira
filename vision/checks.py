"""Numeric sanity checks for the 3D skeleton.

    python -m vision.checks --height-cm 180   # 5 s live: stand still, facing Mira, in view of both cameras
    python -m vision.checks --from FILE       # on a recording, re-triangulated with the current calibration
    python -m vision.checks --poses 1         # live, with pose looking for one person per camera (cost check)
"""
from __future__ import annotations

import argparse
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vision.anchor import FootAnchor
from vision.config import CameraConfig, MatConfig, load_cameras, load_mat
from vision.pipeline import Pipeline, PipelineOutput
from vision.triangulate import triangulate_points

BONES_CHECKED = {"thigh_l": (23, 25), "thigh_r": (24, 26), "shin_l": (25, 27), "shin_r": (26, 28)}
KNEE = {"l": (23, 25, 27), "r": (24, 26, 28)}
MARKER_PAIRS = [(0, 2), (1, 3), (0, 1), (2, 3)]
EYES = (2, 5)
FOOT_POINTS = {"l": (29, 31), "r": (30, 32)}  # heel, toe
EYE_HEIGHT_RATIO = 0.936  # eye height / stature (adult anthropometry); MediaPipe has no head-top point


@dataclass
class Check:
    name: str
    value: float | None
    unit: str
    target: str
    passed: bool | None  # None = could not be measured


def bone_length_stats(seq: np.ndarray) -> dict[str, tuple[float, float]]:
    out = {}
    for name, (i, j) in BONES_CHECKED.items():
        lengths = np.linalg.norm(seq[:, i] - seq[:, j], axis=1)
        lengths = lengths[np.isfinite(lengths)]
        out[name] = (float(lengths.mean()), float(lengths.std())) if len(lengths) >= 10 else (float("nan"), float("nan"))
    return out


def knee_angle_deg(joints: np.ndarray, side: str) -> float:
    hip, knee, ankle = (joints[k] for k in KNEE[side])
    u, v = hip - knee, ankle - knee
    with np.errstate(invalid="ignore"):
        c = u @ v / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def estimate_height_cm(seq: np.ndarray) -> float:
    """Stature from the median eye height above the mat surface (y = 0)."""
    eye_y = np.nanmean(seq[:, list(EYES), 1], axis=1)
    eye_y = eye_y[np.isfinite(eye_y)]
    return float(np.median(eye_y) / EYE_HEIGHT_RATIO * 100) if len(eye_y) else float("nan")


def foot_height_cm(seq: np.ndarray) -> float:
    """Median over frames of the lower foot's lowest heel/toe height above the mat surface."""
    with warnings.catch_warnings():  # frames with no foot point give NaN, dropped below
        warnings.simplefilter("ignore", RuntimeWarning)
        low = np.nanmin(seq[:, [*FOOT_POINTS["l"], *FOOT_POINTS["r"]], 1], axis=1)
    low = low[np.isfinite(low)]
    return float(np.median(low) * 100) if len(low) else float("nan")


def anchored_fraction(outputs: list[PipelineOutput]) -> float | None:
    """% of 3D frames in which a fresh FootAnchor run over the (unanchored) joints holds both feet."""
    anchor, both, n = FootAnchor(), 0, 0
    for o in outputs:
        if o.frame.tracking != "3d":
            continue
        _, locked = anchor.update(o.frame.t, o.frame.joints)
        both += locked["l"] and locked["r"]
        n += 1
    return 100.0 * both / n if n else None


def triangulate_marker_centers(cam_a: CameraConfig, cam_b: CameraConfig) -> dict[int, np.ndarray]:
    obs_a, obs_b = cam_a.marker_obs or {}, cam_b.marker_obs or {}
    out = {}
    for i in sorted(set(obs_a) & set(obs_b)):
        X, _, _ = triangulate_points(obs_a[i], obs_b[i], cam_a, cam_b)
        out[i] = X.mean(axis=0)
    return out


def marker_center_distances(centers: dict[int, np.ndarray], mat: MatConfig) -> list[tuple[str, float, float]]:
    known = mat.marker_centers()
    return [(f"{i}–{j}", float(np.linalg.norm(centers[i] - centers[j])), float(np.linalg.norm(known[i] - known[j])))
            for i, j in MARKER_PAIRS if i in centers and j in centers]


def _check(name, value, unit, target, test) -> Check:
    if value is None or not np.isfinite(value):
        return Check(name, None, unit, target, None)
    return Check(name, float(value), unit, target, bool(test(value)))


def run_checks(outputs: list[PipelineOutput], cams: dict[str, CameraConfig], mat: MatConfig,
               fps: dict[str, float] | None = None, height_cm: float | None = None,
               moved: dict[str, tuple[str, float | None]] | None = None,
               pose_ms: dict[str, float] | None = None) -> list[Check]:
    """`moved`: camera name -> (MoveMonitor status, shift px); `pose_ms`: camera name -> pose time per
    frame (ms); both from a live run."""
    checks = []
    n3d = sum(o.frame.tracking == "3d" for o in outputs)
    frac = 100.0 * n3d / len(outputs) if outputs else None
    checks.append(_check("frames tracked in 3D", frac, "%", "≥ 90", lambda v: v >= 90))

    tris = [o.tri for o in outputs if o.tri is not None]
    errs = np.concatenate([t.reproj_err[t.ok] for t in tris]) if tris else np.empty(0)
    checks.append(_check("mean body reprojection", float(errs.mean()) if errs.size else None, "px", "≤ 5", lambda v: v <= 5))

    seq = np.stack([o.frame.joints for o in outputs if o.frame.tracking == "3d"]) if n3d else None
    stats = bone_length_stats(seq) if seq is not None else {k: (float("nan"),) * 2 for k in BONES_CHECKED}
    for name, (_, std) in stats.items():
        checks.append(_check(f"{name} length std", std * 100, "cm", "< 1.0", lambda v: v < 1.0))
    for side in ("l", "r"):
        vals = [knee_angle_deg(j, side) for j in seq] if seq is not None else []
        vals = [v for v in vals if np.isfinite(v)]
        checks.append(_check(f"standing knee angle {side}", float(np.median(vals)) if vals else None, "°", "170–180",
                             lambda v: 170 <= v <= 180))

    checks.append(_check("feet anchored while standing", anchored_fraction(outputs), "%", "≥ 80", lambda v: v >= 80))
    foot_h = foot_height_cm(seq) if seq is not None else None
    checks.append(_check("foot height above mat", foot_h, "cm", "-1 to 5", lambda v: -1 <= v <= 5))

    if height_cm is not None:
        est = estimate_height_cm(seq) if seq is not None else None
        checks.append(_check("estimated height", est, "cm", f"{height_cm:.0f} ± 5%",
                             lambda v: abs(v / height_cm - 1) <= 0.05))

    angles = np.concatenate([t.angle_deg[t.ok] for t in tris]) if tris else np.empty(0)
    checks.append(_check("median triangulation angle", float(np.median(angles)) if angles.size else None, "°", "60–100",
                         lambda v: 60 <= v <= 100))

    if "A" in cams and "B" in cams and cams["A"].calibrated and cams["B"].calibrated:
        centers = triangulate_marker_centers(cams["A"], cams["B"])
        for label, measured, expected in marker_center_distances(centers, mat):
            checks.append(_check(f"marker distance {label}", measured * 100, "cm", f"{expected * 100:.1f} ± 2",
                                 lambda v, e=expected: abs(v / 100 - e) <= 0.02))

    skews = [abs(o.skew_s) * 1000 for o in outputs if o.skew_s is not None]
    checks.append(_check("pair skew p95", float(np.percentile(skews, 95)) if skews else None, "ms", "≤ 33",
                         lambda v: v <= 33))
    for n, v in sorted((fps or {}).items()):
        checks.append(_check(f"capture fps {n}", v, "fps", "≥ 20", lambda x: x >= 20))
    for n, v in sorted((pose_ms or {}).items()):
        checks.append(_check(f"pose time {n}", v, "ms", "≤ 45", lambda x: x <= 45))
    for n, (status, shift) in sorted((moved or {}).items()):
        value = shift if status in ("ok", "moved") else None
        checks.append(Check(f"camera {n} not moved", value, "px", "< 3",
                            None if value is None else status != "moved"))
    return checks


def format_checks(checks: list[Check]) -> str:
    lines = [f"{'check':32s} {'value':>12s}  {'target':14s} result"]
    for c in checks:
        value = "n/a" if c.value is None else f"{c.value:.2f} {c.unit}"
        result = {True: "PASS", False: "FAIL", None: "--"}[c.passed]
        lines.append(f"{c.name:32s} {value:>12s}  {c.target:14s} {result}")
    return "\n".join(lines)


def outputs_from_recording(path, cams: dict[str, CameraConfig]) -> list[PipelineOutput]:
    """Re-triangulate a recording with the current calibration, unsmoothed and unanchored so jitter and
    floor errors stay visible."""
    from vision.replay import replay_outputs

    return list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False,
                               anchor_feet=False))


def _live_outputs(cams, mat, model: str, seconds: float, num_poses: int | None = None):
    from vision.capture import mono_to_unix
    from vision.live import LiveSource
    from vision.pose import NUM_POSES
    from vision.markers import MarkerDetector
    from vision.moved import MoveMonitor

    outputs = []
    monitor = MoveMonitor(cams, MarkerDetector(mat), interval_s=0.25)
    # smoothing and foot anchoring would hide the jitter and floor errors these checks measure
    pipeline = Pipeline(cams, smoothing=False, mat=mat, anchor_feet=False)
    with LiveSource(cams, model, mat, num_poses=NUM_POSES if num_poses is None else num_poses) as src:
        problems = src.wait_ready()
        if problems:
            raise SystemExit("cannot run checks: " + "; ".join(problems))
        print("Stand still on the mat, facing Mira, arms by your sides. Starting in 2 s ...")
        start = time.monotonic()
        while time.monotonic() - start < 2.0 + seconds:
            for pair in src.poll(0.01):
                out = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
                if time.monotonic() - start >= 2.0:
                    outputs.append(out)
            for n, v in src.latest_frames().items():
                if v is not None:
                    monitor.update(n, v[0], time.monotonic())
        stats = src.stats()
    fps = {n: s["fps"] for n, s in stats.items()}
    pose_ms = {n: s["pose_ms"] for n, s in stats.items()}
    moved = {n: (monitor.status(n), monitor.shift_px(n)) for n in cams}
    return outputs, fps, moved, pose_ms


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--from", dest="from_path", type=Path)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    p.add_argument("--height-cm", type=float, help="your height, to check the 3D scale")
    p.add_argument("--poses", type=int, default=None,
                   help="people pose looks for per camera, live (default vision.pose.NUM_POSES)")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    if args.from_path:
        outputs, fps, moved, pose_ms = outputs_from_recording(args.from_path, cams), None, None, None
    else:
        outputs, fps, moved, pose_ms = _live_outputs(cams, mat, args.model, args.seconds, args.poses)
    checks = run_checks(outputs, cams, mat, fps, args.height_cm, moved, pose_ms)
    print(format_checks(checks))
    return 0 if all(c.passed is not False for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
