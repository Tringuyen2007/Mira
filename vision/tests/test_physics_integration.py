import numpy as np

from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import replay_outputs
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton, target_cams
from vision.types import PoseResult


def _pair(pipe, cams, t, joints=None, **kwargs):
    joints = standing_skeleton() if joints is None else joints
    return pipe.process(t, pose_from_points(cams["A"], joints, t=t),
                        pose_from_points(cams["B"], joints, t=t), **kwargs).frame


def test_ready_pipeline_populates_com_cop_and_load():
    cams = target_cams()
    result = _pair(Pipeline(cams, smoothing=False), cams, 1.0)
    assert result.body == "ready" and result.tracking == "3d"
    assert result.com is not None and result.cop is not None
    assert 45 < result.load_l_pct < 55
    assert result.heel_l_pct is not None and result.heel_r_pct is not None
    assert 0 < result.load_conf < 1


def test_measuring_and_held_one_camera_frames_clear_physics():
    cam, joints = make_cam("B", (-1.31, 1.0, 4.06)), standing_skeleton()
    pipe = Pipeline({"B": cam}, height_m=1.70)
    measuring = pipe.process(0, None, pose_from_points(cam, joints)).frame
    assert measuring.body == "measuring" and measuring.com is None
    assert measuring.cop is None and measuring.load_l_pct is None and measuring.load_conf == 0
    for k in range(1, 60):
        ready = pipe.process(k / 30, None, pose_from_points(cam, joints, t=k / 30)).frame
    assert ready.body == "ready" and ready.com is not None
    false_pose = pose_from_points(cam, joints + [0, 0, -2], t=2)
    false_pose.vis[[27, 28, 29, 30, 31, 32]] = 0.1
    held = pipe.process(2, None, false_pose).frame
    assert "pose_held" in held.flags
    assert held.com is None and held.cop is None and held.load_l_pct is None and held.load_conf == 0


def test_loss_camera_move_and_restart_do_not_reuse_temporal_state():
    cams = target_cams()
    pipe = Pipeline(cams, smoothing=False)
    _pair(pipe, cams, 1.0)
    shifted = standing_skeleton() + [0.04, 0, 0]
    _pair(pipe, cams, 1.1, shifted)
    lost = pipe.process(1.2, PoseResult("A", 1.2), PoseResult("B", 1.2)).frame
    assert lost.tracking == "lost" and lost.com is None and lost.load_conf == 0
    recovered = _pair(pipe, cams, 1.3)
    fresh = _pair(Pipeline(cams, smoothing=False), cams, 1.3)
    assert recovered.cop is not None and recovered.load_conf == fresh.load_conf
    moved = _pair(pipe, cams, 1.4, camera_moved=True)
    assert "camera_moved" in moved.flags and moved.angles == {}
    assert moved.com is None and moved.cop is None and moved.load_l_pct is None
    after_move = _pair(pipe, cams, 1.5)
    assert after_move.load_conf == fresh.load_conf
    _pair(pipe, cams, 1.6, shifted)
    pipe.reset_physics()  # caller starts another session with the same Pipeline
    after_restart = _pair(pipe, cams, 1.7)
    assert after_restart.load_conf == fresh.load_conf


def test_retriangulated_replay_recomputes_physics(tmp_path):
    cams = target_cams()
    path = tmp_path / "physics.jsonl"
    pipe = Pipeline(cams, smoothing=False)
    with Recorder(path, cams, {}) as recorder:
        for k in range(3):
            t = 1 + k / 30
            recorder.write(pipe.process(t, pose_from_points(cams["A"], standing_skeleton(), t=t),
                                        pose_from_points(cams["B"], standing_skeleton(), t=t)))
    recorded = list(replay_outputs(path, realtime=False))
    recomputed = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, smoothing=False))
    assert len(recorded) == len(recomputed) == 3
    assert all(out.frame.com is not None and out.frame.load_l_pct is not None for out in recomputed)
    np.testing.assert_allclose(recorded[0].frame.com, recomputed[0].frame.com, atol=1e-6)
