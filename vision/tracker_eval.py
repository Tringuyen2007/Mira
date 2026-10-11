"""Compare the model tracker with triangulation on recordings.

    python -m vision.tracker_eval data/recordings/<file>.jsonl [...]

Replays each recording (with its own calibration) through both trackers and prints, per tracker:
per-joint-group 2D residual against each camera's detections (px) and how often the joint is
available, 3D jitter while the person stands still (cm), lag on fast wrist/ankle motion (frames,
against unsmoothed triangulation), bone-length std (mm), tracked frames with no body, fit ms.
"""
from __future__ import annotations

import argparse

import numpy as np

GROUPS = {"ankles": [27, 28], "knees": [25, 26], "hips": [23, 24], "wrists": [15, 16],
          "shoulders": [11, 12], "head": [0, 7, 8]}
BONES = ((11, 13), (13, 15), (23, 25), (25, 27), (12, 14), (14, 16), (24, 26), (26, 28))


def _project(cfg, X):
    from vision.frames import project

    return project(X, cfg.K, cfg.dist, cfg.R, cfg.t)


def run(path, tracker: str):
    from vision.config import load_mat
    from vision.pipeline import Pipeline
    from vision.replay import cams_from_header, iter_records, read_header

    cams = cams_from_header(read_header(path))
    pipe = Pipeline(cams, mat=load_mat(), tracker=tracker)
    outs = []
    for frame, raw, skew, people in iter_records(path):
        a = people.get("A", []) if people is not None else raw.get("A")
        b = people.get("B", []) if people is not None else raw.get("B")
        outs.append(pipe.process(frame.t, a, b, skew))
    return outs, cams


def metrics(outs, cams, still: np.ndarray, reference: list) -> dict:
    res = {g: [] for g in GROUPS}
    avail = {g: [] for g in GROUPS}
    for o in outs:
        J = o.frame.joints
        for n, v in o.raw.items():
            if v is None or not v.detected or n not in cams:
                continue
            uv = _project(cams[n], J)
            for g, idx in GROUPS.items():
                for k in idx:
                    if v.vis[k] >= 0.5:
                        avail[g].append(bool(np.isfinite(J[k]).all()))
                        if np.isfinite(uv[k]).all():
                            res[g].append(float(np.linalg.norm(uv[k] - v.lm2d[k])))
    J = np.array([o.frame.joints for o in outs])
    m = still[1:] & still[:-1]
    jit = {g: float(np.nanmedian(np.linalg.norm(np.diff(J[:, idx], axis=0), axis=2)[m]) * 100) for g, idx in GROUPS.items()}
    model = np.array([o.tracker == "model" for o in outs])
    Jb = J[model] if model.any() else J  # model frames only: the first frames are triangulation while measuring
    bones = [np.nanstd([np.linalg.norm(j[a] - j[b]) for j in Jb if np.isfinite(j[[a, b]]).all()]) * 1000 for a, b in BONES]
    lag = []  # delay (frames) that best aligns output positions with unsmoothed triangulation
    for k in (15, 16):
        for axis in range(3):
            o, r = J[:, k, axis], reference[:, k, axis]
            ok = np.isfinite(o) & np.isfinite(r)
            if ok.sum() < 100:
                continue
            o, r = np.where(ok, o - np.nanmean(o[ok]), 0), np.where(ok, r - np.nanmean(r[ok]), 0)
            lag.append(int(np.argmax([np.sum(o[d:] * r[:len(r) - d]) for d in range(0, 11)])))
    no_body = sum(1 for o in outs if o.frame.tracking != "lost" and not np.isfinite(o.frame.joints[[23, 24]]).all())
    ms = [o.fit_ms for o in outs if o.fit_ms is not None]
    return dict(res={g: float(np.median(v)) if v else np.nan for g, v in res.items()},
                avail={g: 100 * float(np.mean(v)) if v else np.nan for g, v in avail.items()},
                jitter=jit, bone_mm=float(np.nanmax(bones)), lag=float(np.median(lag)) if lag else np.nan,
                no_body=no_body, ms=(float(np.median(ms)), float(np.percentile(ms, 95))) if ms else (np.nan, np.nan),
                model_pct=100 * np.mean([o.tracker == "model" for o in outs]))


def compare(path) -> str:
    tri, cams = run(path, "triangulate")
    ref = np.array([o.tri.points if o.tri is not None else np.full((33, 3), np.nan) for o in tri])
    hip = (ref[:, 23] + ref[:, 24]) / 2
    v = np.linalg.norm(np.diff(hip, axis=0), axis=1)
    still = np.zeros(len(ref), bool)
    for i in range(len(v) - 10):
        if np.all(v[i:i + 10] < 0.01):
            still[i:i + 11] = True
    rows = [f"== {path.split('/')[-1]}  ({len(tri)} frames, {still.sum()} still)"]
    for name, outs in (("triangulate", tri), ("model", run(path, "model")[0])):
        m = metrics(outs, cams, still, ref)
        rows.append(f"  {name:11s} model-used {m['model_pct']:3.0f}%  lag {m['lag']:.0f} fr  bone std {m['bone_mm']:5.1f} mm  "
                    f"no-body {m['no_body']}  fit {m['ms'][0]:.1f}/{m['ms'][1]:.1f} ms")
        rows.append("    residual px  " + "  ".join(f"{g} {m['res'][g]:4.1f}" for g in GROUPS))
        rows.append("    available %  " + "  ".join(f"{g} {m['avail'][g]:4.0f}" for g in GROUPS))
        rows.append("    still jitter " + "  ".join(f"{g} {m['jitter'][g]:4.2f}" for g in GROUPS) + "  cm")
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+")
    for path in p.parse_args().paths:
        print(compare(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
