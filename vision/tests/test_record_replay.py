import numpy as np

from vision.config import MatConfig
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import cams_from_header, iter_frames, read_header, replay, replay_outputs
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.types import PoseResult


def _record(path):
    cams, X = target_cams(), standing_skeleton()
    pipe = Pipeline(cams)
    outs = []
    with Recorder(path, cams, {"length_m": MatConfig().length_m}) as rec:
        for k in range(3):
            a, b = pose_from_points(cams["A"], X, t=k / 30), pose_from_points(cams["B"], X, t=k / 30 + 0.004)
            outs.append(pipe.process(1000 + k / 30, a, b, 0.004))
            rec.write(outs[-1])
        # nobody in view: undetected raw results and NaN joints
        outs.append(pipe.process(1000.2, PoseResult("A", 0.2), PoseResult("B", 0.2), 0.0))
        rec.write(outs[-1])
    return cams, outs


def test_round_trip(tmp_path):
    path = tmp_path / "rec.jsonl"
    cams, outs = _record(path)
    header = read_header(path)
    assert header["type"] == "header" and header["version"] == 1
    assert cams_from_header(header)["A"].calibrated
    frames = list(iter_frames(path))
    assert len(frames) == 4
    for (frame, raw, skew), out in zip(frames, outs):
        np.testing.assert_array_equal(frame.joints, out.frame.joints)
        assert frame.tracking == out.frame.tracking
    assert frames[3][0].tracking == "lost" and not frames[3][1]["A"].detected


def test_replay_and_retriangulate(tmp_path):
    path = tmp_path / "rec.jsonl"
    cams, outs = _record(path)
    plain = list(replay(path, realtime=False))
    assert [f.tracking for f in plain] == ["3d", "3d", "3d", "lost"]
    again = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False))
    assert again[0].tri is not None and again[0].frame.tracking == "3d"
    np.testing.assert_allclose(again[0].frame.joints, standing_skeleton(), atol=1e-6)
    assert again[3].frame.tracking == "lost"


def test_replay_can_retriangulate_without_smoothing(tmp_path):
    path = tmp_path / "rec.jsonl"
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    with Recorder(path, cams, {}) as rec:
        pipe = Pipeline(cams)
        for k in range(10):
            a = pose_from_points(cams["A"], X, t=k / 30, noise_px=3.0, rng=rng)
            b = pose_from_points(cams["B"], X, t=k / 30, noise_px=3.0, rng=rng)
            rec.write(pipe.process(1000 + k / 30, a, b, 0.0))
    raw = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False))
    for out in raw:
        np.testing.assert_allclose(out.frame.joints[out.tri.ok], out.tri.points[out.tri.ok])
