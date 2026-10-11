"""How steady the one-camera body is: replays of one-camera recordings (camera B, the recorded person).

    python -m vision.stability_eval data/recordings/<file>.jsonl [...] [--height-cm 170]

Per file: jitter while the person is still in B's picture, motion over all fitted frames, one-frame
jumps, ankle spikes, feet floating above the floor, how well the body matches B's 2D points, lag of the
hip height behind B's own 2D hip height, the deepest knee bend, restarts and fit time.
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np

BODY = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 31, 32]  # shoulders ... toes
ANKLES = [27, 28]
STILL_PX = 2.5  # median 2D motion of the body below this ...
STILL_FRAMES = 15  # ... for this many frames in a row: the person is still
JUMP_M = 0.08
FLOAT_M = 0.05
MAX_LAG = 10  # frames searched either way
LAG_STEP = 0.05  # frames


def replay(path, height_m: float | None, cam_name: str = "B"):
    """(pipeline outputs, camera config, restarts) for a one-camera replay of `path`."""
    from vision.config import load_mat
    from vision.pipeline import Pipeline
    from vision.replay import cams_from_header, iter_records, read_header

    cam = cams_from_header(read_header(path))[cam_name]
    pipe = Pipeline({cam_name: cam}, mat=load_mat(), height_m=height_m)
    outs = []
    for frame, raw, skew, _ in iter_records(path):
        p = raw.get(cam_name)
        args = (None, p) if cam_name == "B" else (p, None)
        outs.append(pipe.process(frame.t, *args, skew))
    restarts = pipe._model.recoveries if pipe._model is not None else 0
    return outs, cam, restarts


def _project(cam, X):
    R, t, K = np.asarray(cam.R, float), np.ravel(cam.t).astype(float), np.asarray(cam.K, float)
    Xc = X @ R.T + t
    return Xc[..., :2] / Xc[..., 2:] @ K[:2, :2].T + K[:2, 2]


def _knee(J, hip, knee, ankle):
    u, v = J[:, hip] - J[:, knee], J[:, ankle] - J[:, knee]
    c = np.sum(u * v, axis=1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1))
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def _lag_frames(a, b) -> float:
    """Frames b is behind a, to LAG_STEP: the shift of b (linearly interpolated between frames) whose
    correlation with a, over the frames where both are known, is highest."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    idx = np.arange(n, dtype=float)
    best, lag = -np.inf, 0.0
    for k in np.arange(-MAX_LAG, MAX_LAG + 1e-9, LAG_STEP):
        src = idx + k  # b shifted back by k frames lines up with a
        inside = (src >= 0) & (src <= n - 1)
        shifted = np.interp(src[inside], idx, np.nan_to_num(b))
        valid = np.interp(src[inside], idx, np.isfinite(b).astype(float)) > 0.999
        both = valid & np.isfinite(a[inside])
        if both.sum() < 10:
            continue
        c = float(np.corrcoef(a[inside][both], shifted[both])[0, 1])
        if c > best:
            best, lag = c, float(k)
    return round(lag, 2)


def metrics(outs, cam, restarts: int = 0) -> dict:
    J = np.array([o.frame.joints for o in outs])
    t = np.array([o.frame.t for o in outs])
    model = np.array([o.tracker == "model" for o in outs])
    seen = [o.raw.get(cam.name) for o in outs]
    lm = np.array([p.lm2d if (p is not None and p.detected) else np.full((33, 2), np.nan) for p in seen])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices where nobody is seen
        motion2d = np.nanmedian(np.linalg.norm(np.diff(lm, axis=0), axis=2)[:, BODY], axis=1)
        still = np.zeros(len(t), bool)
        for i in range(len(motion2d) - STILL_FRAMES + 1):
            if np.all(motion2d[i:i + STILL_FRAMES] < STILL_PX):
                still[i:i + STILL_FRAMES + 1] = True
        step = np.linalg.norm(np.diff(J, axis=0), axis=2)[:, BODY]
        ok = model[1:] & model[:-1] & (np.diff(t) < 0.1)
        st = ok & still[1:] & still[:-1]
        fitted = J[model]
        low = fitted[:, [29, 30, 31, 32], 1].min(axis=1) if model.any() else np.empty(0)
        knee = np.minimum(_knee(fitted, 23, 25, 27), _knee(fitted, 24, 26, 28)) if model.any() else np.empty(0)
        reproj = np.linalg.norm(_project(cam, fitted) - lm[model], axis=2)[:, BODY] if model.any() else np.empty(0)
        hip_px = np.where(model, (_project(cam, J[:, 23]) + _project(cam, J[:, 24]))[:, 1] / 2, np.nan)
        hip_2d = np.where(model, (lm[:, 23, 1] + lm[:, 24, 1]) / 2, np.nan)
        pct = lambda a, q: float(np.nanpercentile(a, q) * 100) if np.size(a) else float("nan")  # noqa: E731
        ank = step[ok][:, [BODY.index(k) for k in ANKLES]]
        fit_ms = [o.fit_ms for o in outs if o.fit_ms is not None]
        return {
            "frames": len(outs), "fitted_pct": float(100 * model.mean()), "still_frames": int(st.sum()),
            "still_med_cm": pct(step[st], 50), "still_p90_cm": pct(step[st], 90),
            "moving_p90_cm": pct(step[ok], 90),
            "jumps": int((np.nanmax(step[ok], axis=1) > JUMP_M).sum()) if ok.any() else 0,
            "ankle_p99_cm": pct(ank, 99),
            "floating": int((low > FLOAT_M).sum()),
            "reproj_px": float(np.nanmedian(reproj)) if reproj.size else float("nan"),
            "lag_frames": _lag_frames(hip_2d, hip_px),
            "deepest_knee_deg": float(np.nanpercentile(knee, 2)) if knee.size else float("nan"),
            "restarts": int(restarts),
            "fit_ms": float(np.median(fit_ms)) if fit_ms else float("nan"),
        }


def format_row(name: str, m: dict) -> str:
    return (f"{name}: fitted {m['fitted_pct']:.0f}%  still {m['still_med_cm']:.2f}/{m['still_p90_cm']:.2f} cm "
            f"({m['still_frames']} fr)  moving p90 {m['moving_p90_cm']:.2f} cm  jumps>8cm {m['jumps']}  "
            f"ankle p99 {m['ankle_p99_cm']:.1f} cm  floating {m['floating']}  reproj {m['reproj_px']:.1f} px  "
            f"lag {m['lag_frames']:.2f} fr  deepest knee {m['deepest_knee_deg']:.0f}°  restarts {m['restarts']}  "
            f"fit {m['fit_ms']:.1f} ms")


def main() -> None:
    from vision.config import load_person

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recordings", nargs="+")
    ap.add_argument("--height-cm", type=float, default=None, help="person's height (default: config/person.yaml; not saved)")
    ap.add_argument("--cam", default="B")
    args = ap.parse_args()
    cm = args.height_cm if args.height_cm is not None else load_person()
    h = cm / 100 if cm else None
    for path in args.recordings:
        outs, cam, restarts = replay(path, h, args.cam)
        print(format_row(path.split("/")[-1], metrics(outs, cam, restarts)))


if __name__ == "__main__":
    main()
