import numpy as np

from vision.config import MatConfig
from vision.mono import mono_joints, stature, world_shape
from vision.pipeline import Pipeline
from vision.tests.synth import make_cam, pose_from_points, standing_skeleton

K = np.array([[1484.0, 0, 640], [0, 1484.0, 360], [0, 0, 1]])


def _cam_b():
    return make_cam("B", (-1.31, 1.0, 4.06), target=(0.0, 0.8, 0.0), K=K)


def test_world_shape_is_the_body_relative_to_its_hips_in_the_mat_frame():
    cam, X = _cam_b(), standing_skeleton()
    W = world_shape(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(W, X - (X[23] + X[24]) / 2, atol=1e-9)


def test_mono_joints_stand_where_the_feet_meet_the_floor():
    cam = _cam_b()
    X = standing_skeleton() + [0.25, 0.0, -0.4]
    J = mono_joints(pose_from_points(cam, X), cam)
    np.testing.assert_allclose(J[[23, 24]].mean(axis=0)[[0, 2]], X[[23, 24]].mean(axis=0)[[0, 2]], atol=0.05)
    assert abs(np.min(J[[29, 30, 31, 32], 1])) < 1e-9  # lowest foot point on the mat


def test_stature_of_the_synthetic_body():
    X = standing_skeleton()
    s = stature(X - (X[23] + X[24]) / 2)
    assert abs(s - X[2, 1] / 0.936 - (0 - np.min(X[[29, 30, 31, 32], 1])) / 0.936) < 1e-9


def test_one_camera_pipeline_fits_a_moving_body_with_the_right_scale():
    cam, X0 = _cam_b(), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    rng = np.random.default_rng(0)
    errs = []
    for k in range(120):
        X = X0 + [0.003 * max(k - 60, 0), 0.0, 0.0]
        v = pose_from_points(cam, X, t=k / 30, noise_px=1.0, rng=rng)
        out = pipe.process(k / 30, None, v)
        if k > 70:
            assert out.tracker == "model"
            errs.append(np.median(np.linalg.norm(out.frame.joints - X, axis=1)))
    assert np.median(errs) < 0.04  # one camera: a few cm


def test_one_camera_without_feet_keeps_the_last_position():
    cam, X = _cam_b(), standing_skeleton()
    J1 = mono_joints(pose_from_points(cam, X), cam)
    p = pose_from_points(cam, X)
    p.vis[[27, 28, 29, 30]] = 0.0
    assert mono_joints(p, cam) is None
    J2 = mono_joints(p, cam, last_xz=J1[[23, 24]].mean(axis=0)[[0, 2]])
    assert J2 is not None


FEET_JOINTS = [27, 28, 29, 30, 31, 32]


def _feet_hidden_run(bystander_x=None, good=30, hidden=20, sway=0.0):
    """One camera (B), list input: someone on the mat for `good` frames, then their feet hidden for
    `hidden` frames; optionally a bystander with visible feet beside the mat throughout."""
    cam = _cam_b()
    X = standing_skeleton()
    X[[23, 24], 2] += sway  # hips pushed forward, shoulders still over the ankles: hips not over the feet
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X - (X[23] + X[24]) / 2))
    outs = []
    for k in range(good + hidden):
        p = pose_from_points(cam, X, t=k / 30)
        if k >= good:
            p.vis[FEET_JOINTS] = 0.0
        people = [p]
        if bystander_x is not None:
            people.append(pose_from_points(cam, standing_skeleton() + [bystander_x, 0.0, 0.0], t=k / 30))
        outs.append(pipe.process(k / 30, None, people))
    return outs


def _hip_xz(out):
    return ((out.frame.joints[23] + out.frame.joints[24]) / 2)[[0, 2]]


def test_one_camera_feet_hidden_stays_tracked_and_does_not_jump_to_a_bystander():
    outs = _feet_hidden_run(bystander_x=1.0)  # 0.7 m beside the mat: a qualifying candidate
    before = _hip_xz(outs[29])
    for o in outs[30:]:
        assert o.frame.tracking != "lost"
        assert np.isfinite(o.frame.joints[[23, 24]]).all()
        assert np.linalg.norm(_hip_xz(o) - before) < 0.3


def test_one_camera_feet_hidden_without_bystander_stays_tracked():
    outs = _feet_hidden_run(bystander_x=2.0)
    assert all(o.frame.tracking != "lost" for o in outs[30:])


def test_one_camera_hold_with_feet_hidden_does_not_drift():
    outs = _feet_hidden_run(sway=0.10)  # hips 10 cm in front of the ankles
    held = np.array([_hip_xz(o) for o in outs[30:]])
    start = _hip_xz(outs[29])
    assert np.max(np.linalg.norm(held - start, axis=1)) < 0.01


