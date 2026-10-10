"""Fake-mode Copilot model (GEMINI_MODE=fake): a deterministic stand-in for Gemini that drives the real tool
loop — real SQL through the guard on copilot_ro, real proposals — so the API, the demo cache and the evals run
without quota. It routes on keywords and builds every answer from the tool results it was given, never from
numbers of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from server.ai.gemini import FunctionCall, Reply

WINDOW_DAYS = 21
TODAY = "(now() AT TIME ZONE 'America/Chicago')::date"

_TREND = f"""
WITH w AS (
  SELECT v.patient_id, p.name, p.affected_side, v.day, v.p95_knee, v.affected_load_pct,
         row_number() OVER (PARTITION BY v.patient_id ORDER BY v.day) AS first_n,
         row_number() OVER (PARTITION BY v.patient_id ORDER BY v.day DESC) AS last_n,
         count(*) OVER (PARTITION BY v.patient_id) AS days
  FROM progress_v v JOIN patients p ON p.id = v.patient_id
  WHERE v.exercise_id = 'squat' AND {{where}}
    AND v.day >= {TODAY} - {WINDOW_DAYS})
SELECT patient_id, name, affected_side, max(days) AS days,
       round(avg(p95_knee) FILTER (WHERE first_n <= 3)::numeric, 1) AS depth_from,
       round(avg(p95_knee) FILTER (WHERE last_n <= 3)::numeric, 1) AS depth_to,
       round(avg(affected_load_pct) FILTER (WHERE first_n <= 3)::numeric, 1) AS load_from,
       round(avg(affected_load_pct) FILTER (WHERE last_n <= 3)::numeric, 1) AS load_to,
       round((avg(affected_load_pct) FILTER (WHERE first_n <= 3)
              - avg(affected_load_pct) FILTER (WHERE last_n <= 3))::numeric, 1) AS load_drop
FROM w {{filter}}
GROUP BY patient_id, name, affected_side
{{having}}
ORDER BY load_drop DESC NULLS LAST"""

COMPENSATION_SQL = _TREND.format(
    where="v.affected_load_pct IS NOT NULL", filter="WHERE days >= 6",
    having="HAVING avg(p95_knee) FILTER (WHERE last_n <= 3) - avg(p95_knee) FILTER (WHERE first_n <= 3) > 5\n"
           "   AND avg(affected_load_pct) FILTER (WHERE first_n <= 3)\n"
           "     - avg(affected_load_pct) FILTER (WHERE last_n <= 3) > 2")

MISSED_SQL = f"""
SELECT p.id AS patient_id, p.name,
       2 * (rx.targets->>'sessions_per_week')::int AS scheduled_days,
       count(DISTINCT (s.started_at AT TIME ZONE 'America/Chicago')::date) AS sessions_last_14_days,
       2 * (rx.targets->>'sessions_per_week')::int
         - count(DISTINCT (s.started_at AT TIME ZONE 'America/Chicago')::date) AS missed
FROM patients p
JOIN prescriptions rx ON rx.patient_id = p.id AND rx.exercise_id = 'squat' AND rx.active
LEFT JOIN sessions s ON s.patient_id = p.id AND s.exercise_id = 'squat'
  AND (s.started_at AT TIME ZONE 'America/Chicago')::date BETWEEN {TODAY} - 14 AND {TODAY} - 1
WHERE p.created_at <= now() - INTERVAL '14 days'
GROUP BY p.id, p.name, rx.targets
HAVING 2 * (rx.targets->>'sessions_per_week')::int
         - count(DISTINCT (s.started_at AT TIME ZONE 'America/Chicago')::date) >= 3
ORDER BY missed DESC, p.name"""


def trend_sql(where: str) -> str:
    return _TREND.format(where=where, filter="", having="")


def series_sql(patient_id: str) -> str:
    return f"""
SELECT day::date AS day, round(p95_knee::numeric, 1) AS p95_knee,
       round(affected_load_pct::numeric, 1) AS affected_load_pct
FROM progress_v WHERE patient_id = '{patient_id}' AND exercise_id = 'squat' AND day >= {TODAY} - {WINDOW_DAYS}
ORDER BY day"""


def pain_sql(patient_id: str) -> str:
    return f"""
