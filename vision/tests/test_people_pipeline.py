import numpy as np

from vision.config import MatConfig
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import iter_frames, iter_records, replay_outputs
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton
from vision.types import CameraPeople

MAT = MatConfig()
K = np.array([[1425.0, 0, 640], [0, 1425.0, 360], [0, 0, 1]])


def _cams():
    return {"A": make_cam("A", (3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K),
            "B": make_cam("B", (-3.18, 0.9, 3.18), target=(0, 0.9, 0), K=K)}


def _people(cams, t, positions):
    out = {"A": [], "B": []}
    for x, z in positions:
        X = standing_skeleton() + [x, 0.0, z]
        out["A"].append(pose_from_points(cams["A"], X, t=t))
        out["B"].append(pose_from_points(cams["B"], X, t=t))
    return out


def test_pipeline_tracks_the_person_at_the_mat_not_the_bystander():
    cams = _cams()
    pipe = Pipeline(cams, mat=MAT)
    p = _people(cams, 0.0, [(2.6, 0.0), (0.0, 0.2)])
    out = pipe.process(1.0, CameraPeople("A", 0.0, p["A"]), CameraPeople("B", 0.0, list(reversed(p["B"]))))
    assert out.frame.tracking == "3d"
    hip = (out.frame.joints[23] + out.frame.joints[24]) / 2
    assert np.linalg.norm(hip[[0, 2]] - [0.0, 0.2]) < 0.05
    assert out.raw["A"] is p["A"][1] and out.ignored["A"] == [p["A"][0]] and out.dropped is None
    assert len(out.people["A"]) == 2


def test_pipeline_single_pose_input_unchanged():
    cams = _cams()
    p = _people(cams, 0.0, [(0.0, 0.0)])
    out = Pipeline(cams, mat=MAT).process(1.0, p["A"][0], p["B"][0])
    assert out.frame.tracking == "3d" and out.people is None and out.ignored is None


def test_recording_keeps_everyone_and_replay_reselects(tmp_path):
    cams = _cams()
    path = tmp_path / "multi.jsonl"
    pipe = Pipeline(cams, mat=MAT)
    with Recorder(path, cams, {}) as rec:
        for k in range(5):
            p = _people(cams, k / 30, [(2.6, 0.0), (0.0, 0.2)])
            rec.write(pipe.process(1000 + k / 30, p["A"], p["B"], 0.0))
    _, raw, _, people = next(iter_records(path))
    assert len(people["A"]) == 2 and raw["A"] is not None
    assert len(list(iter_frames(path))) == 5
    outs = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False))
    assert all(o.ignored is not None and len(o.ignored["A"]) == 1 for o in outs)
