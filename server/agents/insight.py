"""Insight agent (TRI.md §7.4) + milestones (§10 P1).

Step 1 (no AI): five SQL rules over progress_v and sessions find candidate patterns. Each candidate gets a
dedupe_key `<patient>:<kind>:<ISO week>`; keys already in memory_events are skipped.
Step 2: for each new candidate the agent (Gemini, background priority, ≤ MAX_STEPS tool steps) investigates with
read-only tools and writes one memory_events row. The message is number-checked against the stored evidence;
without Gemini (or when the check fails) the SQL-only note is written instead.
Milestones: the first day a patient's p95 knee depth reaches the prescription target → a low-severity
notification, deduped per patient + exercise + target.

Every read goes through the copilot_ro pool (`db.ro_fetch`). The only writes are writes.insert_memory_event and
the F7 notification service (which itself writes through writes.py).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from server.ai import gemini
from server.ai.gemini import AIUnavailable, FunctionCall, Reply
from server.ai.prompts import load_prompt
from server.ai.validators import validate_text
from server.db import writes
from server.db.pool import Database, DBError
from server.db.queries import TZ
from server.logging_setup import get_logger

log = get_logger("insight")

AGENT = "insight"
MAX_STEPS = 5
AI_TIMEOUT_S = 8.0
RO_TIMEOUT_S = 3.0
MAX_TOOL_ROWS = 50

MIN_CONFIDENCE = 0.7          # same data-quality bar as the §7.4 depth query
DEPTH_GAIN_DEG = 10.0         # p95 depth this week >= 3 weeks ago + 10°
TREND_WINDOW_DAYS = 14        # asymmetry / compensation trends: session days in the last 14 days ...
MIN_SESSION_DAYS = 3          # ... at least 3 of them
ASYM_MIN_RISE = 2.0           # fitted rise of avg_asymmetry over the window (load % points)
COMP_DEPTH_MIN_DEG = 5.0      # compensation: fitted depth gain >= 5° ...
COMP_LOAD_MIN_DROP = 2.0      # ... while affected-leg load falls >= 2 points
PAIN_POST_MIN = 5
PAIN_RECENT_DAYS = 7
MISSED_WINDOW_DAYS = 14
MISSED_MIN = 3
DEFAULT_KNEE_TARGET = 90.0    # squat goal when the prescription has no targets.knee_deg
MILESTONE_RECENT_DAYS = 7     # only announce a goal first reached this recently (no flood of old milestones)

HIGH_SEVERITY_KINDS = frozenset({"compensation", "pain"})
KIND_TITLES = {
    "depth_gain": "depth improving",
    "asymmetry_worse": "asymmetry rising",
    "compensation": "possible compensation",
    "pain": "pain after exercise",
    "missed_days": "missed sessions",
}

# ---- step 1: candidate rules (copilot_ro) ------------------------------------------------------------------

DEPTH_GAIN_SQL = """
WITH weekly AS (
  SELECT patient_id, exercise_id, time_bucket(INTERVAL '1 week', day) AS wk, max(p95_knee) AS depth
  FROM progress_v WHERE day > now() - INTERVAL '4 weeks' AND avg_confidence >= %(min_conf)s
  GROUP BY 1, 2, 3)
SELECT DISTINCT ON (a.patient_id, a.exercise_id)
       a.patient_id, a.exercise_id, a.wk AS from_week, b.wk AS to_week, a.depth AS from_deg, b.depth AS to_deg
FROM weekly a JOIN weekly b USING (patient_id, exercise_id)
WHERE b.wk = a.wk + INTERVAL '3 weeks' AND b.depth - a.depth >= %(gain)s
ORDER BY a.patient_id, a.exercise_id, b.wk DESC"""

# Trend over the session days of the window: least-squares slope × span = fitted change, so one odd session
# can't create (or hide) a pattern the way a strict day-over-day comparison would.
TREND_CTE = f"""
series AS (
  SELECT patient_id, exercise_id, day, p95_knee, affected_load_pct, avg_asymmetry,
         extract(epoch FROM day) / 86400.0 AS x
  FROM progress_v
  WHERE day >= time_bucket(INTERVAL '1 day', now(), '{TZ}') - make_interval(days => %(window)s)
    AND avg_confidence >= %(min_conf)s),
