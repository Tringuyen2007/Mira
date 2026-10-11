// Pure view-models for the Live session screen: /ws/live snapshots -> the data the head/room/cue cards render.
// No DOM here so these are unit-testable without fakedom (see tests/live-model.test.mjs, server/samples/ws_live.json
// and ws_events_cue.json).
import { fmtDeg } from "../../core/format.js";

const POSE_HZ_LABEL = "20 Hz"; // pose channel's push rate (server/engine_bridge.py), a fixed label, not measured

function cap(s) {
  const str = String(s || "");
  return str ? str[0].toUpperCase() + str.slice(1) : str;
}

function fmtClockShort(epochSeconds) {
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// liveModel(liveSnapshot, patient, now) -> the Live session screen's head/room text (Figma 210:820). `now` lets
// the "about N ms behind" latency figure be computed from the snapshot's own `t` (epoch seconds) instead of a
// hardcoded number, while staying a pure function the tests can drive with a fixed clock.
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
  const behindMs = Number.isFinite(nowS) && Number.isFinite(tS)
    ? Math.max(0, Math.round((nowS - tS) * 1000)) : 0;
  const latency = `${POSE_HZ_LABEL} · about ${behindMs} ms behind · smoothed`;

  const loadL = Number(liveSnapshot.load_l_pct);
  const affected = Number.isFinite(loadL) ? (affectedSide === "left" ? loadL : 100 - loadL) : null;
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
