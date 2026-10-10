from vision.pairing import Pairer, SkewStats
from vision.types import PoseResult


def r(cam, t):
    return PoseResult(cam, t)


def test_pairs_nearest_within_skew():
    p = Pairer()
    assert p.add(r("A", 0.0)) == []
    out = p.add(r("B", 0.010))
    assert len(out) == 1 and out[0].a.t == 0.0 and out[0].b.t == 0.010
    assert abs(out[0].skew_s - 0.010) < 1e-12


def test_uses_earlier_partner_when_nearest():
    p = Pairer()
    p.add(r("B", -0.005))
    assert p.add(r("A", 0.0)) == []
    out = p.add(r("B", 0.028))
    assert out[0].b.t == -0.005


def test_skew_too_large_emits_anchor_alone_once():
    p = Pairer()
    p.add(r("A", 0.0))
    out = p.add(r("B", 0.050))
    assert len(out) == 1 and out[0].b is None and out[0].a.t == 0.0
    assert p.add(r("B", 0.060)) == []


def test_anchor_waits_then_gives_up_when_other_is_silent():
    p = Pairer()
    p.add(r("A", 0.0))
    out = p.add(r("A", 0.2))
    assert [x.a.t for x in out] == [0.0] and out[0].b is None


def test_other_passes_through_when_anchor_dead():
    p = Pairer()
    out = p.add(r("B", 1.0))
    assert len(out) == 1 and out[0].a is None and out[0].b.t == 1.0


def test_single_camera_mode_emits_immediately():
    p = Pairer(anchor="B", other=None)
    out = p.add(r("B", 0.0))
    assert len(out) == 1 and out[0].b.t == 0.0 and out[0].a is None


def test_skew_stats():
    s = SkewStats()
    p = Pairer()
    p.add(r("A", 0.0))
    for pair in p.add(r("B", 0.02)):
        s.add(pair)
    summary = s.summary()
    assert abs(summary["median_ms"] - 20) < 1e-6 and summary["unpaired_pct"] == 0
