// Pure view-models for the Patient review screen: GET /sessions/{id}, /patients/{id}/progress, /patients/{id}/
// memory and /sessions/{id}/progress-note JSON -> the data the cards render. No DOM here so these are unit-
// testable without fakedom. `patientJson` throughout is the full GET /patients/{id} response ({patient,
// prescriptions, recent_sessions}).
import { fmtDeg, fmtPct, loadSplit } from "../../core/format.js";
import { flagCountLabel } from "../../core/exercises.js";

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

// ---- This session ------------------------------------------------------------------------------------------

export function sessionCardModel(sessionJson, patientJson) {
  const session = (sessionJson && sessionJson.session) || {};
  const reps = (sessionJson && Array.isArray(sessionJson.reps)) ? sessionJson.reps : [];
  const summary = (sessionJson && sessionJson.summary) || {};
  const patient = (patientJson && patientJson.patient) || {};
  const affectedSide = patient.affected_side === "left" ? "left" : "right";

  const depths = reps.map((r) => num(r.peak_angle)).filter((v) => v !== null);
  const avgDepth = r1(avg(depths));

  // One value, two labels that always add to 100 (browser bug 11): left = round(tw load), right = 100 - left.
  const split = loadSplit(num(sessionJson && sessionJson.tw_load_left_pct));
  const affected = split === null ? null : (affectedSide === "right" ? split.right : split.left);
  const other = affected === null ? null : 100 - affected;

  const prescriptions = (patientJson && Array.isArray(patientJson.prescriptions)) ? patientJson.prescriptions : [];
  const rx = prescriptions.find((p) => p.exercise_id === session.exercise_id && p.active !== false);
  const threshold = (rx && rx.rule_overrides && num(rx.rule_overrides.weight_shift_pct)) ?? DEFAULT_WEIGHT_SHIFT_THRESHOLD;
  // Red only when flagged: the affected side carries less than (50 - threshold)% (browser bug 4).
  const flagged = affected !== null && affected < 50 - threshold;

  // summary.flag_counts ({rule_id: n}) when the server sends it; otherwise count the reps' own flag ids.
  // (`sessionJson.flags` are flag rows — objects — and are already summarised there, so never mixed in.)
  let counts = summary.flag_counts && typeof summary.flag_counts === "object" ? summary.flag_counts : null;
  if (!counts || Object.keys(counts).length === 0) {
    counts = {};
    for (const f of reps.flatMap((r) => (Array.isArray(r.flags) ? r.flags : []))) {
      if (typeof f === "string" && f) counts[f] = (counts[f] || 0) + 1;
    }
  }
  const flagSummary = flagCountLabel(counts);

  return {
    good: typeof summary.good_reps === "number" ? summary.good_reps : null,
    total: typeof summary.reps_total === "number" ? summary.reps_total : null,
    avgDepth,
    load: { affected, other, affectedSide, flagged },
    flags: flagSummary,
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

// `name`: the patient's first name for the compensation insight (falls back to "They").
export function trendModel(progressJson, affectedSide, now = new Date(), days = TREND_DAYS, name = "") {
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
      ? `Depth is up while load on the injured leg drops. ${String(name || "").trim() ? `${String(name).trim()} is` : "They are"} compensating.`
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

// `draft` (optional): text the therapist carried over from the time-lapse ({text}). It is the therapist's own
// text, not AI output, so it is labelled as such and still needs the explicit Approve click.
export function noteModel(sessionJson, approval, draft = null) {
  if (approval && approval.approved) {
    return {
      text: String(approval.text || ""),
      label: "Approved by you",
      source: "approved",
      canApprove: false,
      approvedAt: approval.approved_at || null,
    };
  }
  const session = (sessionJson && sessionJson.session) || {};
  const summary = (sessionJson && sessionJson.summary) || {};
  const aiText = String(session.ai_note || summary.ai_note || "");
  const draftText = draft && typeof draft.text === "string" ? draft.text.trim() : "";
  if (draftText) {
    const text = [aiText.trim(), draftText].filter(Boolean).join("\n\n");
    return { text, label: aiText.trim() ? "AI draft + from time-lapse — review before approving"
      : "From time-lapse — review before approving", source: aiText.trim() ? "mixed" : "therapist",
    canApprove: true, approvedAt: null };
  }
  const label = summary.ai_note_label || "AI draft — review before use";
  return { text: aiText, label, source: "ai", canApprove: aiText.trim().length > 0, approvedAt: null };
}
