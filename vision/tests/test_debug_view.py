import numpy as np

from vision.config import MatConfig
from vision.debug_view import FRONT, TOP, compose_view
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_top_view_orientation():
    c = TOP(np.array([[0.0, 0.0]]))[0]
    left = TOP(np.array([[1.0, 0.0]]))[0]  # +x (patient's left) is drawn to the left from above
    front = TOP(np.array([[0.0, 1.0]]))[0]  # +z (Mira) is drawn at the top
    assert left[0] < c[0] and front[1] < c[1]


def test_front_view_orientation():
    c = FRONT(np.array([[0.0, 1.0]]))[0]
    assert FRONT(np.array([[0.5, 1.0]]))[0][0] > c[0]  # Mira's right is drawn to the right
    assert FRONT(np.array([[0.0, 1.5]]))[0][1] < c[1]


def test_compose_view_renders_with_and_without_images():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    out = Pipeline(cams).process(1.0, a, b, 0.004)
    stats = {"A": {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": "camera A delivers 640x480"},
             "B": {"fps": 29.0, "pose_ms": 21.0, "error": None, "warning": None}}
    skew = {"median_ms": 4.0, "p95_ms": 9.0, "unpaired_pct": 0.0}
    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, {"A": a, "B": b}, out, stats, skew, "REC")
    assert img.shape == (720, 1280, 3)
    img = compose_view({}, MatConfig(), {}, {}, None, None, None, None)
    assert img.shape == (720, 1280, 3)


def test_compose_view_shows_camera_moved_banner():
    cams = target_cams()
    plain = compose_view(cams, MatConfig(), {}, {}, None, None, None, None)
    alarm = compose_view(cams, MatConfig(), {}, {}, None, None, None, None,
                         alarms={"A": "Camera A has moved (6 px): re-run calib_extrinsics", "B": None})
    assert (alarm[:360, :640] != plain[:360, :640]).any()
    np.testing.assert_array_equal(alarm[:360, 640:], plain[:360, 640:])


def test_compose_view_draws_tracking_area_and_ignored_people():
    from vision.types import CameraPeople

    cams, X = target_cams(), standing_skeleton()
    on = (pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    far = X + [2.6, 0.0, 0.0]
    by = (pose_from_points(cams["A"], far), pose_from_points(cams["B"], far))
    out = Pipeline(cams).process(1.0, CameraPeople("A", 1.0, [by[0], on[0]]), CameraPeople("B", 1.0, [on[1], by[1]]))
    assert out.ignored["A"] == [by[0]]
    stats = {n: {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": None, "roi": (200, 100, 1000, 700)}
             for n in ("A", "B")}
    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, out, stats, None, None)
    plain = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, out.raw, None, None, None, None)
    assert (img[:360, :640] != plain[:360, :640]).any()
    assert (img[:360, :640] == (255, 255, 0)).all(axis=2).any()  # the cyan tracking-area outline


def test_compose_view_shows_the_3d_person_and_toggles_back_to_front_view():
    from vision.avatar import AvatarView
    from vision.debug_view import render_front

    cams, X = target_cams(), standing_skeleton()
    out = Pipeline(cams).process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    view = AvatarView()
    img3d = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    view.handle_key(ord("v"))
    img2d = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    br3d, br2d = img3d[360:, 640:], img2d[360:, 640:]
    assert (br3d != br2d).any()
    b, r = br3d[..., 0].astype(int), br3d[..., 2].astype(int)
    assert (r > b + 80).sum() > 100  # the orange (left) side of the 3D body is drawn
    np.testing.assert_array_equal(img2d[:360], img3d[:360])  # camera panels unchanged
    assert br2d.shape == render_front(out.frame, []).shape


def test_view_mouse_drag_in_3d_panel_only():
    from vision.avatar import AvatarView
    from vision.debug_view import ViewMouse

    view = AvatarView()
    yaw = view.yaw_deg
    mouse = ViewMouse(view)
    mouse(1, 100, 100, 0, None)  # press outside the 3D panel (top-left camera)
    mouse(0, 160, 100, 1, None)
    assert view.yaw_deg == yaw
    mouse(1, 900, 500, 0, None)  # press inside the bottom-right panel, then drag
    mouse(0, 960, 500, 1, None)
    assert view.yaw_deg != yaw


def test_3d_view_uses_the_rig_and_says_when_it_is_measuring():
    from vision.avatar import AvatarView

    cams, X = target_cams(), standing_skeleton()
    view = AvatarView()
    pipe = Pipeline(cams)
    out = pipe.process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    assert 0 < view.rig.progress < 1


def test_3d_view_passes_confidence_and_anchoring_and_says_when_rules_are_off():
    from vision.avatar import AvatarView

    cams, X = target_cams(), standing_skeleton()
    view = AvatarView()
    seen = {}
    real = view.rig.update

    def spy(t, joints, tracking, conf=None, feet_locked=None):
        seen.update(conf=conf, feet=feet_locked)
        return real(t, joints, tracking, conf, feet_locked)

    view.rig.update = spy
    out = Pipeline(cams).process(1.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    on = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    assert seen["conf"] is out.frame.conf and seen["feet"] is out.feet_locked
    view.handle_key(ord("c"))
    off = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    assert (on[360:, 640:] != off[360:, 640:]).any()  # "body rules off" shown


def test_model_tracker_output_is_drawn_directly_and_labelled():
    from vision.avatar import AvatarView

    cams, X = target_cams(), standing_skeleton()
    pipe = Pipeline(cams, tracker="model")
    out = None
    for k in range(45):
        out = pipe.process(k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X))
    assert out.tracker == "model"
    view = AvatarView()
    called = []
    view.rig.update = lambda *a, **k: called.append(1)  # the rig must not re-pose model output
    img = compose_view(cams, MatConfig(), {}, out.raw, out, None, None, None, view=view)
    assert not called and img.shape == (720, 1280, 3)


def test_replay_outputs_can_use_the_model_tracker(tmp_path):
    from vision.record import Recorder
    from vision.replay import replay_outputs

    cams, X = target_cams(), standing_skeleton()
    path = tmp_path / "r.jsonl"
    pipe = Pipeline(cams)
    with Recorder(path, cams, {}) as rec:
        for k in range(50):
            rec.write(pipe.process(1000 + k / 30, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.0))
    outs = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False, tracker="model"))
    assert outs[-1].tracker == "model"


def test_one_camera_setup_says_the_other_panel_is_unused():
    cams = {"B": target_cams()["B"]}
    one = compose_view(cams, MatConfig(), {}, {}, None, None, None, None)
    two = compose_view(target_cams(), MatConfig(), {}, {}, None, None, None, None)
    assert one.shape == two.shape == (720, 1280, 3)
    assert (one[:360, :640] != two[:360, :640]).any()  # "not used (one-camera setup)" instead of camera A
