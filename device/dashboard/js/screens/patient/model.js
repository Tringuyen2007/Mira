// Pure view-models for the Patient review screen: GET /sessions/{id}, /patients/{id}/progress, /patients/{id}/
// memory and /sessions/{id}/progress-note JSON -> the data the cards render. No DOM here so these are unit-
// testable without fakedom. `patientJson` throughout is the full GET /patients/{id} response ({patient,
// prescriptions, recent_sessions}).
import { fmtDeg, fmtPct } from "../../core/format.js";

const DEFAULT_WEIGHT_SHIFT_THRESHOLD = 15;
const TREND_DAYS = 21;
const TREND_TICKS = 4;
const EDGE_DAYS = 3; // "first 3 days vs last 3 days" compensation rule
const DEPTH_UP_DEG = 5;
const LOAD_DOWN_PCT = 3;
// Memory events are written by server/agents/insight.py with agent="insight" today (the column isn't even
// selected by GET /patients/{id}/memory yet), so an event with no `agent` is treated as AI-written; this set
// is forward-compatible with a deterministic/rule-based writer being added later.
const DETERMINISTIC_AGENTS = new Set(["rule", "deterministic", "coach", "system"]);

const fmtDayShort = (iso) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const r1 = (n) => (n === null ? null : Math.round(n * 10) / 10);

// "knee_cave_r" -> {noun: "knee cave", side: "right"}; "weight_shift" -> {noun: "weight shift", side: null}.
function describeFlag(flag) {
  const raw = String(flag || "");
  const m = raw.match(/^(.*)_(l|r)$/);
  if (m) return { noun: m[1].replace(/_/g, " "), side: m[2] === "l" ? "left" : "right" };
  return { noun: raw.replace(/_/g, " "), side: null };
}

// ---- This session ------------------------------------------------------------------------------------------

export function sessionCardModel(sessionJson, patientJson) {
  const session = (sessionJson && sessionJson.session) || {};
  const reps = (sessionJson && Array.isArray(sessionJson.reps)) ? sessionJson.reps : [];
  const summary = (sessionJson && sessionJson.summary) || {};
  const patient = (patientJson && patientJson.patient) || {};
  const affectedSide = patient.affected_side === "left" ? "left" : "right";

  const depths = reps.map((r) => num(r.peak_angle)).filter((v) => v !== null);
  const avgDepth = r1(avg(depths));

  const twLoadLeft = num(sessionJson && sessionJson.tw_load_left_pct);
  const affected = twLoadLeft === null ? null : (affectedSide === "right" ? 100 - twLoadLeft : twLoadLeft);
  const other = affected === null ? null : 100 - affected;

  const prescriptions = (patientJson && Array.isArray(patientJson.prescriptions)) ? patientJson.prescriptions : [];
  const rx = prescriptions.find((p) => p.exercise_id === session.exercise_id && p.active !== false);
  const threshold = (rx && rx.rule_overrides && num(rx.rule_overrides.weight_shift_pct)) ?? DEFAULT_WEIGHT_SHIFT_THRESHOLD;
  const flagged = affected !== null && affected < 50 - threshold;

  const allFlags = [...(Array.isArray(sessionJson && sessionJson.flags) ? sessionJson.flags : []),
    ...reps.flatMap((r) => (Array.isArray(r.flags) ? r.flags : []))];
  const flagCount = allFlags.length;
  let flagLabel = "No flags";
  if (flagCount > 0) {
    const counts = new Map();
    for (const f of allFlags) counts.set(f, (counts.get(f) || 0) + 1);
    const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
    const { noun, side } = describeFlag(top);
    const plural = `${noun}${flagCount === 1 ? "" : "s"}`;
    flagLabel = side ? `${flagCount} ${side} ${plural}` : `${flagCount} ${plural}`;
  }

  return {
    good: typeof summary.good_reps === "number" ? summary.good_reps : null,
    total: typeof summary.reps_total === "number" ? summary.reps_total : null,
    avgDepth,
    load: { affected, other, affectedSide, flagged },
    flags: { count: flagCount, label: flagLabel },
    pain: num(session.pain_post) ?? num(session.pain_pre),
    effort: num(session.effort),
  };
}

// ---- Rep timeline --------------------------------------------------------------------------------------------

export function repTimelineModel(sessionJson) {
  const session = (sessionJson && sessionJson.session) || {};
  const reps = (sessionJson && Array.isArray(sessionJson.reps)) ? sessionJson.reps.slice() : [];
  reps.sort((a, b) => (a.rep || 0) - (b.rep || 0));
  const startMs = Date.parse(session.started_at);
  const sessionStart = Number.isFinite(startMs) ? startMs : 0;

  let prevEndMs = sessionStart;
  return reps.map((r) => {
    const endMs = Date.parse(r.ended_at);
    const tEnd = Number.isFinite(endMs) ? endMs - sessionStart : prevEndMs - sessionStart;
    const tStart = prevEndMs - sessionStart;
    prevEndMs = Number.isFinite(endMs) ? endMs : prevEndMs;
    const flags = Array.isArray(r.flags) ? r.flags : [];
    const flagged = flags.length > 0 || (typeof r.quality === "number" && r.quality < 1);
    return {
      rep: r.rep, set: r.set_no, flagged, flags, tStart, tEnd,
      label: flagged ? `Rep ${r.rep}, flagged` : `Rep ${r.rep}`,
    };
  });
}

