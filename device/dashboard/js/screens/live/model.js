// Pure view-models for the Live session screen: /ws/live snapshots -> the data the head/room/cue cards render.
// No DOM here so these are unit-testable without fakedom (see tests/live-model.test.mjs, server/samples/ws_live.json
// and ws_events_cue.json).
import { fmtDeg, loadSplit } from "../../core/format.js";

const POSE_HZ_LABEL = "20 Hz"; // pose channel's push rate (server/engine_bridge.py), a fixed label, not measured
// Below this the delay isn't worth a number; above MAX the two clocks clearly disagree (skew), so a number would
// be wrong either way. Both read "live".
const LATENCY_MIN_MS = 50;
const LATENCY_MAX_MS = 5000;

function cap(s) {
  const str = String(s || "");
  return str ? str[0].toUpperCase() + str.slice(1) : str;
}

function fmtClockShort(epochSeconds) {
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// liveModel(liveSnapshot, patient, receivedAt) -> the Live session screen's head/room text (Figma 210:820).
// `receivedAt` is when the browser received the snapshot; "about N ms behind" is receivedAt minus the server's
// own `t` (epoch seconds). A tiny, negative or implausibly large gap (clock skew) reads "live" instead of a
// misleading "about 0 ms behind" (browser bug 11).
export function liveModel(liveSnapshot, patient, now = new Date()) {
  if (!liveSnapshot) {
    return {
      active: false, title: "Live session", rep: null, line: null, hr: null, latency: null,
      balance: null, angles: { kneeL: null, kneeR: null }, paused: false, incident: false,
    };
  }

  const affectedSide = patient && patient.affected_side === "left" ? "left" : "right";
  const title = liveSnapshot.first_name ? `${liveSnapshot.first_name}'s live session` : "Live session";
  const rep = liveSnapshot.rep != null ? `Rep ${liveSnapshot.rep}` : null;
  const exerciseName = cap(liveSnapshot.exercise_name || String(liveSnapshot.exercise_id || "").replace(/_/g, " "));
  const line = liveSnapshot.set != null && liveSnapshot.sets_target != null
    ? `${exerciseName} · set ${liveSnapshot.set} of ${liveSnapshot.sets_target}`
    : (exerciseName || null);
  const hr = typeof liveSnapshot.hr === "number" ? `${liveSnapshot.hr} bpm` : null;

  const nowS = now instanceof Date ? now.getTime() / 1000 : Date.parse(now) / 1000;
  const tS = Number(liveSnapshot.t);
  const behindMs = Number.isFinite(nowS) && Number.isFinite(tS) ? Math.round((nowS - tS) * 1000) : null;
  const latency = behindMs !== null && behindMs >= LATENCY_MIN_MS && behindMs <= LATENCY_MAX_MS
    ? `${POSE_HZ_LABEL} · about ${behindMs} ms behind · smoothed`
    : `${POSE_HZ_LABEL} · live · smoothed`;

  // One value, two labels that add up to 100 (browser bug 11).
  const split = loadSplit(liveSnapshot.load_l_pct);
  const affected = split ? (affectedSide === "left" ? split.left : split.right) : null;
  const other = affected === null ? null : 100 - affected;
  const flagged = Array.isArray(liveSnapshot.flags) && liveSnapshot.flags.length > 0;
  const balance = { affected, other, affectedSide, flagged };

  // The live snapshot only carries one symmetric movement angle (main_angle); per-knee angles arrive separately
  // over the pose channel and are rendered by the avatar view's own pills. Both sides fall back to main_angle
  // here so a text readout always has something to show even before the first pose frame lands.
  const deg = fmtDeg(liveSnapshot.main_angle);
  const angles = { kneeL: deg, kneeR: deg };

  return {
    active: true, title, rep, line, hr, latency, balance, angles,
    paused: Boolean(liveSnapshot.paused), incident: Boolean(liveSnapshot.incident),
  };
}

// cueHistoryModel(events) -> [{time: "9:41", text}] from `cue`-type /ws/events messages (ws_events_cue.json's
// shape), newest first, for the "Said this session" list.
export function cueHistoryModel(events) {
  const list = Array.isArray(events) ? events : [];
  return list
    .filter((e) => e && e.type === "cue" && typeof e.text === "string" && e.text.trim())
    .slice()
    .sort((a, b) => Number(b.t) - Number(a.t))
    .map((e) => ({ time: fmtClockShort(e.t), text: e.text }));
}

// ---- Apple Watch (server/samples/ws_events_breather.json, _hr_recovery, _rotation_rep, _rotation_set) ----------

const BREATHER_END = {
  hr_recovered: "Breather over — heart rate back under the limit",
  escalated: "Breather escalated — check on the patient",
  stopped: "Breather ended — session stopped",
};

// A `breather` event -> the banner: {active: true, text: "Breather — 128 bpm over limit 120"} on start; on end
// {active: false, text: <how it ended> | null} (the banner clears; the line stays as a quiet note).
export function breatherModel(msg) {
  if (!msg || msg.type !== "breather") return null;
  const bpm = typeof msg.bpm === "number" ? Math.round(msg.bpm) : null;
  const limit = typeof msg.limit === "number" ? Math.round(msg.limit) : null;
  if (msg.state === "start") {
    const over = bpm !== null && limit !== null ? `${bpm} bpm over limit ${limit}`
      : bpm !== null ? `${bpm} bpm` : "heart rate over the limit";
    return { active: true, text: `Breather — ${over}` };
  }
  if (msg.state === "end") return { active: false, text: BREATHER_END[msg.resolution] || "Breather over" };
  return null;
}

// The latest `hr_recovery` -> "Set 2 recovery: 126 → 104 bpm, dropped 22 bpm in 1 min".
export function hrRecoveryLine(msg) {
  if (!msg || msg.type !== "hr_recovery" || typeof msg.drop_bpm !== "number") return null;
  const set = msg.set_index != null ? `Set ${msg.set_index} recovery` : "Recovery";
  const span = typeof msg.peak_bpm === "number" && typeof msg.bpm_60s === "number"
    ? `${Math.round(msg.peak_bpm)} → ${Math.round(msg.bpm_60s)} bpm, ` : "";
  return `${set}: ${span}dropped ${Math.round(msg.drop_bpm)} bpm in 1 min`;
}

// `rotation_rep` -> the turn just made vs the goal; `rotation_set` -> the set's best/avg. `reached` drives an
// icon + text (never colour alone).
export function rotationLiveModel(msg) {
  if (!msg) return null;
  const deg = (v) => (typeof v === "number" && Number.isFinite(v) ? Math.round(v) : null);
  if (msg.type === "rotation_rep") {
    const angle = deg(msg.angle_deg);
    const goal = deg(msg.goal_deg);
    const reached = msg.reached === true || (angle !== null && goal !== null && angle >= goal);
    const rep = msg.rep != null ? `Rep ${msg.rep}${msg.reps_target != null ? ` of ${msg.reps_target}` : ""}` : "Turn";
    return {
      exerciseId: msg.exercise_id || null,
      text: `${rep} · ${angle !== null ? `${angle}°` : "—"}${goal !== null ? ` of ${goal}° goal` : ""}`,
      reached,
      status: reached ? "Goal reached" : goal !== null && angle !== null ? `${goal - angle}° short` : "Below goal",
    };
  }
  if (msg.type === "rotation_set") {
    const best = deg(msg.best_deg);
    const goal = deg(msg.goal_deg);
    const reached = best !== null && goal !== null && best >= goal;
    const avgDeg = deg(msg.avg_deg);
    return {
      exerciseId: msg.exercise_id || null,
      text: `Set ${msg.set_index ?? "—"} done · best ${best !== null ? `${best}°` : "—"}, `
        + `avg ${avgDeg !== null ? `${avgDeg}°` : "—"}${goal !== null ? ` (goal ${goal}°)` : ""}`,
      reached,
      status: reached ? "Goal reached" : "Below goal",
    };
  }
  return null;
}
