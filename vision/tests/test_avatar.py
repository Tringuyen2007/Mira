import numpy as np

from vision.avatar import (
    LEFT_TINT, RIGHT_TINT, AvatarView, build_mesh, legend_lines, render_avatar, shadow_points,
)
from vision.config import MatConfig
from vision.tests.synth import standing_skeleton

MAT = MatConfig()


def test_standing_skeleton_builds_a_full_low_poly_body():
    parts = {p.name for p in build_mesh(standing_skeleton())}
    assert {"head", "neck", "torso", "upper_arm_l", "forearm_r", "thigh_l", "shin_r", "hand_l", "foot_r"} <= parts
    for p in build_mesh(standing_skeleton()):
        assert all(np.isfinite(f).all() for f in p.faces) and len(p.faces) >= 4


def test_missing_joint_drops_only_its_parts():
    X = standing_skeleton()
    X[13] = np.nan  # left elbow
    parts = {p.name for p in build_mesh(X)}
    assert "upper_arm_l" not in parts and "forearm_l" not in parts
    assert {"upper_arm_r", "torso", "head", "thigh_l"} <= parts
    assert build_mesh(np.full((33, 3), np.nan)) == []


def test_front_view_shows_the_left_side_on_the_right_of_the_picture():
    view = AvatarView(yaw_deg=0.0, pitch_deg=5.0, dist_m=3.5)  # from in front of the person (Mira's side)
    img = render_avatar(standing_skeleton(), MAT, view)

    b, r = img[..., 0].astype(int), img[..., 2].astype(int)
    orange_x = np.nonzero(r > b + 80)[1]  # LEFT_TINT is orange (BGR, red-heavy) whatever the shading
    blue_x = np.nonzero(b > r + 80)[1]  # RIGHT_TINT is blue
    assert LEFT_TINT[2] > LEFT_TINT[0] and RIGHT_TINT[0] > RIGHT_TINT[2]
    assert orange_x.size > 200 and blue_x.size > 200
    assert orange_x.mean() > blue_x.mean()  # facing the person, their left is on our right


def test_shadow_lies_on_the_floor():
    pts = shadow_points(build_mesh(standing_skeleton()))
    assert len(pts) > 20 and np.allclose(pts[:, 1], 0.001)


def test_render_without_a_person_and_with_hud():
    img = render_avatar(None, MAT, AvatarView(), hud=["tracking: lost"])
    assert img.shape == (360, 640, 3) and img.any()


def test_view_keys_orbit_tilt_zoom_reset_and_toggle():
    v = AvatarView()
    yaw, pitch, dist = v.yaw_deg, v.pitch_deg, v.dist_m
    assert v.handle_key(ord("l")) and v.yaw_deg > yaw
    assert v.handle_key(ord("i")) and v.pitch_deg > pitch
    assert v.handle_key(ord("+")) and v.dist_m < dist
    for _ in range(100):
        v.handle_key(ord("i"))
    assert v.pitch_deg <= 85.0
    assert v.handle_key(ord("0")) and (v.yaw_deg, v.pitch_deg, v.dist_m) == (yaw, pitch, dist)
    assert v.show_3d and v.handle_key(ord("v")) and not v.show_3d
    assert not v.handle_key(ord("q"))  # not a view key


def test_mouse_drag_orbits():
    v = AvatarView()
    yaw = v.yaw_deg
    v.drag(dx=50, dy=0)
    assert v.yaw_deg != yaw


def test_legend_lists_every_key():
    text = " ".join(legend_lines())
    for key in ("drag", "j/l", "i/k", "+/-", "0", "v"):
        assert key in text
