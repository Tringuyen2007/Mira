import numpy as np

from vision.frames import (
    cam_center, look_at_pose, mat_to_cam, project, rotation_angle_deg,
    uncalibrated_world_to_mat, world_to_mat,
)

K = np.array([[900.0, 0, 640], [0, 900, 360], [0, 0, 1]])


def test_look_at_pose_centre_and_axes():
    eye, target = np.array([1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0])
    R, t = look_at_pose(eye, target)
    np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(R), 1.0)
    np.testing.assert_allclose(cam_center(R, t), eye, atol=1e-12)
    c = mat_to_cam(target[None], R, t)[0]
    assert abs(c[0]) < 1e-9 and abs(c[1]) < 1e-9 and c[2] > 0
    # a point above the target is "up" in the image (negative camera y)
    assert mat_to_cam((target + [0, 0.5, 0])[None], R, t)[0, 1] < 0


def test_plus_x_is_miras_right():
    # Mira's viewpoint: in front of the mat, looking back along -z
    R, t = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([0.0, 0.8, 0.0]))
    assert mat_to_cam(np.array([[0.5, 0.8, 0.0]]), R, t)[0, 0] > 0


def test_project_target_hits_principal_point_and_nan_passthrough():
    R, t = look_at_pose(np.array([1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0]))
    px = project(np.array([[0.0, 0.8, 0.0], [np.nan, 0, 0]]), K, np.zeros(5), R, t)
    np.testing.assert_allclose(px[0], [640, 360], atol=1e-6)
    assert np.isnan(px[1]).all()


def test_world_to_mat_inverts_camera_rotation():
    R, _ = look_at_pose(np.array([-1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0]))
    offsets = np.array([[0.1, 0.5, -0.2], [0.0, -0.9, 0.05]])
    world = offsets @ R.T  # what MediaPipe reports (camera-aligned axes)
    np.testing.assert_allclose(world_to_mat(world, R), offsets, atol=1e-12)


def test_uncalibrated_mapping_puts_head_up():
    # MediaPipe world: y down, so the head has negative y
    out = uncalibrated_world_to_mat(np.array([[0.0, -0.6, 0.0]]))
    assert out[0, 1] > 0


def test_rotation_angle():
    R1, _ = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([0.0, 0.8, 0.0]))
    R2, _ = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([np.tan(np.radians(10)) * 3, 0.8, 0.0]))
    assert abs(rotation_angle_deg(R1, R2) - 10.0) < 1e-6