trend AS (
  SELECT patient_id, exercise_id, count(*) AS session_days,
         array_agg(to_char(day AT TIME ZONE '{TZ}', 'YYYY-MM-DD') ORDER BY day) AS days,
         array_agg(round(p95_knee::numeric, 1) ORDER BY day) AS p95_knee,
         array_agg(round(affected_load_pct::numeric, 1) ORDER BY day) AS affected_load_pct,
         array_agg(round(avg_asymmetry::numeric, 1) ORDER BY day) AS avg_asymmetry,
         regr_slope(p95_knee, x) * (max(x) - min(x)) AS depth_change,
         regr_slope(affected_load_pct, x) * (max(x) - min(x)) AS load_change,
         regr_slope(avg_asymmetry, x) * (max(x) - min(x)) AS asym_change
  FROM series GROUP BY 1, 2 HAVING count(*) >= %(min_days)s)"""

ASYMMETRY_SQL = f"""
WITH {TREND_CTE}
SELECT patient_id, exercise_id, session_days, days, avg_asymmetry, asym_change FROM trend
WHERE asym_change >= %(asym_rise)s ORDER BY patient_id, exercise_id"""

COMPENSATION_SQL = f"""
WITH {TREND_CTE},
pain AS (
  SELECT patient_id, exercise_id, to_char(started_at AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS day, max(pain_post) AS pain
  FROM sessions WHERE started_at >= now() - make_interval(days => %(window)s + 1)
  GROUP BY 1, 2, 3)
SELECT t.patient_id, t.exercise_id, t.session_days, t.days, t.p95_knee, t.affected_load_pct,
       t.depth_change, t.load_change,
       (SELECT array_agg(p.pain ORDER BY d.i) FROM unnest(t.days) WITH ORDINALITY AS d(day, i)
        LEFT JOIN pain p ON p.patient_id = t.patient_id AND p.exercise_id = t.exercise_id AND p.day = d.day)
         AS pain_post
FROM trend t
WHERE t.depth_change >= %(depth)s AND t.load_change <= -%(load)s
ORDER BY t.patient_id, t.exercise_id"""

PAIN_SQL = f"""
SELECT patient_id,
       array_agg(to_char(started_at AT TIME ZONE '{TZ}', 'YYYY-MM-DD') ORDER BY started_at) AS days,
       array_agg(exercise_id ORDER BY started_at) AS exercises,
       array_agg(pain_post ORDER BY started_at) AS pain_post,
       max(pain_post) FILTER (WHERE started_at >= now() - make_interval(days => %(recent)s)) AS max_pain_recent,
       count(*) FILTER (WHERE pain_post >= %(pain)s AND started_at >= now() - make_interval(days => %(recent)s))
         AS high_sessions
FROM sessions
WHERE started_at >= now() - make_interval(days => %(window)s) AND pain_post IS NOT NULL
GROUP BY patient_id
HAVING count(*) FILTER (WHERE pain_post >= %(pain)s AND started_at >= now() - make_interval(days => %(recent)s)) > 0
ORDER BY patient_id"""

# Prescribed days = sessions_per_week (max over active prescriptions) × window / 7, over the last 14 full local
# days (yesterday back), clipped to the patient's first session so new patients aren't "missing" history.
MISSED_SQL = f"""
WITH rx AS (
  SELECT patient_id, max((targets->>'sessions_per_week')::numeric) AS per_week
  FROM prescriptions WHERE active AND targets ? 'sessions_per_week' GROUP BY 1),
first AS (SELECT patient_id, min(started_at) AS first_at FROM sessions GROUP BY 1),
win AS (
  SELECT rx.patient_id, rx.per_week, t.today AS hi,
         greatest(t.today - make_interval(days => %(window)s),
                  time_bucket(INTERVAL '1 day', f.first_at, '{TZ}')) AS lo
  FROM rx JOIN first f USING (patient_id)
  CROSS JOIN (SELECT time_bucket(INTERVAL '1 day', now(), '{TZ}') AS today) t),
done AS (
  SELECT w.patient_id, w.per_week, round(extract(epoch FROM w.hi - w.lo) / 86400) AS window_days,
         count(DISTINCT time_bucket(INTERVAL '1 day', s.started_at, '{TZ}')) AS session_days
  FROM win w LEFT JOIN sessions s ON s.patient_id = w.patient_id AND s.started_at >= w.lo AND s.started_at < w.hi
  WHERE w.lo < w.hi
  GROUP BY 1, 2, 3)
SELECT patient_id, per_week, window_days, session_days,
       floor(per_week * window_days / 7) AS prescribed_days,
       floor(per_week * window_days / 7) - session_days AS missed
FROM done
WHERE floor(per_week * window_days / 7) - session_days >= %(missed)s
ORDER BY patient_id"""

MILESTONE_SQL = f"""
WITH target AS (
  SELECT DISTINCT ON (patient_id, exercise_id) patient_id, exercise_id,
         coalesce((targets->>'knee_deg')::numeric,
                  CASE WHEN exercise_id = 'squat' THEN %(default_target)s::numeric END) AS target
  FROM prescriptions WHERE active ORDER BY patient_id, exercise_id, version DESC),
reached AS (
  SELECT v.patient_id, v.exercise_id, t.target, min(v.day) AS first_day
  FROM progress_v v JOIN target t USING (patient_id, exercise_id)
  WHERE t.target IS NOT NULL AND v.p95_knee >= t.target AND v.avg_confidence >= %(min_conf)s
  GROUP BY 1, 2, 3)
SELECT r.patient_id, r.exercise_id, r.target, to_char(r.first_day AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS first_day,
       round(v.p95_knee::numeric, 1) AS p95_knee
FROM reached r
JOIN progress_v v ON v.patient_id = r.patient_id AND v.exercise_id = r.exercise_id AND v.day = r.first_day
WHERE r.first_day >= time_bucket(INTERVAL '1 day', now(), '{TZ}') - make_interval(days => %(recent)s)
ORDER BY r.patient_id, r.exercise_id"""

PATIENTS_SQL = "SELECT id, name, therapist_id FROM patients"
EXISTING_KEYS_SQL = "SELECT dedupe_key FROM memory_events WHERE dedupe_key = ANY(%(keys)s)"

# ---- step 2: read-only agent tools (copilot_ro, scoped to the candidate's patient) ----------------------------

FIXED_QUERIES: dict[str, str] = {
    "progress": f"""
        SELECT to_char(day AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS day, exercise_id,
               round(p95_knee::numeric, 1) AS p95_knee,
               round(affected_load_pct::numeric, 1) AS affected_load_pct,
               round(avg_asymmetry::numeric, 1) AS avg_asymmetry, round(avg_trunk_lean::numeric, 1) AS avg_trunk_lean
        FROM progress_v WHERE patient_id = %(pid)s AND day >= now() - make_interval(days => %(days)s)
        ORDER BY day, exercise_id""",
    "sessions": f"""
        SELECT to_char(started_at AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS day, exercise_id, reps_done, pain_pre,
               pain_post, effort
        FROM sessions WHERE patient_id = %(pid)s AND started_at >= now() - make_interval(days => %(days)s)
        ORDER BY started_at""",
    "form_flags": """
        SELECT rule_id, count(*) AS events FROM form_events
        WHERE patient_id = %(pid)s AND time >= now() - make_interval(days => %(days)s)
        GROUP BY rule_id ORDER BY events DESC""",
}
SUMMARY_DAYS = 21
MAX_QUERY_DAYS = 90

GET_SUMMARY_TOOL = {
    "name": "get_patient_summary",
    "description": "Affected side, daily progress, sessions (pain, reps) and form-flag counts for the last 21 days.",
    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]},
}
WRITE_NOTE_TOOL = {
    "name": "write_note",
    "description": "Final step: the note for the therapist (at most 3 short factual sentences, numbers from the data).",
    "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
}
FREE_SQL_TOOL = {
    "name": "run_sql",
    "description": "Run one read-only SELECT over progress_v, sessions, reps, form_events, memory_events, patients, "
                   "prescriptions (max 200 rows).",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
}
FIXED_SQL_TOOL = {
    "name": "run_sql",
    "description": "Run a fixed read-only query for this patient: progress (daily depth/load/asymmetry/trunk lean), "
                   "sessions (pain, reps, effort) or form_flags (counts per rule), over the last `days` days.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "enum": sorted(FIXED_QUERIES)},
        "days": {"type": "integer", "minimum": 1, "maximum": MAX_QUERY_DAYS}}, "required": ["query"]},
}


def _sql_guard() -> Any | None:
    """F4's sql_guard.check_sql when that feature is present, else None (fixed query set)."""
    try:
        from server.agents import sql_guard  # type: ignore[attr-defined]
    except ImportError:
        return None
    return getattr(sql_guard, "check_sql", None)


def tools() -> list[dict[str, Any]]:
    return [GET_SUMMARY_TOOL, FREE_SQL_TOOL if _sql_guard() else FIXED_SQL_TOOL, WRITE_NOTE_TOOL]


# ---- notifications (F7) ------------------------------------------------------------------------------------

async def notify(**kwargs: Any) -> int | None:
    """server.notify.notifications.create(...) when F7 is present; a no-op returning None otherwise. Never raises."""
    try:
        from server.notify import notifications  # type: ignore[attr-defined]
    except ImportError:
        return None
    create = getattr(notifications, "create", None)
    if create is None:
        return None
    try:
        return await create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never stop an agent run
        log.warning("notification failed", extra={"kind": kwargs.get("kind"), "error": repr(exc)})
        return None


# ---- helpers -------------------------------------------------------------------------------------------------

def plain(value: Any) -> Any:
    """JSON-safe copy of a DB value: Decimal/float → float rounded to 1 decimal, dates → ISO strings."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, (Decimal, float)):
        f = float(value)
        if not math.isfinite(f):
            return None
        return int(f) if f.is_integer() else round(f, 1)
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo(TZ)).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


_DATE_RE = re.compile(r"^\d{4}-(\d{2})-(\d{2})$")


def evidence_numbers(evidence: Any) -> list[float]:
    """Every number in the evidence (recursively), plus month/day of ISO dates so "Oct 9" is allowed."""
    out: list[float] = []
    if isinstance(evidence, dict):
        for v in evidence.values():
            out += evidence_numbers(v)
    elif isinstance(evidence, (list, tuple)):
        for v in evidence:
            out += evidence_numbers(v)
    elif isinstance(evidence, bool):
        pass
    elif isinstance(evidence, (int, float)):
        out.append(float(evidence))
    elif isinstance(evidence, str):
        m = _DATE_RE.match(evidence)
        if m:
            out += [float(m.group(1)), float(m.group(2))]
    return out


def iso_week(now: datetime | None = None) -> str:
    local = (now or datetime.now(ZoneInfo(TZ))).astimezone(ZoneInfo(TZ))
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def _exercise(exercise_id: str | None) -> str:
    return (exercise_id or "exercise").replace("_", " ")


def _f(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _first_last(values: list[Any]) -> tuple[Any, Any]:
    present = [v for v in values if v is not None]
    return (present[0], present[-1]) if present else (None, None)


@dataclass
class Candidate:
    patient_id: str
    kind: str
    exercise_id: str | None
    evidence: dict[str, Any]
    note: str  # the SQL-only note (P0 fallback)
    dedupe_key: str = ""

    @property
    def severity(self) -> str:
        return "high" if self.kind in HIGH_SEVERITY_KINDS else "normal"


@dataclass
class RunResult:
    candidates: int = 0
    new: int = 0
    written: int = 0
    ai_notes: int = 0
    fallback_notes: int = 0
    notifications: int = 0
    milestones: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ---- candidate builders --------------------------------------------------------------------------------------

def _depth_gain(row: dict[str, Any]) -> Candidate:
    ev = plain({"exercise_id": row["exercise_id"], "from_week": row["from_week"], "to_week": row["to_week"],
                "from_deg": row["from_deg"], "to_deg": row["to_deg"],
                "gain_deg": _f(row["to_deg"]) - _f(row["from_deg"]), "weeks": 3})
    note = (f"{_exercise(row['exercise_id']).capitalize()} depth up {ev['gain_deg']:.0f}° over 3 weeks "
            f"({ev['from_deg']:.0f}° → {ev['to_deg']:.0f}°).")
    return Candidate(row["patient_id"], "depth_gain", row["exercise_id"], ev, note)


def _asymmetry(row: dict[str, Any]) -> Candidate:
    ev = plain({"exercise_id": row["exercise_id"], "session_days": row["session_days"], "days": row["days"],
                "avg_asymmetry": row["avg_asymmetry"], "asymmetry_change": row["asym_change"]})
    first, last = _first_last(ev["avg_asymmetry"])
    note = (f"Left-right load asymmetry in {_exercise(row['exercise_id'])} rose from {first} to {last} points "
            f"over {ev['session_days']} session days.")
    return Candidate(row["patient_id"], "asymmetry_worse", row["exercise_id"], ev, note)


def _compensation(row: dict[str, Any]) -> Candidate:
    ev = plain({"exercise_id": row["exercise_id"], "session_days": row["session_days"], "days": row["days"],
                "affected_load_pct": row["affected_load_pct"], "p95_knee": row["p95_knee"],
                "pain_post": row["pain_post"], "depth_change": row["depth_change"],
                "load_change": row["load_change"]})
    l0, l1 = _first_last(ev["affected_load_pct"])
    d0, d1 = _first_last(ev["p95_knee"])
    note = (f"Affected-leg load fell {l0}% → {l1}% over {ev['session_days']} session days while "
            f"{_exercise(row['exercise_id'])} depth rose {d0}° → {d1}°. Possible compensation.")
    return Candidate(row["patient_id"], "compensation", row["exercise_id"], ev, note)


def _pain(row: dict[str, Any]) -> Candidate:
    ev = plain({"days": row["days"], "exercises": row["exercises"], "pain_post": row["pain_post"],
                "max_pain_recent": row["max_pain_recent"], "high_pain_sessions": row["high_sessions"],
                "threshold": PAIN_POST_MIN, "recent_days": PAIN_RECENT_DAYS})
    count = ev["high_pain_sessions"]
    note = (f"Pain after exercise reached {ev['max_pain_recent']}/10 in the last {PAIN_RECENT_DAYS} days "
            f"({count} session{'s' if count != 1 else ''} at {PAIN_POST_MIN} or more).")
    return Candidate(row["patient_id"], "pain", None, ev, note)


def _missed(row: dict[str, Any]) -> Candidate:
    ev = plain({"window_days": row["window_days"], "sessions_per_week": row["per_week"],
                "prescribed_days": row["prescribed_days"], "session_days": row["session_days"],
                "missed": row["missed"]})
    note = (f"{ev['missed']} of {ev['prescribed_days']} prescribed session days missed in the last "
            f"{ev['window_days']} days.")
    return Candidate(row["patient_id"], "missed_days", None, ev, note)


RULES: list[tuple[str, str, Any]] = [
    ("depth_gain", DEPTH_GAIN_SQL, _depth_gain),
    ("asymmetry_worse", ASYMMETRY_SQL, _asymmetry),
    ("compensation", COMPENSATION_SQL, _compensation),
    ("pain", PAIN_SQL, _pain),
    ("missed_days", MISSED_SQL, _missed),
]
RULE_PARAMS: dict[str, Any] = {
    "min_conf": MIN_CONFIDENCE, "gain": DEPTH_GAIN_DEG, "window": TREND_WINDOW_DAYS, "min_days": MIN_SESSION_DAYS,
    "asym_rise": ASYM_MIN_RISE, "depth": COMP_DEPTH_MIN_DEG, "load": COMP_LOAD_MIN_DROP, "pain": PAIN_POST_MIN,
    "recent": PAIN_RECENT_DAYS, "missed": MISSED_MIN,
}


# ---- fake-mode behaviour ------------------------------------------------------------------------------------

def _fake_insight(messages: list[dict[str, Any]], tools_: list[dict[str, Any]] | None) -> Reply:
    """Deterministic agent: look at the patient summary once, then write the SQL note plus the session count."""
    data = json.loads(messages[0]["content"].split("DATA:", 1)[1])["candidate"]
    summaries = [m for m in messages if m.get("role") == "tool" and m.get("name") == "get_patient_summary"]
    if not summaries:
        return Reply(function_call=FunctionCall("get_patient_summary", {"patient_id": data["patient_id"]}))
    sessions = summaries[-1]["content"].get("data", {}).get("sessions_last_21_days")
    message = data["sql_note"] + (f" {sessions} sessions in the last 21 days." if sessions is not None else "")
    return Reply(function_call=FunctionCall("write_note", {"message": message}))


gemini.register_fake(AGENT, _fake_insight)


# ---- the agent ------------------------------------------------------------------------------------------------

class InsightAgent:
    """`await run()` = one full pass (candidates → notes → milestones). Never raises DBError; reports errors."""

    def __init__(self, db: Database, *, use_ai: bool = True) -> None:
        self.db = db
        self.use_ai = use_ai

    async def _ro(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return await self.db.ro_fetch(sql, params, timeout_s=RO_TIMEOUT_S)

    # ---- step 1 ---------------------------------------------------------------------------------------

    async def find_candidates(self, week: str | None = None) -> list[Candidate]:
        week = week or iso_week()
        found: list[Candidate] = []
        for kind, sql, build in RULES:
            for row in await self._ro(sql, RULE_PARAMS):
                cand = build(row)
                cand.dedupe_key = f"{cand.patient_id}:{kind}:{week}"
                found.append(cand)
        # Patient-level keys: keep the first candidate per key (rule order, then patient/exercise order).
        unique: dict[str, Candidate] = {}
        for cand in found:
            unique.setdefault(cand.dedupe_key, cand)
        return list(unique.values())

    async def new_candidates(self, candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        rows = await self._ro(EXISTING_KEYS_SQL, {"keys": [c.dedupe_key for c in candidates]})
        existing = {r["dedupe_key"] for r in rows}
        return [c for c in candidates if c.dedupe_key not in existing]

    # ---- step 2 ---------------------------------------------------------------------------------------

    async def _tool(self, cand: Candidate, call: FunctionCall) -> dict[str, Any]:
        args = call.args or {}
        try:
            if call.name == "get_patient_summary":
                if args.get("patient_id") != cand.patient_id:
                    return {"error": "only the candidate's patient can be inspected"}
                return {"data": await self.patient_summary(cand.patient_id)}
            if call.name == "run_sql":
                return {"data": await self._run_sql(cand, args)}
        except DBError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - guard rejections etc. go back to the model as errors
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"error": f"unknown tool {call.name!r}"}

    async def patient_summary(self, patient_id: str) -> dict[str, Any]:
        params = {"pid": patient_id, "days": SUMMARY_DAYS}
        side = await self._ro("SELECT affected_side FROM patients WHERE id = %(pid)s", params)
        sessions = await self._ro(FIXED_QUERIES["sessions"], params)
        return plain({
            "patient_id": patient_id, "affected_side": side[0]["affected_side"] if side else None,
            "window_days": SUMMARY_DAYS,
            "progress": (await self._ro(FIXED_QUERIES["progress"], params))[:MAX_TOOL_ROWS],
            "sessions": sessions[:MAX_TOOL_ROWS], "sessions_last_21_days": len(sessions),
            "form_flags": (await self._ro(FIXED_QUERIES["form_flags"], params))[:MAX_TOOL_ROWS],
        })

    async def _run_sql(self, cand: Candidate, args: dict[str, Any]) -> list[Any]:
        guard = _sql_guard()
        if guard is not None and isinstance(args.get("sql"), str):
            return plain((await self._ro(guard(args["sql"])))[:MAX_TOOL_ROWS])
        query = args.get("query")
        if query not in FIXED_QUERIES:
            raise ValueError(f"query must be one of {sorted(FIXED_QUERIES)}")
        days = max(1, min(int(args.get("days") or SUMMARY_DAYS), MAX_QUERY_DAYS))
        return plain((await self._ro(FIXED_QUERIES[query], {"pid": cand.patient_id, "days": days}))[:MAX_TOOL_ROWS])

    async def investigate(self, cand: Candidate) -> tuple[str, dict[str, Any], str]:
        """(message, evidence, source) where source is "ai" or "sql". The AI message must only use numbers found
        in the evidence that is stored with it (candidate evidence + what the tools returned)."""
        evidence = dict(cand.evidence)
        if not self.use_ai:
            return cand.note, evidence, "sql"
        payload = {"candidate": {"patient_id": cand.patient_id, "kind": cand.kind, "exercise_id": cand.exercise_id,
                                 "evidence": cand.evidence, "sql_note": cand.note}}
        messages: list[dict[str, Any]] = [{"role": "user", "content": "DATA: " + json.dumps(payload)}]
        investigation: list[dict[str, Any]] = []
        replies: list[Reply] = []
        message: str | None = None
        try:
            for _ in range(MAX_STEPS):
                reply = await gemini.ask(AGENT, gemini.models.smart(), load_prompt(AGENT), messages, tools=tools(),
                                         timeout_s=AI_TIMEOUT_S, background=True)
                replies.append(reply)
                call = reply.function_call
                if call is None:
                    message = reply.text
                    break
                if call.name == "write_note":
                    message = (call.args or {}).get("message")
                    break
                messages.append(reply.as_message())
                result = await self._tool(cand, call)
                if "data" in result:
                    investigation.append({"tool": call.name, "args": call.args, "result": result["data"]})
                tool_msg: dict[str, Any] = {"role": "tool", "name": call.name, "content": result}
                if call.id:
                    tool_msg["id"] = call.id
                messages.append(tool_msg)
        except AIUnavailable as exc:
            log.info("insight AI unavailable, using SQL note", extra={"patient_id": cand.patient_id,
                                                                       "kind": cand.kind, "error": str(exc)})
            return cand.note, evidence, "sql"
        if investigation:
            evidence["investigation"] = investigation
        clean = validate_text(message, "note", evidence_numbers(evidence))
        if clean is None:
            for reply in replies:
                await gemini.discard(reply)
            log.info("insight AI note rejected, using SQL note", extra={"patient_id": cand.patient_id,
                                                                         "kind": cand.kind})
            return cand.note, dict(cand.evidence), "sql"
        return clean, evidence, "ai"

    # ---- full run -------------------------------------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        result = RunResult()
        if not self.db.has_ro:
            result.errors.append("COPILOT_DSN is not configured; Insight reads only through copilot_ro")
            log.warning("insight skipped", extra={"reason": result.errors[-1]})
            return result.as_dict()
        try:
            patients = {r["id"]: r for r in await self._ro(PATIENTS_SQL)}
            candidates = await self.find_candidates()
            fresh = await self.new_candidates(candidates)
        except DBError as exc:
            result.errors.append(str(exc))
            log.warning("insight candidate search failed", extra={"error": str(exc)})
            return result.as_dict()
        result.candidates, result.new = len(candidates), len(fresh)
        for cand in fresh:
            await self._write(cand, patients.get(cand.patient_id), result)
        try:
            result.milestones = await self.milestones(patients)
        except DBError as exc:
            result.errors.append(str(exc))
            log.warning("milestone search failed", extra={"error": str(exc)})
        log.info("insight run", extra=result.as_dict())
        return result.as_dict()

    async def _write(self, cand: Candidate, patient: dict[str, Any] | None, result: RunResult) -> None:
        message, evidence, source = await self.investigate(cand)
        evidence["note_source"] = source
        memory_id = await writes.insert_memory_event(self.db, cand.patient_id, cand.kind, message, evidence,
                                                     dedupe_key=cand.dedupe_key, agent=AGENT)
        if memory_id is None:
            return  # dedupe_key already written (race with an on-demand run) or the insert failed (logged)
        result.written += 1
        if source == "ai":
            result.ai_notes += 1
        else:
            result.fallback_notes += 1
        if cand.kind in HIGH_SEVERITY_KINDS and patient and patient.get("therapist_id"):
            nid = await notify(kind="insight", severity=cand.severity, therapist_id=patient["therapist_id"],
                               patient_id=cand.patient_id, title=f"{patient['name']}: {KIND_TITLES[cand.kind]}",
                               body=message, link=f"/dashboard#/patients/{cand.patient_id}",
                               source_table="memory_events", source_id=str(memory_id),
                               dedupe_key=f"insight:{cand.dedupe_key}")
            result.notifications += nid is not None

    async def milestones(self, patients: dict[str, dict[str, Any]]) -> int:
        rows = await self._ro(MILESTONE_SQL, {"default_target": DEFAULT_KNEE_TARGET, "min_conf": MIN_CONFIDENCE,
                                              "recent": MILESTONE_RECENT_DAYS})
        sent = 0
        for row in rows:
            patient = patients.get(row["patient_id"])
            if not patient or not patient.get("therapist_id"):
                continue
            target = plain(row["target"])
            name = patient["name"].split(" ")[0]
            nid = await notify(
                kind="milestone", severity="low", therapist_id=patient["therapist_id"], patient_id=row["patient_id"],
                title=f"{name} hit the {target}° {_exercise(row['exercise_id'])} goal",
                body=f"p95 knee depth {plain(row['p95_knee'])}° on {row['first_day']} (goal {target}°).",
                link=f"/dashboard#/patients/{row['patient_id']}", source_table="progress_v",
                source_id=f"{row['patient_id']}:{row['exercise_id']}:{row['first_day']}",
                dedupe_key=f"milestone:{row['patient_id']}:{row['exercise_id']}:{target}")
            sent += nid is not None
        return sent


async def run_once(db: Database) -> dict[str, Any]:
    """One Insight pass with the current config (used by the scheduler and POST /insight/run)."""
    return await InsightAgent(db).run()

