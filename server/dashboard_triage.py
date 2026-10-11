"""Therapist dashboard caseload triage (dashboard spec 2026-10-11).

Pure decision logic for `GET /caseload`: given the signals the database layer (Task 8) gathers for one patient,
pick the caseload bucket ("needs_you" / "watch" / "on_track"), a severity for sorting, a one-line reason and an
evidence string. No I/O here — callers assemble `Signals` from the DB and format `Triage` for the response.

Rule order follows the spec's "Triage rules (GET /caseload)" section; the first matching rule wins:

- needs_you: open alert (fall/pain/hr) > pain_post >= 7 > unacknowledged compensation/asymmetry_worse memory
  event > 3+ missed sessions.
- watch: unacknowledged pain memory event (below 7) > >= 3 flagged reps in the last session > newest 3 efforts
  all >= 8.
- on_track: a progress memory event (depth_gain) as the reason, else "steady".

Memory event evidence dicts come from two sources that don't always agree on key names: the Insight agent
(server/agents/insight.py) writes `compensation` evidence with `affected_load_pct` (a list, oldest-first, no
side label) and `asymmetry_worse` evidence with `avg_asymmetry` (also a list); `depth_gain` evidence carries
`gain_deg`, which matches this module's test fixtures directly. The Task 7 brief's tests instead use
`load_from`/`load_to`/`side` for compensation and `gain_deg` for depth_gain. This module accepts both shapes for
compensation/asymmetry (explicit scalar keys first, then the list-based Insight keys as a fallback) since a
later task may enrich evidence with the patient's affected side before calling `triage()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

Bucket = Literal["needs_you", "watch", "on_track"]

SEVERITY: dict[Bucket, int] = {"needs_you": 3, "watch": 2, "on_track": 1}

ALERT_REASONS: dict[str, str] = {
    "fall": "Fall detected, not resolved yet",
    "pain": "Pain alert, not resolved yet",
    "hr": "Heart rate alert, not resolved yet",
}

FLAG_PHRASES: dict[str, str] = {
    "knee_cave_l": "Knee drifting inward",
    "knee_cave_r": "Knee drifting inward",
    "knees_out": "Knee drifting inward",
    "trunk_lean": "Leaning forward",
    "weight_shift": "Shifting weight off the affected leg",
    "depth_more": "Not reaching depth",
}
DEFAULT_FLAG_PHRASE = "Form flagged"

PAIN_NEEDS_YOU_MIN = 7
FLAGGED_REPS_WATCH_MIN = 3
EFFORT_HARD_MIN = 8
EFFORT_STREAK = 3
MISSED_NEEDS_YOU_MIN = 3

NEEDS_YOU_MEMORY_KINDS = frozenset({"compensation", "asymmetry_worse"})
PAIN_MEMORY_KIND = "pain"
PROGRESS_MEMORY_KINDS = frozenset({"depth_gain", "milestone"})


@dataclass(frozen=True)
class Signals:
    open_alert_kind: str | None            # "fall" | "pain" | "hr" (latest open alert)
    last_pain_post: int | None             # last finished session
    prev_pain_post: int | None             # the one before
    last_effort: list[int]                 # newest first, up to 3
    flagged_reps_last: int                 # flagged reps in the last session
    flagged_reps_prev: int
    reps_last: int
    top_flag_last: str | None              # most common flag in the last session, e.g. "knee_cave_r"
    memory: list[dict[str, Any]]           # unacknowledged memory_events, newest first: {kind, message, evidence, time}
    missed_sessions: int                   # consecutive expected sessions missed (0 if none)
    last_session_at: datetime | None
    live_now: bool


@dataclass(frozen=True)
class Triage:
    bucket: Bucket
    severity: int                          # 3 needs_you, 2 watch, 1 on_track; higher sorts first
    reason: str
    evidence: str
    kind: str                              # rule id: alert|pain|compensation|asymmetry|missed|flags|effort|progress|steady


def _fmt_num(value: Any) -> str:
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _first_last(values: Any) -> tuple[Any, Any] | tuple[None, None]:
    if not isinstance(values, (list, tuple)):
        return None, None
    present = [v for v in values if v is not None]
    return (present[0], present[-1]) if len(present) >= 2 else (None, None)


def _compensation_evidence(ev: dict[str, Any]) -> str:
    load_from, load_to, side = ev.get("load_from"), ev.get("load_to"), ev.get("side")
    if load_from is None or load_to is None:
        load_from, load_to = _first_last(ev.get("affected_load_pct"))
    if load_from is None or load_to is None:
        return ""
    label = f"{side.capitalize()} leg" if isinstance(side, str) and side else "Affected leg"
    return f"{label} {_fmt_num(load_from)}% → {_fmt_num(load_to)}%"


def _asymmetry_evidence(ev: dict[str, Any]) -> str:
    frm, to = ev.get("from"), ev.get("to")
    if frm is None or to is None:
        frm, to = _first_last(ev.get("avg_asymmetry"))
    if frm is None or to is None:
        return ""
    return f"Asymmetry {_fmt_num(frm)}% → {_fmt_num(to)}%"


def _progress_evidence(ev: dict[str, Any]) -> str:
    gain = ev.get("gain_deg")
    if gain is None:
        return ""
    sign = "+" if float(gain) >= 0 else ""
    return f"Depth {sign}{_fmt_num(gain)}° this week"


def _find_memory(memory: list[dict[str, Any]], kinds: frozenset[str]) -> dict[str, Any] | None:
    for event in memory:
        if event.get("kind") in kinds:
            return event
    return None


def _flag_phrase(flag: str | None) -> str:
    return FLAG_PHRASES.get(flag or "", DEFAULT_FLAG_PHRASE)


def _result(bucket: Bucket, reason: str, evidence: str, kind: str) -> Triage:
    return Triage(bucket=bucket, severity=SEVERITY[bucket], reason=reason, evidence=evidence, kind=kind)


def triage(s: Signals) -> Triage:
    # ---- needs_you --------------------------------------------------------------------------------------
    if s.open_alert_kind is not None:
        reason = ALERT_REASONS.get(s.open_alert_kind, "Alert, not resolved yet")
        return _result("needs_you", reason, "", "alert")

    if s.last_pain_post is not None and s.last_pain_post >= PAIN_NEEDS_YOU_MIN:
        reason = f"Reported pain {s.last_pain_post} out of 10 after the last session"
        evidence = f"Pain {s.prev_pain_post} → {s.last_pain_post}" if s.prev_pain_post is not None else ""
        return _result("needs_you", reason, evidence, "pain")

    worsening = _find_memory(s.memory, NEEDS_YOU_MEMORY_KINDS)
    if worsening is not None:
        kind = "compensation" if worsening.get("kind") == "compensation" else "asymmetry"
        evidence_fn = _compensation_evidence if kind == "compensation" else _asymmetry_evidence
        evidence = evidence_fn(worsening.get("evidence") or {})
        return _result("needs_you", worsening.get("message", ""), evidence, kind)

    if s.missed_sessions >= MISSED_NEEDS_YOU_MIN:
        reason = f"Missed {s.missed_sessions} sessions in a row"
        evidence = f"0 of {s.missed_sessions} sessions done"
        return _result("needs_you", reason, evidence, "missed")

    # ---- watch --------------------------------------------------------------------------------------------
    low_pain = _find_memory(s.memory, frozenset({PAIN_MEMORY_KIND}))
    if low_pain is not None:
        return _result("watch", low_pain.get("message", ""), "", "pain")

    if s.flagged_reps_last >= FLAGGED_REPS_WATCH_MIN:
        reason = f"{_flag_phrase(s.top_flag_last)} on {s.flagged_reps_last} of {s.reps_last} reps"
        evidence = f"Flagged reps {s.flagged_reps_prev} → {s.flagged_reps_last}"
        return _result("watch", reason, evidence, "flags")

    if len(s.last_effort) >= EFFORT_STREAK and all(e >= EFFORT_HARD_MIN for e in s.last_effort[:EFFORT_STREAK]):
        reason = f"Rated effort “hard” {EFFORT_STREAK} sessions running"
        return _result("watch", reason, "", "effort")

    # ---- on_track -------------------------------------------------------------------------------------------
    progress = _find_memory(s.memory, PROGRESS_MEMORY_KINDS)
    if progress is not None:
        evidence = _progress_evidence(progress.get("evidence") or {})
        return _result("on_track", progress.get("message", ""), evidence, "progress")

    if s.last_session_at is not None:
        reason = f"Last session on {s.last_session_at.strftime('%b %-d')}"
    else:
        reason = "No sessions yet"
    return _result("on_track", reason, "", "steady")
