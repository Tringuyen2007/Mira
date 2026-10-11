from vision.calib_refine import REFINED_ZOOM_STD, refined_zoom_std


def test_refined_zoom_counts_as_measured_but_never_worse():
    assert refined_zoom_std(None) == REFINED_ZOOM_STD <= 0.03
    assert refined_zoom_std(0.08) == REFINED_ZOOM_STD
    assert refined_zoom_std(0.01) == 0.01


def test_countdown_gives_time_to_walk_to_the_mat():
    from vision.calib_refine import COUNTDOWN_S

    assert COUNTDOWN_S == 8.0
