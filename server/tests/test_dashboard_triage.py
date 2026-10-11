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


def test_compensation_memory_needs_you_is_deterministic_not_ai_message() -> None:
    """The reason/evidence come from `evidence` numbers and `affected_side`, never the AI `message` (I9)."""
    mem = [{"kind": "compensation", "message": "Bending deeper, but taking weight off her right leg (AI note).",
            "evidence": {"load_from": 47.0, "load_to": 36.0, "side": "right"}, "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind) == ("needs_you", "compensation")
    assert t.reason == "Bending deeper, but taking weight off the right leg"
    assert t.evidence == "Right leg 47% → 36%"


def test_compensation_unknown_side_falls_back_to_affected_leg() -> None:
    mem = [{"kind": "compensation", "message": "ai text", "evidence": {"load_from": 47.0, "load_to": 36.0},
            "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem))
    assert t.reason == "Bending deeper, but taking weight off the affected leg"
    assert t.evidence == "Affected leg 47% → 36%"


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
    assert e.evidence == "Effort 9 → 8"


def test_pain_memory_watch_is_deterministic_using_real_insight_evidence_keys() -> None:
    """server/agents/insight.py `_pain` evidence carries `max_pain_recent`, not `pain_post` alone."""
    mem = [{"kind": "pain", "message": "Pain after exercise reached 5/10 in the last 7 days (AI note).",
            "evidence": {"days": 21, "exercises": ["squat"], "pain_post": [2, 5], "max_pain_recent": 5,
                        "high_pain_sessions": 1, "threshold": 7, "recent_days": 7},
            "time": "2026-10-11T07:00:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind) == ("watch", "pain")
    assert t.reason == "Pain after exercise reached 5/10 this week"


def test_pain_memory_watch_falls_back_without_evidence_numbers() -> None:
    mem = [{"kind": "pain", "message": "ai text", "evidence": {}, "time": "2026-10-11T07:00:00Z"}]
    t = triage(sig(memory=mem))
    assert t.reason == "Pain reported after exercise this week"
    assert t.evidence == ""


def test_on_track_uses_progress_memory_else_steady() -> None:
    mem = [{"kind": "depth_gain", "message": "Reached the 110° knee bend goal (AI note)",
            "evidence": {"exercise_id": "squat", "gain_deg": 6, "weeks": 3},
            "time": "2026-10-11T07:45:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind) == ("on_track", "progress")
    assert t.reason == "Squat depth up 6° over 3 weeks"
    assert t.evidence == "Depth +6°"
    assert triage(sig()).kind == "steady"


def test_steady_reason_uses_houston_local_date_not_utc() -> None:
    """Review I4: 2026-10-11T01:53Z is Oct 10 evening in Houston (America/Chicago), not Oct 11."""
    t = triage(sig(memory=[], last_session_at=datetime(2026, 10, 11, 1, 53, tzinfo=UTC)))
    assert t.kind == "steady"
    assert t.reason == "Last session on Oct 10"


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
    assert t.reason == "Bending deeper, but taking weight off the right leg"
    assert t.evidence == "Right leg 47% → 36%"


def test_asymmetry_real_insight_evidence_uses_affected_side() -> None:
    """Insight's _asymmetry builder writes avg_asymmetry as a list, no `from`/`to` scalar keys."""
    mem = [{"kind": "asymmetry_worse",
            "message": "Left-right load asymmetry in squat rose from 3 to 7 points over 5 session days.",
            "evidence": {"avg_asymmetry": [3.0, 7.0], "session_days": 5, "days": 14},
            "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem, affected_side="left"))
    assert (t.bucket, t.kind) == ("needs_you", "asymmetry")
    assert t.reason == "Leaning more onto one side"
    assert t.evidence == "Asymmetry 3% → 7%"


def test_malformed_memory_evidence_falls_back_to_reason_only() -> None:
    """One bad memory event (evidence a list, or a non-numeric value) must not fail triage — and with it the
    whole caseload (final review A, M2)."""
    listy = [{"kind": "compensation", "evidence": ["not", "an", "object"], "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=listy, affected_side="right"))
    assert (t.bucket, t.kind, t.evidence) == ("needs_you", "compensation", "")
    assert t.reason == "Bending deeper, but taking weight off the right leg"

    bad_num = [{"kind": "asymmetry_worse", "evidence": {"from": "n/a", "to": 7}, "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=bad_num))
    assert (t.reason, t.evidence) == ("Leaning more onto one side", "")

    bad_gain = [{"kind": "depth_gain", "evidence": {"gain_deg": "lots", "exercise_id": "squat"},
                 "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=bad_gain))
    assert (t.bucket, t.reason, t.evidence) == ("on_track", "Depth improving", "")

    not_a_dict = triage(sig(memory=["junk", {"kind": "pain", "evidence": None}]))
    assert not_a_dict.bucket == "watch"
