"""Camera-B-only replays of two-camera recordings, compared with the two-camera triangulation.

    python -m vision.mono_eval data/recordings/<file>.jsonl [...] [--height-cm H]
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from vision.mono import stature


def run(path, only: str | None, height_m=None):
    from vision.config import load_mat
    from vision.pipeline import Pipeline
    from vision.replay import cams_from_header, iter_records, read_header

    cams = cams_from_header(read_header(path))
    if only:
        cams = {only: cams[only]}
    pipe = Pipeline(cams, mat=load_mat(), height_m=height_m)
    outs, ms = [], []
    for frame, raw, skew, people in iter_records(path):
        get = (lambda n: people.get(n, [])) if people is not None else (lambda n: raw.get(n))
        a = get("A") if "A" in cams else None
        b = get("B") if "B" in cams else None
        t0 = time.perf_counter()
        outs.append(pipe.process(frame.t, a, b, skew))
        ms.append((time.perf_counter() - t0) * 1000)
    return outs, ms


def reference_height(tri_outs) -> float:
    hs = []
    for o in tri_outs:
        if o.tri is None or not o.tri.ok[[2, 5, 23, 24, 27, 28, 29, 30, 31, 32]].all():
            continue
        J = o.tri.points
        rel = J - (J[23] + J[24]) / 2
        u, v = J[23] - J[25], J[27] - J[25]
        if np.degrees(np.arccos(np.clip(u @ v / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))) > 165:
            hs.append(stature(rel))
    return float(np.median(hs)) if hs else 1.75


def compare(path, height_cm=None) -> str:
    tri, _ = run(path, None)
    h = height_cm / 100 if height_cm else reference_height(tri)
    mono, ms = run(path, "B", h)
    seen = [o.raw.get("B") is not None for o in mono]
    present = [np.isfinite(o.frame.joints[[23, 24]]).all() for o in mono]
    model = np.array([o.tracker == "model" for o in mono])
    J = np.array([o.frame.joints for o in mono])
    bones = [np.nanstd(np.linalg.norm(J[model][:, a] - J[model][:, b], axis=1)) * 1000
             for a, b in ((23, 25), (25, 27), (11, 13), (13, 15))] if model.any() else [np.nan]
    err = []
    for o_t, o_m in zip(tri, mono):
        if o_t.tri is not None and o_t.tri.ok[[23, 24]].all() and np.isfinite(o_m.frame.joints[[23, 24]]).all():
            ref = (o_t.tri.points[23] + o_t.tri.points[24]) / 2
            err.append(np.linalg.norm(((o_m.frame.joints[23] + o_m.frame.joints[24]) / 2 - ref)[[0, 2]]))
    hip = np.array([(o.tri.points[23] + o.tri.points[24]) / 2 if o.tri is not None and o.tri.ok[[23, 24]].all()
                    else [np.nan] * 3 for o in tri])
    v = np.linalg.norm(np.diff(hip, axis=0), axis=1)
    still = np.zeros(len(hip), bool)
    for i in range(len(v) - 10):
        if np.all(v[i:i + 10] < 0.01):
            still[i:i + 11] = True
    m = still[1:] & still[:-1]
    jit = np.nanmedian(np.linalg.norm(np.diff(J, axis=0), axis=2)[m]) * 100 if m.any() else np.nan
    est = []
    for o in mono:
        Jm = o.frame.joints
        if o.tracker == "model" and np.isfinite(Jm).all():
            est.append(stature(Jm - (Jm[23] + Jm[24]) / 2))
    e = np.array(err) * 100
    fits = [o.fit_ms for o in mono if o.fit_ms is not None]
    return (f"== {path.split('/')[-1]}: B sees someone {100 * np.mean(seen):.0f}%, body present when seen "
            f"{100 * np.mean([p for p, s in zip(present, seen) if s]):.0f}%, model {100 * model.mean():.0f}%\n"
            f"   hip position vs two-camera: median {np.median(e):.1f} cm, p90 {np.percentile(e, 90):.1f} cm (n {len(e)})\n"
            f"   bones std {np.nanmax(bones):.2f} mm, still jitter {jit:.2f} cm, fit {np.median(fits):.1f} ms "
            f"(p95 {np.percentile(fits, 95):.1f}), height {np.median(est) * 100:.0f} cm vs {h * 100:.0f} cm")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    p.add_argument("--height-cm", type=float)
    a = p.parse_args()
    for path in a.paths:
        print(compare(path, a.height_cm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
