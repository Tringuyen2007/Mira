"""Incident snapshot → plain-language facts for the emergency call (§8.6.2–8.6.3). Rules only, no AI.

Facts come from the live ring buffer, the events the Guardian has seen and the profile cached at
session start, so building a snapshot never waits on the database.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from server.config import cfg
from server.logging_setup import get_logger
from server.notify.templates import LIMITS_SENTENCE
from server.state import PatientProfile

if TYPE_CHECKING:
    from server.agents.safety_guardian import Incident

log = get_logger("snapshot")

__all__ = ["LIMITS_SENTENCE", "base_facts", "build_snapshot", "call_variables", "context_text", "estimate_posture",
           "limits_text", "situation_short", "situation_text", "status_text"]

KNOWN_POSTURES = ("standing", "sitting", "kneeling", "lying")
POSTURE_PHRASES = {"lying": "lying down", "sitting": "sitting up", "kneeling": "kneeling", "standing": "standing up"}
EXERCISE_NAMES = {"squat": "squats", "sit_to_stand": "sit-to-stands", "heel_raise": "heel raises",
                  "lunge": "lunges", "bridge": "bridges"}

# Posture estimate from the centre of mass height (metres above the floor) and trunk lean from vertical.
LYING_COM_Y_M = 0.40
LYING_TRUNK_LEAN_DEG = 50.0
KNEELING_COM_Y_M = 0.70
POSTURE_WINDOW_S = 1.0
# Mean joint displacement between consecutive frames above which the body counts as moving.
MOVING_STEP_M = 0.02
MOVING_RECENT_S = 0.5

MAX_SITUATION_CHARS = 480
HR_MISSING = "not available"
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


# ---- helpers -------------------------------------------------------------------------------------


def _be(subj: str) -> str:
    return "are" if subj == "they" else "is"


def _have(subj: str) -> str:
    return "have" if subj == "they" else "has"


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def time_local(epoch_s: float) -> str:
    """'3:42 PM' in the configured time zone (now, if `epoch_s` is not a usable timestamp)."""
    tz = ZoneInfo(cfg.TZ_NAME)
    try:
        dt = datetime.fromtimestamp(epoch_s, tz)
    except (OverflowError, OSError, ValueError, TypeError):
        dt = datetime.now(tz)
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def estimate_posture(com_y: float, trunk_lean_deg: float) -> str:
    if com_y < LYING_COM_Y_M:
        return "lying" if abs(trunk_lean_deg) >= LYING_TRUNK_LEAN_DEG else "sitting"
    if com_y < KNEELING_COM_Y_M:
        return "kneeling"
    return "standing"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def posture_from_frames(frames: Sequence[dict[str, Any]]) -> str | None:
    """Median com_y and trunk lean over the last second of frames → posture, or None without data."""
    if not frames:
        return None
    horizon = frames[-1].get("t", 0.0) - POSTURE_WINDOW_S
    com_ys, leans = [], []
    for f in frames:
        if f.get("t", 0.0) < horizon:
            continue
        com = f.get("com") or []
        com_y = _num(com[1]) if len(com) > 1 else None
        lean = _num((f.get("angles") or {}).get("trunk_lean"))
        if com_y is not None and lean is not None:
            com_ys.append(com_y)
            leans.append(lean)
    if not com_ys:
        return None
    com_ys.sort()
    leans.sort()
    return estimate_posture(com_ys[len(com_ys) // 2], leans[len(leans) // 2])


def motion_from_frames(frames: Sequence[dict[str, Any]], since_t: float) -> tuple[bool | None, float | None]:
    """(moving now, seconds still) from joint displacement between consecutive frames after `since_t`."""
    window = [f for f in frames if f.get("t", 0.0) >= since_t and f.get("joints")]
    if len(window) < 2:
        return None, None
    last_moving_t: float | None = None
    for prev, cur in zip(window, window[1:], strict=False):
        steps = [math.dist(a, b) for a, b in zip(prev["joints"], cur["joints"], strict=False)]
        if steps and sum(steps) / len(steps) > MOVING_STEP_M:
            last_moving_t = cur["t"]
    latest_t = window[-1]["t"]
    still_from = last_moving_t if last_moving_t is not None else window[0]["t"]
    moving = last_moving_t is not None and latest_t - last_moving_t <= MOVING_RECENT_S
    return moving, round(max(0.0, latest_t - still_from), 1)


def _posture(inc: Incident, ring_frames: Sequence[dict[str, Any]]) -> tuple[str | None, bool]:
    """Engine posture if it gave one, else the ring-buffer estimate (labelled estimated)."""
    for source in (inc.last_status, inc.trigger):
        reported = (source or {}).get("posture")
        if reported in KNOWN_POSTURES:
            return reported, False
    estimated = posture_from_frames(ring_frames)
    return estimated, estimated is not None


def base_facts(inc: Incident, profile: PatientProfile) -> dict[str, Any]:
    """The facts that need nothing but the incident and the cached profile (never the ring buffer)."""
    return {
        "kind": inc.kind,
        "time_local": time_local(inc.started_t),
        "first_name": profile.first_name,
        "pronoun_subj": profile.pronoun_subj,
        "pronoun_obj": profile.pronoun_obj,
        "pronoun_poss": profile.pronoun_poss,
        "checkins": inc.checkins,
        "responses": list(inc.responses) or "none",
        "hr_before": inc.hr_before,
        "hr_now": inc.hr_now,
        "hr_limit": profile.hr_limit,
        "pain_level": inc.pain_level,
        "pain_today": profile.pain_today,
        "condition": profile.condition,
        "weeks_since_surgery": profile.weeks_since_surgery,
        "falls_30d": profile.falls_30d,
    }


def build_snapshot(inc: Incident, ring_frames: Sequence[dict[str, Any]], profile: PatientProfile) -> dict[str, Any]:
    trig = inc.trigger
    posture, estimated = _posture(inc, ring_frames)
    status = inc.last_status or {}
    moving, still_s = status.get("moving"), _num(status.get("still_s"))
    if moving is None or still_s is None:
        f_moving, f_still = motion_from_frames(ring_frames, inc.started_t)
        moving = f_moving if moving is None else moving
        still_s = f_still if still_s is None else still_s
    on_mat = status.get("on_mat", trig.get("on_mat"))
    return {
        **base_facts(inc, profile),
        "exercise_id": trig.get("exercise_id"),
        "set": trig.get("set"),
        "rep": trig.get("rep"),
        "drop_m": _num(trig.get("drop_m")),
        "peak_down_speed_mps": _num(trig.get("peak_down_speed_mps")),
        "posture": posture,
        "posture_estimated": estimated,
        "moving": moving if isinstance(moving, bool) else None,
        "still_s": int(round(still_s)) if still_s is not None else None,
        "on_mat": on_mat if isinstance(on_mat, bool) else None,
    }


# ---- sentences -----------------------------------------------------------------------------------


def _place(s: dict[str, Any]) -> str:
    return "on the mat" if s.get("on_mat") else "on the floor"


def posture_phrase(s: dict[str, Any]) -> str | None:
    posture = s.get("posture")
    if posture not in POSTURE_PHRASES:
        return None
    return f"{POSTURE_PHRASES[posture]} {_place(s)}" if posture != "standing" else POSTURE_PHRASES[posture]


def _during(s: dict[str, Any]) -> str:
    poss = s["pronoun_poss"]
    exercise_id = s.get("exercise_id")
    if not exercise_id:
        return f"during {poss} exercises"
    name = EXERCISE_NAMES.get(exercise_id, str(exercise_id).replace("_", " "))
    return f"during set {s['set']} of {poss} {name}" if s.get("set") else f"during {poss} {name}"


def _posture_sentence(s: dict[str, Any]) -> str:
    subj, poss = s["pronoun_subj"], s["pronoun_poss"]
    phrase = posture_phrase(s)
    if phrase is None:
        lead, bare = f"Mira can't tell {poss} position now, but {subj}", f"Mira can't tell {poss} position now."
    else:
        verb = f"{_be(subj)} now" if not s.get("posture_estimated") else \
            f"{'appear' if subj == 'they' else 'appears'} to be"
        lead = f"{_cap(subj)} {verb} {phrase} and"
        bare = f"{_cap(subj)} {verb} {phrase}."
    if s.get("moving") is True:
        return f"{lead} {_be(subj)} moving."
    if s.get("still_s"):
        return f"{lead} {_have(subj)} barely moved for {s['still_s']} seconds."
    return bare


def _checkin_sentence(s: dict[str, Any]) -> str:
    subj, obj, poss = s["pronoun_subj"], s["pronoun_obj"], s["pronoun_poss"]
    asked = f"Mira asked {obj} {_times(max(1, s['checkins']))} to give a thumbs up or raise {poss} hand"
    return f"{asked}, and {subj} {_have(subj)} not responded."


def _hr_sentence(s: dict[str, Any]) -> str:
    poss = _cap(s["pronoun_poss"])
    before, now = s.get("hr_before"), s.get("hr_now")
    if before is not None and now is not None and s["kind"] == "fall":
        return f"{poss} heart rate was {before} just before and is {now} now."
    if now is not None:
        return f"{poss} heart rate is {now} now."
    return f"{poss} heart rate is {HR_MISSING}."


def situation_text(s: dict[str, Any]) -> str:
    """3–4 sentences, spoken word for word as the end of the opening message."""
    name, poss = s["first_name"], s["pronoun_poss"]
    when = f"At {s['time_local']}, {_during(s)},"
    if s["kind"] == "pain":
        level = f" of {s['pain_level']} out of 10" if s.get("pain_level") is not None else ""
        first = f"{when} {name} reported severe pain{level} on {poss} phone, and Mira paused the session."
    elif s["kind"] == "hr":
        first = (f"{when} {name}'s heart rate stayed above {poss} limit of {s['hr_limit']} for more than "
                 f"5 seconds, and Mira paused the session.")
    else:
        drop = f", about {s['drop_m']:.1f} meters in under a second" if s.get("drop_m") else ""
        first = f"{when} Mira's cameras saw {name} drop quickly to the floor{drop}."
    sentences = [first, _posture_sentence(s), _checkin_sentence(s), _hr_sentence(s)]
    if s["kind"] != "fall" and posture_phrase(s) is None:
        del sentences[1]  # for pain/HR the position only matters when Mira can see it
    text = " ".join(sentences)
    if len(text) > MAX_SITUATION_CHARS and len(sentences) == 4:
        del sentences[-1 if s["kind"] == "fall" else 1]
        text = " ".join(sentences)
    count = len(_SENTENCE_END.findall(text))
    if not 3 <= count <= 4 or "None" in text:
        raise ValueError(f"situation text failed validation ({count} sentences): {text!r}")
    return text


def situation_short(s: dict[str, Any]) -> str:
    name, subj, poss = s["first_name"], s["pronoun_subj"], s["pronoun_poss"]
    tail = f"and {subj} {'haven' if subj == 'they' else 'hasn'}'t responded."
    if s["kind"] == "pain":
        return f"{name} reported severe pain at {s['time_local']} {tail}"
    if s["kind"] == "hr":
        return f"{name}'s heart rate went above {poss} limit at {s['time_local']} {tail}"
    return f"Mira saw {name} fall at {s['time_local']} {tail}"


def status_text(s: dict[str, Any]) -> str:
    """The `current_status` variable, e.g. "Still lying down on the mat, not moving. No response yet."."""
    phrase = posture_phrase(s)
    if s.get("responses") not in (None, "none", []):
        return f"Responded. {_cap(phrase)}." if phrase else "Responded."
    moving = s.get("moving")
    if phrase is None:
        body = {True: "Moving. ", False: "Not moving. "}.get(moving, "")
    elif moving is True:
        body = f"{_cap(phrase)}, moving. "
    elif moving is False:
        body = f"Still {phrase}, not moving. "
    else:
        body = f"{_cap(phrase)}. "
    return f"{body}No response yet."


def context_text(inc: Incident) -> str:
    """Condition, weeks since surgery, today's pain and recent falls, from the cached profile."""
    p = inc.profile
    parts: list[str] = []
    if p.condition:
        weeks = p.weeks_since_surgery
        ago = "" if weeks is None else (" this week" if weeks == 0 else
                                        f" {weeks} week{'s' if weeks != 1 else ''} ago")
        parts.append(f"Recovering from a {p.condition}{ago}.")
    if p.pain_today is not None:
        parts.append(f"{_cap(p.pronoun_subj)} rated {p.pronoun_poss} pain {p.pain_today} out of 10 today.")
    if p.falls_30d == 0:
        parts.append("No falls in the last month.")
    elif p.falls_30d:
        parts.append(f"{p.falls_30d} other fall{'s' if p.falls_30d != 1 else ''} in the last month.")
    return " ".join(parts) or "No other context is available."


