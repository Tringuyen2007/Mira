import numpy as np

from vision.filters import OneEuroFilter


def test_constant_input_stays_constant():
    f = OneEuroFilter()
    for k in range(30):
        out = f(np.array([1.5, -2.0]), k / 30)
    np.testing.assert_allclose(out, [1.5, -2.0])


def test_step_is_smoothed_then_converges():
    f = OneEuroFilter()
    f(np.array([0.0]), 0.0)
    first = f(np.array([1.0]), 1 / 30)[0]
    assert 0.0 < first < 1.0
    for k in range(2, 60):
        out = f(np.array([1.0]), k / 30)[0]
    assert out > 0.99


def test_nan_does_not_poison_other_coords_and_restarts():
    f = OneEuroFilter()
    f(np.array([0.0, 0.0]), 0.0)
    out = f(np.array([np.nan, 1.0]), 1 / 30)
    assert np.isnan(out[0]) and np.isfinite(out[1])
    out = f(np.array([5.0, 1.0]), 2 / 30)
    assert out[0] == 5.0


def test_non_increasing_time_returns_previous_output():
    f = OneEuroFilter()
    f(np.array([1.0]), 1.0)
    prev = f(np.array([2.0]), 1.1)
    same = f(np.array([9.0]), 1.1)
    np.testing.assert_array_equal(same, prev)
    assert np.isfinite(f(np.array([9.0]), 1.05)).all()
