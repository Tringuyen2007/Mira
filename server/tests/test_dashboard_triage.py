from __future__ import annotations

from datetime import UTC, datetime

from server.dashboard_triage import Signals, triage

BASE = dict(open_alert_kind=None, last_pain_post=3, prev_pain_post=3, last_effort=[5], flagged_reps_last=0,
            flagged_reps_prev=0, reps_last=12, top_flag_last=None, memory=[], missed_sessions=0,
            last_session_at=datetime(2026, 10, 11, 9, 10, tzinfo=UTC), live_now=False)


def sig(**over) -> Signals:
    return Signals(**{**BASE, **over})


def test_open_alert_wins() -> None:
    t = triage(sig(open_alert_kind="fall", last_pain_post=8))
    assert (t.bucket, t.kind) == ("needs_you", "alert")
    assert t.reason == "Fall detected, not resolved yet"


def test_pain_seven_or_more_needs_you_with_change_as_evidence() -> None:
    t = triage(sig(last_pain_post=7, prev_pain_post=3))
    assert (t.bucket, t.kind, t.evidence) == ("needs_you", "pain", "Pain 3 → 7")
    assert t.reason == "Reported pain 7 out of 10 after the last session"


def test_compensation_memory_needs_you() -> None:
    mem = [{"kind": "compensation", "message": "Bending deeper, but taking weight off her right leg",
            "evidence": {"load_from": 47.0, "load_to": 36.0, "side": "right"}, "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind) == ("needs_you", "compensation")
    assert t.reason == "Bending deeper, but taking weight off her right leg"
    assert t.evidence == "Right leg 47% → 36%"


def test_missed_three_needs_you() -> None:
    t = triage(sig(missed_sessions=3))
    assert (t.bucket, t.reason, t.evidence) == ("needs_you", "Missed 3 sessions in a row", "0 of 3 sessions done")


def test_flags_and_effort_watch() -> None:
    t = triage(sig(flagged_reps_last=4, flagged_reps_prev=1, top_flag_last="knee_cave_r"))
    assert (t.bucket, t.kind, t.evidence) == ("watch", "flags", "Flagged reps 1 → 4")
    assert t.reason == "Knee drifting inward on 4 of 12 reps"
    e = triage(sig(last_effort=[8, 8, 9]))
    assert (e.bucket, e.kind) == ("watch", "effort")
    assert e.reason == "Rated effort “hard” 3 sessions running"


def test_on_track_uses_progress_memory_else_steady() -> None:
    mem = [{"kind": "depth_gain", "message": "Reached the 110° knee bend goal", "evidence": {"gain_deg": 6},
            "time": "2026-10-11T07:45:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind, t.evidence) == ("on_track", "progress", "Depth +6° this week")
    assert triage(sig()).kind == "steady"


def test_severity_orders_buckets() -> None:
    assert triage(sig(open_alert_kind="hr")).severity > triage(sig(flagged_reps_last=4)).severity > triage(sig()).severity


def test_compensation_real_insight_evidence_uses_affected_side() -> None:
    """Insight's _compensation builder (server/agents/insight.py) writes affected_load_pct as an oldest-first
    list and never a `side` key; the patient's affected_side (Task 8) supplies the leg label."""
    mem = [{"kind": "compensation",
            "message": "Affected-leg load fell 47% → 36% over 6 session days while squat depth rose 80° → 92°. "
                       "Possible compensation.",
            "evidence": {"affected_load_pct": [47.0, 36.0], "p95_knee": [80.0, 92.0], "pain_post": [2, 3],
                        "depth_change": 12.0, "load_change": -11.0},
            "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem, affected_side="right"))
    assert (t.bucket, t.kind) == ("needs_you", "compensation")
    assert t.evidence == "Right leg 47% → 36%"


def test_asymmetry_real_insight_evidence_uses_affected_side() -> None:
    """Insight's _asymmetry builder writes avg_asymmetry as a list, no `from`/`to` scalar keys."""
    mem = [{"kind": "asymmetry_worse",
            "message": "Left-right load asymmetry in squat rose from 3 to 7 points over 5 session days.",
            "evidence": {"avg_asymmetry": [3.0, 7.0], "session_days": 5, "days": 14},
            "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem, affected_side="left"))
    assert (t.bucket, t.kind) == ("needs_you", "asymmetry")
    assert t.evidence == "Left leg asymmetry 3% → 7%"
