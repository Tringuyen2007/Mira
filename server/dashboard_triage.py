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

`Signals.affected_side` (Task 8) is that enrichment: it is used as the leg label whenever the memory event's
`evidence` dict has no `side` key of its own (real Insight evidence never does), e.g. "Right leg 47% → 36%".

Review I9: a memory event's `message` is Insight's AI-written note (Gemini, or a SQL-only fallback) and is
never shown here. Every reason/evidence pair this module returns for a memory-backed rule is built only from
the `evidence` dict's numbers (plus `Signals.affected_side`), by `_compensation_text` / `_asymmetry_text` /
`_pain_watch_text` / `_progress_text` below. The AI `message` stays available from `GET /patients/{id}/memory`
("Therapist Memory"), labelled as an AI draft there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable
from typing import Any, Literal
from zoneinfo import ZoneInfo

from server.db.queries import TZ

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
    affected_side: str | None = None       # fallback leg label when a memory event's evidence has no "side"


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


def _fmt_round(value: Any) -> str:
    """Whole-number formatting for percentages/degrees, matching the Figma one-liners (Minor 3)."""
    return str(round(float(value)))


def _first_last(values: Any) -> tuple[Any, Any] | tuple[None, None]:
    if not isinstance(values, (list, tuple)):
        return None, None
    present = [v for v in values if v is not None]
    return (present[0], present[-1]) if len(present) >= 2 else (None, None)


def _exercise_name(exercise_id: Any) -> str:
    return str(exercise_id or "exercise").replace("_", " ")


def _compensation_text(ev: dict[str, Any], affected_side: str | None) -> tuple[str, str]:
    """Deterministic (reason, evidence) for a compensation memory event, built only from `evidence`
    numbers and the patient's affected side — never Insight's AI-written `message` (I9)."""
    load_from, load_to, side = ev.get("load_from"), ev.get("load_to"), ev.get("side") or affected_side
    if load_from is None or load_to is None:
        load_from, load_to = _first_last(ev.get("affected_load_pct"))
    if isinstance(side, str) and side:
        reason = f"Bending deeper, but taking weight off the {side.lower()} leg"
        label = side.capitalize()
    else:
        reason = "Bending deeper, but taking weight off the affected leg"
        label = "Affected"
    if load_from is None or load_to is None:
        return reason, ""
    return reason, f"{label} leg {_fmt_round(load_from)}% → {_fmt_round(load_to)}%"


def _asymmetry_text(ev: dict[str, Any], affected_side: str | None) -> tuple[str, str]:
    """Deterministic (reason, evidence) for an asymmetry_worse memory event (I9)."""
    reason = "Leaning more onto one side"
    frm, to = ev.get("from"), ev.get("to")
    if frm is None or to is None:
        frm, to = _first_last(ev.get("avg_asymmetry"))
    if frm is None or to is None:
        return reason, ""
    return reason, f"Asymmetry {_fmt_round(frm)}% → {_fmt_round(to)}%"


def _pain_watch_text(ev: dict[str, Any]) -> tuple[str, str]:
    """Deterministic (reason, evidence) for a watch-level pain memory event (< 7), reading the real
    `_pain` evidence keys from server/agents/insight.py (I9)."""
    max_pain = ev.get("max_pain_recent")
    if max_pain is None:
        return "Pain reported after exercise this week", ""
    return f"Pain after exercise reached {_fmt_num(max_pain)}/10 this week", ""


def _progress_text(ev: dict[str, Any]) -> tuple[str, str]:
    """Deterministic (reason, evidence) for a depth_gain memory event (I9; also fixes the "this week"
    vs. "3 weeks" mismatch, Minor 3)."""
    gain = ev.get("gain_deg")
    exercise = _exercise_name(ev.get("exercise_id")).capitalize()
    if gain is None:
        return f"{exercise} depth improving", ""
    gain_s = _fmt_round(gain)
    return f"{exercise} depth up {gain_s}° over 3 weeks", f"Depth +{gain_s}°"


def _find_memory(memory: list[dict[str, Any]], kinds: frozenset[str]) -> dict[str, Any] | None:
    for event in memory:
        if isinstance(event, dict) and event.get("kind") in kinds:
            return event
    return None


def _event_text(text_fn: Callable[..., tuple[str, str]], event: dict[str, Any], fallback: str,
                *args: Any) -> tuple[str, str]:
    """`text_fn(evidence, *args)`, but one malformed memory event (evidence not an object, a non-numeric value)
    falls back to the reason-only `fallback` instead of failing the whole caseload (final review A, M2)."""
    ev = event.get("evidence")
    try:
        return text_fn(ev if isinstance(ev, dict) else {}, *args)
    except (TypeError, ValueError, AttributeError):
        return fallback, ""


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
        text_fn = _compensation_text if kind == "compensation" else _asymmetry_text
        fallback = ("Bending deeper, but taking weight off the affected leg" if kind == "compensation"
                    else "Leaning more onto one side")
        reason, evidence = _event_text(text_fn, worsening, fallback, s.affected_side)
        return _result("needs_you", reason, evidence, kind)

    if s.missed_sessions >= MISSED_NEEDS_YOU_MIN:
        reason = f"Missed {s.missed_sessions} sessions in a row"
        evidence = f"0 of {s.missed_sessions} sessions done"
        return _result("needs_you", reason, evidence, "missed")

    # ---- watch --------------------------------------------------------------------------------------------
    low_pain = _find_memory(s.memory, frozenset({PAIN_MEMORY_KIND}))
    if low_pain is not None:
        reason, evidence = _event_text(_pain_watch_text, low_pain, "Pain reported after exercise this week")
        return _result("watch", reason, evidence, "pain")

    if s.flagged_reps_last >= FLAGGED_REPS_WATCH_MIN:
        reason = f"{_flag_phrase(s.top_flag_last)} on {s.flagged_reps_last} of {s.reps_last} reps"
        evidence = f"Flagged reps {s.flagged_reps_prev} → {s.flagged_reps_last}"
        return _result("watch", reason, evidence, "flags")

    if len(s.last_effort) >= EFFORT_STREAK and all(e >= EFFORT_HARD_MIN for e in s.last_effort[:EFFORT_STREAK]):
        reason = f"Rated effort “hard” {EFFORT_STREAK} sessions running"
        evidence = f"Effort {s.last_effort[EFFORT_STREAK - 1]} → {s.last_effort[0]}"
        return _result("watch", reason, evidence, "effort")

    # ---- on_track -------------------------------------------------------------------------------------------
    progress = _find_memory(s.memory, PROGRESS_MEMORY_KINDS)
    if progress is not None:
        reason, evidence = _event_text(_progress_text, progress, "Depth improving")
        return _result("on_track", reason, evidence, "progress")

    if s.last_session_at is not None:
        local = s.last_session_at.astimezone(ZoneInfo(TZ))
        reason = f"Last session on {local.strftime('%b %-d')}"
    else:
        reason = "No sessions yet"
    return _result("on_track", reason, "", "steady")