WITH d AS (
  SELECT (started_at AT TIME ZONE 'America/Chicago')::date AS day, max(pain_post) AS pain_post
  FROM sessions
  WHERE patient_id = '{patient_id}' AND exercise_id = 'squat' AND started_at >= now() - INTERVAL '{WINDOW_DAYS} days'
  GROUP BY 1),
t AS (SELECT day, pain_post, coalesce(day - lag(day) OVER (ORDER BY day) = 1, false) AS after_training_day FROM d)
SELECT after_training_day, count(*) AS days, round(avg(pain_post), 1) AS avg_pain_post
FROM t GROUP BY after_training_day ORDER BY after_training_day"""


def load_week_sql(where: str) -> str:
    return f"""
SELECT p.name, p.affected_side, count(*) AS days, round(avg(v.affected_load_pct)::numeric, 1) AS affected_load_pct
FROM progress_v v JOIN patients p ON p.id = v.patient_id
WHERE {where} AND v.exercise_id = 'squat' AND v.day >= {TODAY} - 6
GROUP BY p.name, p.affected_side"""


PROPOSED_CHANGE = {"exercise_id": "squat", "rest_days_between": 1, "add": ["weight_shift_drill"]}

_STOP = {"which", "what", "whats", "who", "how", "has", "have", "is", "are", "show", "delete", "diagnose", "review",
         "weekly", "the", "my", "ignore", "please", "run", "drop", "i", "tell", "give", "list", "can", "does", "did",
         "of", "patient", "question", "mode", "sql", "select", "remove", "erase", "update"}
_WRITE_RE = re.compile(r"\b(delete|drop|truncate|erase|wipe|insert|update|alter)\b", re.IGNORECASE)


@dataclass
class Ctx:
    question: str
    patient_id: str | None
    mode: str
    results: list[tuple[str, dict[str, Any]]]

    def rows(self, i: int) -> list[dict[str, Any]] | None:
        """Rows of the i-th tool result, or None if that call was rejected."""
        if i >= len(self.results):
            return None
        data = self.results[i][1].get("data")
        if not isinstance(data, dict):
            return None
        return data.get("rows") if "rows" in data else [data]

    def error(self) -> str | None:
        return self.results[-1][1].get("error") if self.results else None


def _call(name: str, **args: Any) -> Reply:
    return Reply(function_call=FunctionCall(name, args))


def _final(answer: str, evidence: list[str] | None = None, charts: list[dict[str, Any]] | None = None) -> Reply:
    return _call("final_answer", answer=answer, evidence=evidence or [], charts=charts or [])


def _parse(first: str) -> tuple[str, str | None, str]:
    fields = dict(re.findall(r"^([A-Z_]+): ?(.*)$", first, re.MULTILINE))
    return fields.get("QUESTION", first), fields.get("PATIENT_ID") or None, fields.get("MODE", "ask")


def _name_in(question: str) -> str | None:
    for word in re.findall(r"\b[A-Z][a-z]+(?:'s)?\b", question):
        word = word.removesuffix("'s")
        if word.lower() not in _STOP:
            return word
    return None


def _patient_filter(ctx: Ctx) -> str | None:
    if ctx.patient_id and re.fullmatch(r"[A-Za-z0-9_.-]+", ctx.patient_id):
        return f"p.id = '{ctx.patient_id}'"
    name = _name_in(ctx.question)
    return f"p.name ILIKE '{name}%'" if name else None


def _num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _count_word(n: int) -> str:
    return ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"][n] if n <= 10 else str(n)


def _could_not(ctx: Ctx) -> Reply:
    return _final(f"I couldn't get that from the data: {ctx.error() or 'the query returned nothing'}.")


# ---- routes ------------------------------------------------------------------------------------------------

def _refuse_write(ctx: Ctx) -> Reply:
    return _final("I can't delete or change records: my database access is read-only, and plan changes only "
                  "happen through proposals you approve. I can show you the data instead.")


def _compensation(ctx: Ctx) -> Reply:
    n = len(ctx.results)
    if n == 0:
        return _call("run_sql", query=COMPENSATION_SQL)
    ranking = ctx.rows(0)
    if ranking is None:
        return _could_not(ctx)
    if not ranking:
        return _final(f"No patient shows compensation in the last {WINDOW_DAYS} days (squat depth improving while "
                      "load on the affected leg drops).", [f"progress_v, squat, last {WINDOW_DAYS} days"])
    top = ranking[0]
    if n == 1:
        return _call("run_sql", query=series_sql(top["patient_id"]))
    if n == 2:
        return _call("run_sql", query=pain_sql(top["patient_id"]))
    pain = {r["after_training_day"]: r for r in ctx.rows(2) or []}
    if n == 3:
        return _propose(top, pain)
    lines = [f"{i}. {r['name']} ({r['affected_side']} side): squat depth {_num(r['depth_from'])}° → "
             f"{_num(r['depth_to'])}°, load on the affected leg {_num(r['load_from'])}% → {_num(r['load_to'])}%."
             for i, r in enumerate(ranking, 1)]
    answer = (f"{_count_word(len(ranking))} patient{'s are' if len(ranking) != 1 else ' is'} compensating over the "
              f"last {WINDOW_DAYS} days (depth improving while load on the affected leg drops), most first:\n"
              + "\n".join(lines))
    evidence = [f"progress_v (squat, last {WINDOW_DAYS} days): mean of the first vs last 3 session days of p95_knee "
                "and affected_load_pct"]
    if True in pain and False in pain:
        answer += (f"\n{top['name']}'s pain after back-to-back training days averaged "
                   f"{_num(pain[True]['avg_pain_post'])}/10, versus {_num(pain[False]['avg_pain_post'])}/10 otherwise.")
        evidence.append(f"sessions.pain_post for {top['name']}: {_num(pain[True]['avg_pain_post'])} after a training "
                        f"day ({pain[True]['days']} days) vs {_num(pain[False]['avg_pain_post'])} "
                        f"({pain[False]['days']} days)")
    proposal = ctx.rows(3)
    if proposal and proposal[0].get("proposal_id"):
        answer += ("\nI drafted a plan proposal for you to review: a rest day between squat days and a "
                   "weight-shift drill.")
    series = ctx.rows(1) or []
    chart = {"type": "line", "title": f"{top['name']}: squat depth vs load on the affected leg",
             "x_label": "day", "y_label": "degrees / % body weight",
             "series": [{"name": "p95 knee depth (°)", "points": [[r["day"], r["p95_knee"]] for r in series]},
                        {"name": "affected-leg load (%)",
                         "points": [[r["day"], r["affected_load_pct"]] for r in series]}]}
    return _final(answer, evidence, [chart] if series else [])


def _propose(row: dict[str, Any], pain: dict[bool, dict[str, Any]]) -> Reply:
    rationale = (f"{row['name']}'s squat depth rose from {_num(row['depth_from'])}° to {_num(row['depth_to'])}° "
                 f"while load on the affected leg fell from {_num(row['load_from'])}% to {_num(row['load_to'])}% "
                 f"over the last {WINDOW_DAYS} days, a compensation pattern.")
    evidence = [f"progress_v: p95_knee {_num(row['depth_from'])} → {_num(row['depth_to'])}, affected_load_pct "
                f"{_num(row['load_from'])} → {_num(row['load_to'])}"]
    if True in pain:
        rationale += f" Pain after back-to-back days averaged {_num(pain[True]['avg_pain_post'])}/10."
        evidence.append(f"sessions.pain_post after a training day: {_num(pain[True]['avg_pain_post'])}")
    rationale += " Suggest a rest day between squat days and a weight-shift drill."
    return _call("create_plan_proposal", patient_id=row["patient_id"], rationale=rationale, evidence=evidence,
                 change=dict(PROPOSED_CHANGE))


def _missed(ctx: Ctx) -> Reply:
    if not ctx.results:
        return _call("run_sql", query=MISSED_SQL)
    rows = ctx.rows(0)
    if rows is None:
        return _could_not(ctx)
    if not rows:
        return _final("No patient missed 3 or more scheduled squat days in the last 14 days.")
    names = "; ".join(f"{r['name']} ({r['missed']} missed, {r['sessions_last_14_days']} of "
                      f"{r['scheduled_days']} scheduled days done)" for r in rows)
    return _final(f"{_count_word(len(rows))} patient{'s' if len(rows) != 1 else ''} missed 3 or more scheduled squat "
                  f"days in the last 14 days: {names}.",
                  ["sessions per local day vs 2 × prescriptions.targets.sessions_per_week (yesterday back)"])


def _trend(ctx: Ctx, *, diagnose: bool = False) -> Reply:
    where = _patient_filter(ctx)
    if where is None:
        return _final("Which patient do you mean? Please name them.")
    if not ctx.results:
        return _call("run_sql", query=trend_sql(where))
    rows = ctx.rows(0)
    if rows is None:
        return _could_not(ctx)
    prefix = "I can't diagnose; that is a clinical judgment for you. " if diagnose else ""
    if not rows:
        return _final(prefix + f"There is not enough data: no squat sessions in the last {WINDOW_DAYS} days.")
    r = rows[0]
    if r["days"] < 6:
        return _final(prefix + f"There is not enough data to describe a trend for {r['name']}: only "
                      f"{r['days']} squat day{'s' if r['days'] != 1 else ''} in the last {WINDOW_DAYS} days.",
                      [f"progress_v: {r['days']} squat day(s) for {r['name']}"])
    load = ""
    if r["load_from"] is not None:
        load = f", and load on the affected leg went from {_num(r['load_from'])}% to {_num(r['load_to'])}%"
    lead = "What the data shows: " if diagnose else ""
    return _final(prefix + f"{lead}{r['name']}'s squat depth (p95 knee flexion) went from {_num(r['depth_from'])}° "
                  f"to {_num(r['depth_to'])}° over the last {WINDOW_DAYS} days{load}.",
                  [f"progress_v (squat): mean of the first vs last 3 of {r['days']} session days"])


def _load(ctx: Ctx) -> Reply:
    where = _patient_filter(ctx)
    if where is None:
        return _final("Which patient do you mean? Please name them.")
    if not ctx.results:
        return _call("run_sql", query=load_week_sql(where))
    rows = ctx.rows(0)
    if rows is None:
        return _could_not(ctx)
    if not rows or rows[0]["affected_load_pct"] is None:
        return _final("There is not enough data for this week: no squat days with an affected-leg load.")
    r = rows[0]
    return _final(f"{r['name']}'s average load on the affected ({r['affected_side']}) leg this week is "
                  f"{_num(r['affected_load_pct'])}% of body weight, over {r['days']} squat days.",
                  ["progress_v.affected_load_pct (squat), last 7 local days"])


def _weekly_review(ctx: Ctx) -> Reply:
    n = len(ctx.results)
    pid = ctx.patient_id or ""
    if n == 0:
        return _call("run_sql", query=trend_sql(f"p.id = '{pid}'"))
    rows = ctx.rows(0)
    if rows is None:
        return _could_not(ctx)
    if not rows or rows[0]["days"] < 6:
        return _final("Not enough data this week for a review; no plan change suggested.")
    r = rows[0]
    if n == 1:
        return _call("run_sql", query=pain_sql(pid))
    pain = {p["after_training_day"]: p for p in ctx.rows(1) or []}
    compensating = (r["load_drop"] is not None and r["load_drop"] > 2
                    and r["depth_to"] - r["depth_from"] > 5)
    if compensating and n == 2:
        return _propose(r, pain)
    summary = (f"{r['name']}: squat depth {_num(r['depth_from'])}° → {_num(r['depth_to'])}° over the last "
               f"{WINDOW_DAYS} days")
    if r["load_from"] is not None:
        summary += f", affected-leg load {_num(r['load_from'])}% → {_num(r['load_to'])}%"
    if compensating:
        summary += ". This looks like compensation; I left a plan proposal for you to review."
    else:
        summary += ". No plan change suggested."
    return _final(summary, [f"progress_v (squat), last {WINDOW_DAYS} days"])


def _help(ctx: Ctx) -> Reply:
    return _final("I can answer questions about your patients' exercise data, for example who is compensating, "
                  "who missed sessions, or how a patient's squat depth has changed.")


def fake(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> Reply:
    question, patient_id, mode = _parse(messages[0].get("content", ""))
    results = [(m["name"], m.get("content") or {}) for m in messages
               if m.get("role") == "tool" and m.get("name") != "final_answer"]
    ctx = Ctx(question, patient_id, mode, results)
    q = question.lower()
    if mode == "weekly_review":
        return _weekly_review(ctx)
    if _WRITE_RE.search(q):
        return _refuse_write(ctx)
    if "diagnos" in q:
        return _trend(ctx, diagnose=True)
    if "compensat" in q:
        return _compensation(ctx)
    if "miss" in q:
        return _missed(ctx)
    if "load" in q or "weight" in q:
        return _load(ctx)
    if any(w in q for w in ("depth", "trend", "progress", "how is", "how has", "doing")):
        return _trend(ctx)
    return _help(ctx)
