// Pure view-models for the Recovery time-lapse screen: two GET /sessions/{id} JSONs + a GET /patients/{id} JSON
// -> the data the two date cards and the shared headline render. No DOM here so these are unit-testable without
// fakedom (see tests/timelapse-model.test.mjs). Reuses repTimelineModel's tStart/tEnd chaining (patient/model.js)
// instead of recomputing it.
import { weeksSince, capitalize, loadSplit } from "../../core/format.js";
import { repTimelineModel } from "../patient/model.js";

const fmtDayShort = (iso) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

// bestRep(sessionJson) -> {rep, tStart, tEnd}: the highest peak_angle among unflagged reps, else the highest
// peak_angle among all reps. null when the session has no reps.
export function bestRep(sessionJson) {
  const rows = repTimelineModel(sessionJson);
  if (rows.length === 0) return null;
  const rawReps = (sessionJson && Array.isArray(sessionJson.reps)) ? sessionJson.reps : [];
  const peakFor = (n) => {
    const r = rawReps.find((x) => x.rep === n);
    const peak = r && num(r.peak_angle);
    return peak === null ? -Infinity : peak;
  };
  const unflagged = rows.filter((r) => !r.flagged);
  const pool = unflagged.length ? unflagged : rows;
  const best = pool.reduce((a, b) => (peakFor(b.rep) > peakFor(a.rep) ? b : a));
  return { rep: best.rep, tStart: best.tStart, tEnd: best.tEnd };
}

// syncClock(durA, durB, t) maps one shared 0..1 progress to both reps' own millisecond durations, so a shorter
// rep finishes early (and holds) while a longer one is still playing out.
export function syncClock(durA, durB, t) {
  const p = Math.max(0, Math.min(1, Number(t) || 0));
  return { tA: p * (Number(durA) || 0), tB: p * (Number(durB) || 0) };
}

// Deepest knee flexion in a window of frames (mean of both knees, degrees): the depth when the reps carry no
// peak_angle (the live engine doesn't always store it).
export function framesDepth(frames) {
  let best = null;
  for (const f of frames || []) {
    const l = num(f.knee_l);
    const r = num(f.knee_r);
    const v = l !== null && r !== null ? (l + r) / 2 : (l ?? r);
    if (v !== null && (best === null || v > best)) best = v;
  }
  return best;
}

// "Week N" counts from the patient's first session (`firstSessionAt`), else from their registration.
export function weekLabel(startedAt, firstSessionAt, createdAt) {
  const origin = firstSessionAt || createdAt;
  if (!origin || !startedAt) return null;
  return `Week ${Math.max(1, weeksSince(origin, new Date(startedAt)) + 1)}`;
}

function sideModel(sessionJson, patientJson, { firstSessionAt = null, framesDepthDeg = null } = {}) {
  const session = (sessionJson && sessionJson.session) || {};
  const patient = (patientJson && patientJson.patient) || {};
  const affectedSide = patient.affected_side === "left" ? "left" : "right";
  const rep = bestRep(sessionJson);
  const rawReps = (sessionJson && Array.isArray(sessionJson.reps)) ? sessionJson.reps : [];
  const repRow = rep ? rawReps.find((r) => r.rep === rep.rep) : null;
  const depth = (repRow ? num(repRow.peak_angle) : null) ?? num(framesDepthDeg);

  const split = loadSplit(sessionJson && sessionJson.tw_load_left_pct);
  const load = split === null ? null : (affectedSide === "left" ? split.left : split.right);

  const startedAt = session.started_at;
  const week = weekLabel(startedAt, firstSessionAt, patient.created_at);

  return {
    sessionId: session.id || null,
    label: week || "—",
    date: startedAt ? fmtDayShort(startedAt) : "—",
    depth: depth === null ? null : Math.round(depth),
    load, affectedSide, rep,
  };
}

function buildHeadline(left, right) {
  const parts = [];
  if (left.depth != null && right.depth != null) {
    const d = Math.round(right.depth - left.depth);
    parts.push(`Depth ${d >= 0 ? "+" : ""}${d}°`);
  }
  if (left.load != null && right.load != null) {
    const side = left.affectedSide === "left" ? "left" : "right";
    parts.push(`${side}-leg load ${Math.round(left.load)}% → ${Math.round(right.load)}%`);
  }
  // Figma: "Depth +22°, right-leg load 36% → 47%", a capitalised sentence even when depth is missing.
  return parts.length ? capitalize(parts.join(", ")) : null;
}

// timelapseModel(sessA, sessB, patient) -> {left, right, headline} (Figma 208:339). `sessA`/`sessB` are two
// GET /sessions/{id} JSONs for the same exercise, `patient` is a GET /patients/{id} JSON.
// opts: {firstSessionAt (ISO, for week labels), depthA/depthB (frames-based depth fallbacks, degrees)}.
export function timelapseModel(sessA, sessB, patientJson, { firstSessionAt = null, depthA = null, depthB = null } = {}) {
  const left = sideModel(sessA, patientJson, { firstSessionAt, framesDepthDeg: depthA });
  const right = sideModel(sessB, patientJson, { firstSessionAt, framesDepthDeg: depthB });
  return { left, right, headline: buildHeadline(left, right) };
}
