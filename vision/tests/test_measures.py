import numpy as np
import pytest

from vision.measures import image_squat_metrics, measure_angles, populate_angles
from vision.mono import stature
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import replay_outputs
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton, target_cams
from vision.types import FrameResult


def standing():
    j = np.full((33, 3), np.nan)
    for side, x in ((0, 0.1), (1, -0.1)):
        shoulder, elbow, wrist = 11 + side, 13 + side, 15 + side
        hip, knee, ankle, toe = 23 + side, 25 + side, 27 + side, 31 + side
        j[shoulder] = (x, 1.5, 0)
        j[elbow] = (x, 1.2, 0)
        j[wrist] = (x, 0.9, 0)
        j[hip] = (x, 1, 0)
        j[knee] = (x, 0.5, 0)
        j[ankle] = (x, 0, 0)
        j[toe] = (x, 0, 0.2)
    return j


def test_standing_angles_and_gap():
    a = measure_angles(standing())
    for side in ('left', 'right'):
        assert a[f'{side}_knee'] == pytest.approx(180)
        assert a[f'{side}_hip'] == pytest.approx(180)
        assert a[f'{side}_ankle'] == pytest.approx(90)
        assert a[f'{side}_shoulder'] == pytest.approx(0)
        assert a[f'{side}_elbow'] == pytest.approx(180)
    assert a['trunk_lean'] == pytest.approx(0)
    assert a['trunk_side_lean'] == pytest.approx(0)
    assert a['pelvis_rotation'] == pytest.approx(0)
    assert a['knee_to_ankle_gap_ratio'] == pytest.approx(1)


def test_squat_and_lunge_distinguish_both_legs():
    squat = standing()
    squat[[23, 24], 1] = 0.7
    squat[[23, 24], 2] = -0.25
    squat[[11, 12], 1] = 1.2
    squat[[11, 12], 2] = -0.25
    s = measure_angles(squat)
    assert 70 < s['left_knee'] < 130
    assert s['left_knee'] == pytest.approx(s['right_knee'])
    assert s['left_hip'] < 160

    lunge = standing()
    lunge[23] = squat[23]
    lunge[25] = squat[25]
    lunge[11] = squat[11]
    a = measure_angles(lunge)
    assert a['left_knee'] < 130
    assert a['right_knee'] == pytest.approx(180)


def test_asymmetry_and_signed_mat_frame_orientation():
    j = standing()
    j[25, 0] = 0.05
    j[26, 0] = -0.05
    j[[11, 12], 0] += 0.1
    j[[11, 12], 2] += 0.2
    j[23, 2] = 0.1
    a = measure_angles(j)
    assert a['knee_to_ankle_gap_ratio'] == pytest.approx(0.5)
    assert a['trunk_lean'] > 0
    assert a['trunk_side_lean'] > 0
    assert a['pelvis_rotation'] > 0


def test_missing_low_confidence_and_degenerate_joints_omit_only_dependent_values():
    j = standing()
    j[25] = np.nan
    a = measure_angles(j)
    assert 'left_knee' not in a and 'left_hip' not in a
    assert 'right_knee' in a and 'trunk_lean' in a
    assert 'knee_to_ankle_gap_ratio' not in a
    conf = np.ones(33)
    conf[26] = 0.1
    assert 'right_knee' not in measure_angles(standing(), conf)
    j = standing()
    j[27] = j[25]
    assert 'left_knee' not in measure_angles(j)
    assert all(np.isfinite(list(measure_angles(j).values())))


def test_frame_helper_skips_untrusted_and_replaces_stale_angles():
    f = FrameResult(0, standing(), np.ones(33), tracking='3d')
    assert populate_angles(f)['left_knee'] == pytest.approx(180)
    for changes in ({'body': 'measuring'}, {'flags': ['pose_held']},
                    {'flags': ['camera_moved']}, {'tracking': 'lost'}):
        f.angles = {'stale': 1}
        f.body, f.flags, f.tracking = 'ready', [], '3d'
        for name, value in changes.items():
            setattr(f, name, value)
        assert populate_angles(f) == {}
        assert f.angles == {}


def test_pipeline_populates_ready_two_camera_frame_and_skips_lost_frame():
    cams, joints = target_cams(), standing_skeleton()
    ready = Pipeline(cams, smoothing=False).process(
        0, pose_from_points(cams['A'], joints), pose_from_points(cams['B'], joints))
    assert ready.frame.body == 'ready'
    assert ready.frame.angles['left_knee'] == pytest.approx(measure_angles(ready.frame.joints)['left_knee'])
    lost = Pipeline(cams).process(0, None, None)
    assert lost.frame.tracking == 'lost' and lost.frame.angles == {}


def test_pipeline_skips_measuring_and_held_one_camera_frames():
    cam, joints = make_cam('B', (-1.31, 1.0, 4.06)), standing_skeleton()
    h = stature(joints - (joints[23] + joints[24]) / 2)
    pipe = Pipeline({'B': cam}, height_m=h)
    first = pipe.process(0, None, pose_from_points(cam, joints))
    assert first.frame.body == 'measuring' and first.frame.angles == {}
    for k in range(1, 60):
        ready = pipe.process(k / 30, None, pose_from_points(cam, joints, t=k / 30))
    assert ready.frame.body == 'ready' and 'left_knee' in ready.frame.angles
    assert ready.frame.image_metrics['knee_mean'] > 150
    false_pose = pose_from_points(cam, joints + [0, 0, -2], t=2)
    false_pose.vis[[27, 28, 29, 30, 31, 32]] = 0.1
    held = pipe.process(2, None, false_pose)
    assert 'pose_held' in held.frame.flags and held.frame.angles == {}
    assert held.frame.image_metrics == {}


def test_image_squat_metrics_require_visible_knees_and_hips():
    cam, joints = make_cam('B', (-1.31, 1.0, 4.06)), standing_skeleton()
    pose = pose_from_points(cam, joints)
    metrics = image_squat_metrics(pose)
    assert metrics['knee_mean'] > 150 and metrics['torso_px'] > 1
    pose.vis[25] = .1
    assert image_squat_metrics(pose) == {}


def test_recorded_and_retriangulated_frames_have_angles(tmp_path):
    cams, joints = target_cams(), standing_skeleton()
    path = tmp_path / 'pose.jsonl'
    with Recorder(path, cams, {}) as rec:
        rec.write(Pipeline(cams).process(
            1, pose_from_points(cams['A'], joints), pose_from_points(cams['B'], joints)))
    for retriangulate in (False, True):
        out = next(replay_outputs(path, retriangulate=retriangulate, cams=cams, realtime=False))
        assert 150 < out.frame.angles['left_knee'] <= 180
