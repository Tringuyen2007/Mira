// Pure view-models for Patient review's "Apple Watch" cards: GET /patients/{id}/watch JSON ({patient_id,
// hr_recovery[], rotation_sets[], tremor[]}, newest first; server/api/watch_admin.py) -> the data the cards render.
// No DOM here, so these are unit-testable without fakedom (tests/patient-watch-model.test.mjs).
import { exerciseName } from "../../core/exercises.js";

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const r1 = (n) => (n === null ? null : Math.round(n * 10) / 10);
const timeMs = (iso) => { const t = Date.parse(iso); return Number.isFinite(t) ? t : 0; };
const fmtDayShort = (iso) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

function rows(watchJson, key) {
  return (watchJson && Array.isArray(watchJson[key])) ? watchJson[key].filter((r) => r && typeof r === "object") : [];
}

// "3 h 10 min", "25 min", "0 min".
export function fmtMinutes(min) {
  const m = Math.max(0, Math.round(Number(min) || 0));
  const hours = Math.floor(m / 60);
  const rest = m % 60;
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

// ---- Heart-rate recovery -----------------------------------------------------------------------------------

// Per-set drop (peak -> 60 s later), oldest first so the chart reads left to right. `points` are numbered sets
// (1..n): two sets can share a day and a set number across sessions, so a date label wouldn't be unique.
export function hrRecoveryModel(watchJson) {
  const list = rows(watchJson, "hr_recovery")
    .filter((r) => num(r.drop_bpm) !== null)
    .sort((a, b) => timeMs(a.time) - timeMs(b.time));
  if (list.length === 0) return { empty: true, points: [], avgDrop: null, summary: null, latest: null };
  const avgDrop = Math.round(avg(list.map((r) => r.drop_bpm)));
  const last = list[list.length - 1];
  return {
    empty: false,
    points: list.map((r, i) => ({ x: i + 1, y: r1(r.drop_bpm) })),
    avgDrop,
    summary: `Avg drop ${avgDrop} bpm in 1 min`,
    latest: num(last.peak_bpm) !== null && num(last.bpm_60s) !== null
      ? `Last set: ${Math.round(last.peak_bpm)} → ${Math.round(last.bpm_60s)} bpm` : null,
    count: list.length,
  };
}

// ---- Rotation range ------------------------------------------------------------------------------------------

// One row per watch exercise over the recent sets: best of the sets' best angles, mean of their averages, and the
// goal from the newest set. `reached` (best >= goal) is shown with an icon and text, never colour alone.
export function rotationModel(watchJson) {
  const byExercise = new Map();
  for (const r of rows(watchJson, "rotation_sets")) {
    const id = String(r.exercise_id || "");
    if (!id) continue;
    if (!byExercise.has(id)) byExercise.set(id, []);
    byExercise.get(id).push(r);
  }
  const exercises = [...byExercise.entries()].map(([id, sets]) => {
    const sorted = sets.slice().sort((a, b) => timeMs(b.time) - timeMs(a.time)); // newest first
    const bests = sorted.map((s) => num(s.best_deg)).filter((v) => v !== null);
    const avgs = sorted.map((s) => num(s.avg_deg)).filter((v) => v !== null);
    const goal = sorted.map((s) => num(s.goal_deg)).find((v) => v !== null) ?? null;
    const best = bests.length ? Math.round(Math.max(...bests)) : null;
    const average = avgs.length ? Math.round(avg(avgs)) : null;
    const reached = best !== null && goal !== null && best >= goal;
    const short = best !== null && goal !== null && !reached ? Math.round(goal - best) : null;
    return {
      exerciseId: id,
      name: exerciseName(id),
      sets: sorted.length,
      best, avg: average, goal: goal === null ? null : Math.round(goal), reached,
      status: goal === null || best === null ? "No goal set" : reached ? "Goal reached" : `${short}° short of goal`,
      line: [best !== null ? `Best ${best}°` : null, average !== null ? `avg ${average}°` : null,
        goal !== null ? `goal ${Math.round(goal)}°` : null].filter(Boolean).join(" · "),
    };
  }).sort((a, b) => a.name.localeCompare(b.name));
  return { empty: exercises.length === 0, exercises };
}

// ---- Tremor (14 days) ------------------------------------------------------------------------------------------

export const TREMOR_LEVELS = Object.freeze(["none", "slight", "mild", "strong"]);
const LEVEL_LABEL = { none: "None", slight: "Slight", mild: "Mild", strong: "Strong" };
const DYSKINESIA_LABEL = { none: "none", few: "few", some: "some", many: "many" };

// `monitoring`: the patient's tremor_monitoring flag (GET /patients/{id}.patient.tremor_monitoring), so an empty
// card can say why it's empty. Days are oldest first; a day's bar is its none/slight/mild/strong minutes.
export function tremorModel(watchJson, { monitoring = false } = {}) {
  const days = rows(watchJson, "tremor")
    .filter((d) => typeof d.day === "string" && !Number.isNaN(Date.parse(d.day)))
    .sort((a, b) => Date.parse(a.day) - Date.parse(b.day))
    .map((d) => {
      const minutes = Object.fromEntries(TREMOR_LEVELS.map((k) => [k, Math.max(0, num(d[`${k}_min`]) ?? 0)]));
      const dysk = DYSKINESIA_LABEL[d.dyskinesia] || null;
      const label = fmtDayShort(d.day);
      const parts = TREMOR_LEVELS.map((k) => `${LEVEL_LABEL[k].toLowerCase()} ${fmtMinutes(minutes[k])}`);
      return {
        day: d.day, label, minutes,
        total: TREMOR_LEVELS.reduce((a, k) => a + minutes[k], 0),
        dyskinesia: dysk,
        simulated: d.source === "simulated",
        text: `${label}: ${parts.join(", ")}${dysk ? `; extra movements: ${dysk}` : ""}`,
      };
    });
  const simulated = days.some((d) => d.simulated);
  if (days.length === 0) {
    return {
      empty: true, days: [], maxTotal: 0, simulated: false, latest: null, monitoring: Boolean(monitoring),
      emptyText: monitoring ? "No tremor data from the watch yet." : "Tremor monitoring is off.",
    };
  }
  const last = days[days.length - 1];
  // Headline: the strongest level with any minutes on the newest day ("Slight for 3 h 10 min").
  const top = [...TREMOR_LEVELS].reverse().find((k) => k !== "none" && last.minutes[k] > 0);
  const headline = top ? `${LEVEL_LABEL[top]} for ${fmtMinutes(last.minutes[top])}` : "No tremor";
  return {
    empty: false, days, maxTotal: Math.max(...days.map((d) => d.total), 1), simulated,
    monitoring: Boolean(monitoring),
    latest: `${last.label}: ${headline}${last.dyskinesia ? ` · Extra movements: ${last.dyskinesia}` : ""}`,
    emptyText: null,
  };
}

// ---- Tremor monitoring switch ------------------------------------------------------------------------------------

// The current flag from GET /patients/{id} (the patients row carries tremor_monitoring after migration 007).
export function tremorMonitoringOf(patientJson) {
  return Boolean(patientJson && patientJson.patient && patientJson.patient.tremor_monitoring === true);
}

// A `watch_features_changed` /ws/events message -> the new flag when it's for `pid`, else null (ignore it).
export function watchFeaturesFromEvent(msg, pid) {
  if (!msg || msg.type !== "watch_features_changed" || msg.patient_id !== pid) return null;
  return typeof msg.tremor_monitoring === "boolean" ? msg.tremor_monitoring : null;
}

// Whole "Apple Watch" group: no watch data at all -> one empty state instead of three empty cards.
export function watchGroupModel(watchJson, patientJson) {
  const monitoring = tremorMonitoringOf(patientJson);
  const hr = hrRecoveryModel(watchJson);
  const rotation = rotationModel(watchJson);
  const tremor = tremorModel(watchJson, { monitoring });
  return { empty: hr.empty && rotation.empty && tremor.empty, hr, rotation, tremor, monitoring };
}