def test_one_camera_body_scale_is_the_same_after_a_reset():
    cam, X = _cam_b(), standing_skeleton()
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=1.90)
    t, sessions = 0.0, []
    for _ in range(2):
        for _ in range(60):
            out = pipe.process(t, None, [pose_from_points(cam, X, t=t)])
            t += 1 / 30
        assert out.tracker == "model"
        J = out.frame.joints
        sessions.append((pipe._model.world_scale, stature(J - (J[23] + J[24]) / 2)))
        for _ in range(180):  # 6 s with nobody: the body is forgotten
            pipe.process(t, None, [])
            t += 1 / 30
    (s1, h1), (s2, h2) = sessions
    assert abs(s2 / s1 - 1) < 0.01
    assert abs(h2 / h1 - 1) < 0.01


def _lunge():
    """Left foot 0.7 m in front of the right (towards +z), both planted."""
    X = standing_skeleton()
    X[[25, 27, 29, 31], 2] += [0.175, 0.35, 0.35, 0.35]
    X[[26, 28, 30, 32], 2] -= [0.175, 0.35, 0.35, 0.35]
    return X


def test_mono_joints_in_a_lunge_stand_on_the_foot_that_placed_them():
    cam = _cam_b()
    X = _lunge() + [0.1, 0.0, 0.2]
    J = mono_joints(pose_from_points(cam, X), cam)
    # the cameras sees the feet 0.7 m apart, so only the one nearer the camera (left, in front) places the body
    assert np.linalg.norm(J[27, [0, 2]] - X[27, [0, 2]]) < 0.03
    hip = lambda P: (P[23] + P[24]) / 2
    assert np.linalg.norm(hip(J)[[0, 2]] - hip(X)[[0, 2]]) < 0.05


def test_floor_feet_says_which_joints_placed_the_person():
    from vision.select import floor_feet, floor_position

    cam = _cam_b()
    p = pose_from_points(cam, _lunge())
    xz, used = floor_feet(p, cam)
    assert sorted(used) == [27, 29]
    np.testing.assert_allclose(xz, floor_position(p, cam))
    p = pose_from_points(cam, standing_skeleton())
    assert sorted(floor_feet(p, cam)[1]) == [27, 28, 29, 30]  # both planted: both feet


def _tilted_world_pose(cam, X, deg, t=0.0):
    """2D from the upright body X, but MediaPipe's world shape tilted `deg` toward the camera about the hips."""
    p = pose_from_points(cam, X, t=t)
    c = np.asarray(cam.R).T @ -np.ravel(cam.t)  # camera centre
    d = np.array([c[0], 0.0, c[2]]) / np.hypot(c[0], c[2])
    axis = np.cross([0.0, 1.0, 0.0], d)
    a = np.radians(deg)
    Kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + np.sin(a) * Kx + (1 - np.cos(a)) * Kx @ Kx  # top of the body toward the camera
    hip = (X[23] + X[24]) / 2
    p.world = ((X - hip) @ R.T) @ np.asarray(cam.R).T
    return p


def test_one_camera_body_is_upright_when_mediapipe_leans_toward_the_camera():
    from vision.checks import body_tilt_deg

    cam, X = _cam_b(), standing_skeleton()
    assert body_tilt_deg(np.stack([world_shape(_tilted_world_pose(cam, X, 8.0), cam)] * 10)) > 7.5
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X - (X[23] + X[24]) / 2))
    seq = []
    for k in range(120):
        out = pipe.process(k / 30, None, [_tilted_world_pose(cam, X, 8.0, t=k / 30)])
        if out.tracker == "model":
            seq.append(out.frame.joints)
    assert len(seq) > 60
    assert body_tilt_deg(np.array(seq)) < 2.0
    assert pipe._model.upright is not None
    pipe._model.reset()
    assert pipe._model.upright is None


def _bent_lean(deg_knee_off=25.0, lean_deg=8.0):
    """A standing body with slightly bent knees (as MediaPipe's world shape looks), tipped lean_deg toward +z."""
    X = standing_skeleton()
    for knee, ankle in ((25, 27), (26, 28)):
        X[[ankle, ankle + 2, ankle + 4]] += [0.0, 0.0, -0.18 * np.sin(np.radians(deg_knee_off))]
    a = np.radians(lean_deg)
    R = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    return X @ R.T


def test_upright_rotation_needs_enough_standing_frames_and_is_capped():
    from vision.mono import UPRIGHT_MAX_DEG, UPRIGHT_MIN_FRAMES, upright_rotation

    lean = _bent_lean()  # knees ~155°, like MediaPipe: still counts as standing
    assert upright_rotation([lean] * (UPRIGHT_MIN_FRAMES - 1)) is None  # too few frames: no correction
    U = upright_rotation([lean] * UPRIGHT_MIN_FRAMES)
    assert U is not None
    v = U @ ((lean[11] + lean[12]) / 2 - (lean[27] + lean[28]) / 2)
    assert np.degrees(np.arctan2(np.hypot(v[0], v[2]), v[1])) < 1.0  # straightened
    big = _bent_lean(lean_deg=40.0)

    def tilt(v):
        return np.degrees(np.arctan2(np.hypot(v[0], v[2]), v[1]))

    line = (big[11] + big[12]) / 2 - (big[27] + big[28]) / 2
    left = tilt(upright_rotation([big] * 20) @ line)
    assert abs(left - (tilt(line) - UPRIGHT_MAX_DEG)) < 0.5  # only UPRIGHT_MAX_DEG taken out


