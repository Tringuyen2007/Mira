import numpy as np

from vision.calib_extrinsics import lock_problems, save_problems
from vision.markers import ZoomSolve


def _result(quality="measured", warns=()):
    sol = ZoomSolve(K=np.eye(3), R=np.eye(3), t=np.zeros(3), rms=0.5, quality=quality, zoom_std=None, zoom_msg="")
    return (sol, {}, None, list(warns), [])


def test_lock_needs_both_cameras_and_no_warnings():
    assert lock_problems({"A": _result(), "B": _result()}) == []
    assert lock_problems({"A": _result("assumed"), "B": _result("rough")}) == []  # zoom is remembered, so allowed
    assert any("B" in p for p in lock_problems({"A": _result()}))
    assert any("below the floor" in p for p in lock_problems({"A": _result(warns=["camera is below the floor"]),
                                                              "B": _result()}))


def test_save_allows_rough_and_assumed_zoom():
    assert save_problems({"A": _result("assumed"), "B": _result("rough")}) == []
    assert any("A" in p for p in save_problems({"B": _result()}))
    assert save_problems({"A": _result(warns=["only one marker seen: pose is unreliable"]), "B": _result()})


def test_legend_explains_every_key_and_fits_the_panel():
    from vision.calib_extrinsics import legend_lines

    ready = legend_lines(save_blockers=[], lock_blockers=[])
    text = "\n".join(t for t, _ in ready)
    for key in ("s ", "t ", "q "):
        assert any(t.startswith(key) for t, _ in ready), key
    assert "calib_refine" in text and "ready" in text
    assert all(len(t) <= 66 for t, _ in ready)
    blocked = "\n".join(t for t, _ in legend_lines(["camera B has no marker solution"], ["camera B has no marker solution"]))
    assert "not yet" in blocked and "camera B" in blocked


def test_one_camera_setup_saves_and_pads_the_layout():
    from vision.calib_extrinsics import pad_tiles

    assert save_problems({"B": _result()}, ("B",)) == []
    assert lock_problems({"B": _result()}, ("B",)) == []
    assert save_problems({}, ("B",))
    tiles = pad_tiles([np.zeros((360, 640, 3), np.uint8)])
    assert len(tiles) == 2 and np.hstack(tiles).shape == (360, 1280, 3)
