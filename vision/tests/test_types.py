import json

import numpy as np

from vision.types import FrameResult, PoseResult, from_json_array, to_json_array


def test_json_array_nan_round_trip():
    a = np.array([[1.0, np.nan], [3.0, 4.0]])
    j = json.loads(json.dumps(to_json_array(a)))
    assert j[0][1] is None
    np.testing.assert_array_equal(from_json_array(j), a)
    assert to_json_array(None) is None and from_json_array(None) is None


def test_pose_result_round_trip():
    p = PoseResult("A", 12.5, np.ones((33, 2)), np.full(33, 0.9), np.zeros((33, 3)))
    q = PoseResult.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.cam == "A" and q.t == 12.5 and q.detected
    np.testing.assert_array_equal(q.lm2d, p.lm2d)
    empty = PoseResult.from_dict(json.loads(json.dumps(PoseResult("B", 1.0).to_dict())))
    assert not empty.detected and empty.world is None


def test_frame_result_round_trip_with_nan():
    joints = np.zeros((33, 3))
    joints[5] = np.nan
    f = FrameResult(t=100.0, joints=joints, conf=np.full(33, 0.5), tracking="3d", flags=["x"])
    g = FrameResult.from_dict(json.loads(json.dumps(f.to_dict(), allow_nan=False)))
    np.testing.assert_array_equal(g.joints, joints)
    assert g.tracking == "3d" and g.flags == ["x"] and g.com is None and g.phase == "idle"
