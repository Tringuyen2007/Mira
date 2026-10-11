import numpy as np

from vision.config import CameraConfig, MatConfig, load_cameras, load_mat, save_cameras


def test_marker_centers_match_layout():
    c = MatConfig().marker_centers()
    np.testing.assert_allclose(c[0], [0.20, 0.0, 0.76], atol=1e-9)
    np.testing.assert_allclose(c[1], [-0.20, 0.0, 0.76], atol=1e-9)
    np.testing.assert_allclose(c[2], [0.20, 0.0, -0.76], atol=1e-9)
    np.testing.assert_allclose(c[3], [-0.20, 0.0, -0.76], atol=1e-9)


def test_marker_corners_order_and_size():
    corners = MatConfig().marker_corners()
    tl, tr, br, bl = corners[0]
    # TL is toward Mira (+z) and the patient's left (+x)
    assert tl[0] > tr[0] and tl[2] > bl[2]
    np.testing.assert_allclose(np.linalg.norm(tr - tl), 0.17)
    np.testing.assert_allclose(np.linalg.norm(bl - tl), 0.17)
    assert np.all(corners[0][:, 1] == 0.0)


def test_load_mat_reads_repo_file():
    mat = load_mat()
    assert mat.length_m == 1.73
    np.testing.assert_allclose(mat.camera_targets["A"], [3.18, 0.85, 3.18])
    np.testing.assert_allclose(mat.aim_point, [0.0, 0.8, 0.0])


def test_cameras_round_trip_and_backup(tmp_path):
    path = tmp_path / "cameras.yaml"
    assert load_cameras(path) == {}
    cam = CameraConfig(
        name="A", index=1, hint="1280x720",
        K=np.eye(3), dist=np.zeros(5),
        R=np.eye(3), t=np.array([0.0, 0.0, 3.0]), extrinsics_source="markers", extrinsics_rms=0.8,
        marker_obs={0: np.arange(8.0).reshape(4, 2)}, target_eye=np.array([3.2, 0.85, 3.2]),
    )
    assert save_cameras({"A": cam}, path) is None
    loaded = load_cameras(path)["A"]
    assert loaded.index == 1 and loaded.calibrated
    np.testing.assert_allclose(loaded.t, [0.0, 0.0, 3.0])
    np.testing.assert_allclose(loaded.marker_obs[0], cam.marker_obs[0])
    np.testing.assert_allclose(loaded.target_eye, [3.2, 0.85, 3.2])
    backup = save_cameras({"A": cam}, path)
    assert backup is not None and backup.exists()


def test_uncalibrated_camera_flags():
    cam = CameraConfig(name="B", index=0)
    assert not cam.has_intrinsics and not cam.calibrated
    assert CameraConfig.from_dict("B", cam.to_dict()).index == 0


def test_old_file_with_intrinsics_rms_still_loads(tmp_path):
    path = tmp_path / "cameras.yaml"
    path.write_text("cameras:\n  A:\n    index: 0\n    K: [[900, 0, 640], [0, 900, 360], [0, 0, 1]]\n"
                    "    dist: [0, 0, 0, 0, 0]\n    intrinsics_rms: 0.22\n")
    cam = load_cameras(path)["A"]
    assert cam.has_intrinsics and cam.target_eye is None
    assert "intrinsics_rms" not in cam.to_dict()
