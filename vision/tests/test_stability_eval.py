import numpy as np

from vision.config import MatConfig
from vision.mono import stature
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.stability_eval import _lag_frames, metrics, replay
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

K = np.array([[1484.0, 0, 640], [0, 1484.0, 360], [0, 0, 1]])


def test_lag_is_found_between_frames():
    t = np.arange(300)
    a = np.sin(t / 15.0)
    assert abs(_lag_frames(a, np.sin((t - 3) / 15.0)) - 3.0) < 0.05
    assert abs(_lag_frames(a, np.sin((t - 1.5) / 15.0)) - 1.5) < 0.1


def test_a_steady_synthetic_session_reports_steady(tmp_path):
    cam, X0 = make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    path, rng = tmp_path / "one.jsonl", np.random.default_rng(4)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    with Recorder(path, {"B": cam}, {"length_m": MatConfig().length_m}) as rec:
        for k in range(150):  # 3 s standing still, then 2 s walking sideways
            X = X0 + [0.01 * max(k - 90, 0), 0.0, 0.0]
            rec.write(pipe.process(1000 + k / 30, None, pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng)))
    outs, cam2, restarts = replay(str(path), h)
    m = metrics(outs, cam2, restarts)
    assert m["fitted_pct"] > 50 and m["still_frames"] > 30
    assert m["still_p90_cm"] < 1.0 and m["jumps"] == 0 and m["floating"] == 0
    assert m["reproj_px"] < 10.0  # the walk is followed a frame or two behind (smoothing)
    assert m["restarts"] == 0  # (no lag check: the hips never move up or down here)


def test_unknown_lag_is_nan_not_zero():
    assert np.isnan(_lag_frames(np.full(20, np.nan), np.full(20, np.nan)))


def test_an_empty_recording_or_missing_camera_is_reported_not_a_crash(tmp_path, capsys):
    import sys

    from vision import stability_eval

    cam = make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K)
    path = tmp_path / "empty.jsonl"
    with Recorder(path, {"B": cam}, {"length_m": MatConfig().length_m}):
        pass
    m = metrics(*replay(str(path), 1.7)[:2])
    assert m["frames"] == 0 and np.isnan(m["still_p90_cm"]) and np.isnan(m["lag_frames"])
    argv = sys.argv
    try:
        sys.argv = ["stability_eval", str(path), "--height-cm", "170", "--cam", "A"]
        stability_eval.main()
    finally:
        sys.argv = argv
    assert "no camera A" in capsys.readouterr().out
