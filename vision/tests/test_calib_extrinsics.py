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
