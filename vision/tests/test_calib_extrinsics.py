import numpy as np

from vision.calib_extrinsics import lock_problems
from vision.markers import ZoomSolve


def _result(guessed=False, warns=()):
    sol = ZoomSolve(K=np.eye(3), R=np.eye(3), t=np.zeros(3), rms=0.5, guessed=guessed, zoom_msg="")
    return (sol, {}, {}, list(warns))


def test_lock_needs_both_cameras_measured():
    assert lock_problems({"A": _result(), "B": _result()}) == []
    assert any("B" in p for p in lock_problems({"A": _result()}))
    assert any("zoom" in p for p in lock_problems({"A": _result(), "B": _result(guessed=True)}))
    assert any("below the floor" in p for p in lock_problems({"A": _result(warns=["camera is below the floor"]),
                                                              "B": _result()}))