def limits_text(profile: PatientProfile) -> str:
    subj = profile.pronoun_subj
    return LIMITS_SENTENCE.replace("whether she is", f"whether {subj} {_be(subj)}")


def _str(value: str | None, fallback: str) -> str:
    return str(value) if value not in (None, "") else fallback


def _safe(name: str, build: Callable[[], str], fallback: str) -> str:
    try:
        text = build()
    except Exception:  # noqa: BLE001 - a template bug must never stop the emergency call
        log.exception("call variable failed, using fallback", extra={"variable": name})
        return fallback
    return text if isinstance(text, str) and text else fallback


def call_variables(inc: Incident, contact_name: str | None) -> dict[str, str]:
    """Retell dynamic variables (§8.6.3). Total: every value is a non-empty string, whatever the snapshot holds
    (even empty, when building it failed); no `None` ever reaches a call."""
    p = inc.profile
    s = {**base_facts(inc, p), **inc.snapshot}
    name = _str(p.first_name, "the patient")
    short = _safe("situation_short", lambda: situation_short(s),
                  f"Mira detected a possible emergency for {name} at {time_local(inc.started_t)}.")
    return {
        "contact_name": _str(contact_name, "there"),
        "patient_first_name": name,
        "patient_phone": _str(p.phone, f"the number you have for {p.pronoun_obj}"),
        "situation": _safe("situation", lambda: situation_text(s),
                           f"{short} Mira asked {p.pronoun_obj} to respond and got no answer."),
        "situation_short": short,
        "current_status": _safe("current_status", lambda: status_text(s), "No response yet."),
        "context": _safe("context", lambda: context_text(inc), "No other context is available."),
        "limits": _safe("limits", lambda: limits_text(p), LIMITS_SENTENCE),
        "therapist_name": _str(p.therapist_name, "the care team"),
        "therapist_phone": _str(p.therapist_phone, "not available"),
    }