def test_one_camera_depth_shake_is_held_when_the_person_is_still_in_the_picture():
    import vision.tracker as T

    cam, X = _cam_b(), standing_skeleton()
    h = stature(X - (X[23] + X[24]) / 2)
    rng = np.random.default_rng(3)

    def run():
        pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
        outs = []
        for k in range(150):
            v = pose_from_points(cam, X, t=k / 30, noise_px=0.3, rng=rng)
            v.world = v.world + rng.normal(0, 0.03, v.world.shape)  # MediaPipe's depth wobble
            outs.append(pipe.process(k / 30, None, v).frame.joints)
        J = np.array(outs[80:])
        return np.nanmedian(np.linalg.norm(np.diff(J, axis=0), axis=2))

    gated = run()
    full, T.GATE_MIN_GAIN = T.GATE_MIN_GAIN, 1.0  # gate off
    try:
        ungated = run()
    finally:
        T.GATE_MIN_GAIN = full
    assert gated < 0.6 * ungated


def test_one_camera_real_movement_still_comes_through():
    cam, X0 = _cam_b(), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    errs = []
    for k in range(150):
        X = X0 + [0.01 * max(k - 80, 0), 0.0, 0.0]  # walks sideways 30 cm/s after measuring
        out = pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30))
        if k > 100:
            errs.append(np.linalg.norm((out.frame.joints[23] + out.frame.joints[24]) / 2 - (X[23] + X[24]) / 2))
    assert np.median(errs) < 0.05


def test_one_camera_catches_up_quickly_when_the_person_comes_back():
    cam, X0 = _cam_b(), standing_skeleton()
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=stature(X0 - (X0[23] + X0[24]) / 2))
    t = 0.0
    for _ in range(60):
        pipe.process(t, None, pose_from_points(cam, X0, t=t))
        t += 1 / 30
    for _ in range(16):  # nobody: lost
        assert pipe.process(t, None, None).frame.tracking == "lost"
        t += 1 / 30
    c = np.asarray(cam.R).T @ -np.ravel(cam.t)
    X = X0 + np.array([c[0], 0.0, c[2]]) / np.hypot(c[0], c[2]) * 1.0  # back 1 m nearer the camera
    errs = []
    for _ in range(5):
        out = pipe.process(t, None, pose_from_points(cam, X, t=t))
        t += 1 / 30
        errs.append(np.linalg.norm((out.frame.joints[23] + out.frame.joints[24]) / 2 - (X[23] + X[24]) / 2))
    assert errs[-1] < 0.05, errs  # within 5 frames


def _rot_x(deg):
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def _tilt_deg(v):
    return np.degrees(np.arctan2(np.hypot(v[0], v[2]), v[1]))


def test_upright_rotation_ignores_frames_bent_at_the_waist():
    from vision.mono import upright_rotation

    stand = standing_skeleton()  # knees straight, shoulders over the ankles
    bent = stand.copy()
    hip = (stand[23] + stand[24]) / 2
    upper = list(range(23))  # face, shoulders, arms, hands
    bent[upper] = (stand[upper] - hip) @ _rot_x(40.0).T + hip  # bent 40° forward at the waist, knees straight
    lean = _rot_x(8.0)
    shapes = [stand @ lean.T] * 20 + [bent @ lean.T] * 20
    U = upright_rotation(shapes)
    assert U is not None
    line = (stand[11] + stand[12]) / 2 - (stand[27] + stand[28]) / 2
    assert _tilt_deg(lean @ line) > 7.5
    assert _tilt_deg(U @ lean @ line) < 1.5  # the 8° lean taken out; the bent frames ignored


def test_upright_rotation_gives_up_when_the_standing_lean_is_scattered():
    from vision.mono import upright_rotation

    shapes = [_bent_lean(lean_deg=d) for d in np.linspace(-15.0, 15.0, 31)]
    assert upright_rotation(shapes) is None


def _toward_camera(cam):
    """Horizontal unit vector from the mat centre toward the camera."""
    c = np.asarray(cam.R).T @ -np.ravel(cam.t)
    return np.array([c[0], 0.0, c[2]]) / np.hypot(c[0], c[2])


def _walk_toward_camera_errors(speed):
    cam, X0 = _cam_b(), standing_skeleton()
    h = stature(X0 - (X0[23] + X0[24]) / 2)
    pipe = Pipeline({"B": cam}, mat=MatConfig(), height_m=h)
    d = _toward_camera(cam)
    errs = []
    for k in range(120):
        X = X0 + d * speed * max(k - 60, 0) / 30  # walks toward the camera for 2 s after measuring
        out = pipe.process(k / 30, None, pose_from_points(cam, X, t=k / 30))
        if k > 60:
            errs.append(np.linalg.norm((out.frame.joints[23] + out.frame.joints[24]) / 2 - (X[23] + X[24]) / 2))
    return np.median(errs)


def test_one_camera_walking_toward_the_camera_keeps_up():
    assert _walk_toward_camera_errors(0.5) < 0.08
    assert _walk_toward_camera_errors(1.2) < 0.12