// ---- 3-week trend --------------------------------------------------------------------------------------------

function inWindow(iso, now, days) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return false;
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now);
  return t >= nowMs - days * 24 * 60 * 60 * 1000 && t <= nowMs;
}

function trendTicks(now, days) {
  const nowMs = now instanceof Date ? now.getTime() : Date.parse(now);
  const start = nowMs - days * 24 * 60 * 60 * 1000;
  const ticks = [];
  for (let i = 0; i < TREND_TICKS; i++) {
    const t = start + ((nowMs - start) * i) / (TREND_TICKS - 1);
    ticks.push(fmtDayShort(new Date(t)));
  }
  return ticks;
}

export function trendModel(progressJson, affectedSide, now = new Date(), days = TREND_DAYS) {
  const series = (progressJson && Array.isArray(progressJson.series)) ? progressJson.series.slice() : [];
  const inRange = series
    .filter((s) => s && inWindow(s.day, now, days))
    .sort((a, b) => Date.parse(a.day) - Date.parse(b.day));

  const depthPoints = inRange.map((s) => [s.day, r1(num(s.p95_knee))]).filter(([, v]) => v !== null);
  const loadPoints = inRange.map((s) => [s.day, r1(num(s.affected_load_pct))]).filter(([, v]) => v !== null);

  const firstDepth = depthPoints.length ? depthPoints[0][1] : null;
  const lastDepth = depthPoints.length ? depthPoints[depthPoints.length - 1][1] : null;
  const firstLoad = loadPoints.length ? loadPoints[0][1] : null;
  const lastLoad = loadPoints.length ? loadPoints[loadPoints.length - 1][1] : null;

  const side = affectedSide === "left" ? "Left" : "Right";
  const caption = firstDepth !== null && lastDepth !== null
    ? `Depth ${fmtDeg(firstDepth)} → ${fmtDeg(lastDepth)}` : null;
  const loadCaption = firstLoad !== null && lastLoad !== null
    ? `${side} leg load ${fmtPct(firstLoad)} → ${fmtPct(lastLoad)}` : null;

  const firstDepths = depthPoints.slice(0, EDGE_DAYS).map(([, v]) => v);
  const lastDepths = depthPoints.slice(-EDGE_DAYS).map(([, v]) => v);
  const firstLoads = loadPoints.slice(0, EDGE_DAYS).map(([, v]) => v);
  const lastLoads = loadPoints.slice(-EDGE_DAYS).map(([, v]) => v);
  const depthDelta = (avg(lastDepths) ?? 0) - (avg(firstDepths) ?? 0);
  const loadDelta = (avg(firstLoads) ?? 0) - (avg(lastLoads) ?? 0);
  const compensating = depthPoints.length >= 2 && loadPoints.length >= 2
    && depthDelta >= DEPTH_UP_DEG && loadDelta >= LOAD_DOWN_PCT;

  return {
    points: { depth: depthPoints, load: loadPoints },
    labels: trendTicks(now, days),
    caption,
    loadCaption,
    compensating,
    insight: compensating
      ? "Depth is up while load on the injured leg drops. Maya is compensating."
      : null,
  };
}

// ---- Therapist Memory ------------------------------------------------------------------------------------

export function memoryModel(memoryJson, lastVisitIso) {
  const events = (memoryJson && Array.isArray(memoryJson.events)) ? memoryJson.events : [];
  const subtitle = lastVisitIso
    ? `What changed since your visit on ${fmtDayShort(lastVisitIso)}`
    : "What changed in the last 3 weeks";
  const items = events.map((e) => ({
    text: String((e && e.message) || ""),
    aiDraft: !DETERMINISTIC_AGENTS.has((e && e.agent) || "insight"),
  }));
  return { subtitle, items };
}

// ---- Progress note ----------------------------------------------------------------------------------------

export function noteModel(sessionJson, approval) {
  if (approval && approval.approved) {
    return {
      text: String(approval.text || ""),
      label: "Approved by you",
      canApprove: false,
      approvedAt: approval.approved_at || null,
    };
  }
  const session = (sessionJson && sessionJson.session) || {};
  const summary = (sessionJson && sessionJson.summary) || {};
  const text = String(session.ai_note || summary.ai_note || "");
  const label = summary.ai_note_label || "AI draft — review before use";
  return { text, label, canApprove: text.trim().length > 0, approvedAt: null };
}
