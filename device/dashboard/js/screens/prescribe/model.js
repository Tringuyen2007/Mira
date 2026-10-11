// Pure view-models for the Prescribe screen (Figma 207:282): GET /patients/{id}, GET /prescriptions/{id} and
// GET /patients/{id}/progress JSON -> the data the cards render, plus diffPrescription for the Save flow.
// No DOM here, so these are unit-testable without fakedom.

import { exerciseName } from "../../core/exercises.js";

export const DEFAULT_REST_S = 60;
export const DEFAULT_TEMPO_S = 3;
export const DEFAULT_WEIGHT_SHIFT_PCT = 15;
export const DEFAULT_KNEE_CAVE_DEG = 10;
export const DEFAULT_TRUNK_LEAN_DEG = 20;
export const DEFAULT_HR_LIMIT = 120;

export { exerciseName } from "../../core/exercises.js";

function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmtDayShort(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(d);
}

// progress_v has no true daily-average knee angle column (only max_knee/p95_knee); p95 is the closest
// available approximation of "today's average" and is what this reads. Documented in the README.
function todayAvgText(progressJson, exerciseId) {
  const series = (progressJson && Array.isArray(progressJson.series)) ? progressJson.series : [];
  const rows = series.filter((s) => s && (exerciseId == null || s.exercise_id === exerciseId));
  if (rows.length === 0) return null;
  const last = rows.slice().sort((a, b) => Date.parse(a.day) - Date.parse(b.day)).pop();
  const deg = num(last.p95_knee);
  if (deg === null) return null;
  return `Today's average was ${Math.round(deg)}°.`;
}

// ---- Prescribe screen view-model -------------------------------------------------------------------------

// patientJson: GET /patients/{id} ({patient, prescriptions, recent_sessions}). prescriptionsJson: GET
// /prescriptions/{id} ({patient_id, prescriptions}) — kept as a separate param (rather than reusing
// patientJson.prescriptions) because the Save flow reloads prescriptions on their own after a PUT/approve.
// progressJson: GET /patients/{id}/progress ({series: [...]})."selectedExerciseId" picks which exercise's
// row drives the editor/flags cards; defaults to the first active exercise.
export function prescribeModel(patientJson, prescriptionsJson, progressJson, selectedExerciseId = null) {
  const patient = (patientJson && patientJson.patient) || {};
  const allRx = (prescriptionsJson && Array.isArray(prescriptionsJson.prescriptions))
    ? prescriptionsJson.prescriptions
    : (patientJson && Array.isArray(patientJson.prescriptions) ? patientJson.prescriptions : []);
  const activeRx = allRx.filter((r) => r && r.active !== false);

  const selId = activeRx.some((r) => r.exercise_id === selectedExerciseId)
    ? selectedExerciseId
    : (activeRx[0] ? activeRx[0].exercise_id : null);
  const selectedRx = activeRx.find((r) => r.exercise_id === selId) || null;

  const exercises = activeRx.map((r) => ({
    id: r.exercise_id,
    name: r.display_name ? String(r.display_name) : exerciseName(r.exercise_id),
    dose: `${r.sets} × ${r.reps}`,
    active: true,
    staged: Boolean(r.staged),
    selected: r.exercise_id === selId,
  }));

  const targets = (selectedRx && selectedRx.targets) || {};
  const rule = (selectedRx && selectedRx.rule_overrides) || {};

  const editor = selectedRx ? {
    exerciseId: selId,
    sets: selectedRx.sets ?? null,
    reps: selectedRx.reps ?? null,
    restS: num(targets.rest_s) ?? DEFAULT_REST_S,
    kneeTarget: num(targets.knee_deg),
    tempoS: num(targets.tempo_s) ?? DEFAULT_TEMPO_S,
    lastChanged: fmtDayShort(selectedRx.created_at),
    todayAvg: todayAvgText(progressJson, selId),
  } : {
    exerciseId: null, sets: null, reps: null, restS: DEFAULT_REST_S, kneeTarget: null, tempoS: DEFAULT_TEMPO_S,
    lastChanged: null, todayAvg: null,
  };

  const flags = {
    weightShiftPct: num(rule.weight_shift_pct) ?? DEFAULT_WEIGHT_SHIFT_PCT,
    kneeCaveDeg: num(rule.knee_cave_deg) ?? DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: num(rule.trunk_lean_deg) ?? DEFAULT_TRUNK_LEAN_DEG,
  };

  const hrLimit = num(patient.hr_limit) ?? DEFAULT_HR_LIMIT;

  const emergency = {
    name: patient.emergency_contact_name || null,
    // Only wire-tri's schema has this column (emergency_contact_relationship); null hides the row.
    relation: patient.emergency_contact_relationship || patient.emergency_contact_relation || null,
    phone: patient.emergency_contact_phone || null,
  };

  return { exercises, editor, flags, hrLimit, emergency, limits: (selectedRx && selectedRx.limits) || {},
    patientName: patient.name || null, selectedExerciseId: selId };
}

// ---- diff for the Save flow -------------------------------------------------------------------------------

function clamp(v, min, max) {
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(max, Math.max(min, n));
}

function roundToStep(v, step) {
  return Math.round(v / step) * step;
}

// original: the active prescription row for this exercise ({exercise_id, sets, reps, targets, limits,
// rule_overrides}). edited: the editor + flags values currently shown in the UI ({sets, reps, restS,
// kneeTarget, tempoS, weightShiftPct, kneeCaveDeg, trunkLeanDeg}). Returns only the fields that changed from
// `original` (each stepper/slider value is clamped to its limits first, so an out-of-range value never
// reaches the server), or null when nothing changed. `targets`/`rule_overrides` in the result are PARTIAL
// (only the changed keys) — buildPutBody() below merges them with the original before a PUT, because the
// server replaces those columns wholesale (server/db/writes._insert_version).
export function diffPrescription(original, edited) {
  const o = original || {};
  if (!o.exercise_id) return null;
  const oTargets = o.targets || {};
  const oRule = o.rule_overrides || {};
  const limits = o.limits || {};
  const e = edited || {};

  const out = { exercise_id: o.exercise_id };
  let changed = false;

  if (e.sets != null) {
    const sets = clamp(e.sets, 1, 10);
    if (sets !== null && sets !== o.sets) { out.sets = sets; changed = true; }
  }
  if (e.reps != null) {
    const reps = clamp(e.reps, limits.min_reps ?? 1, limits.max_reps ?? 100);
    if (reps !== null && reps !== o.reps) { out.reps = reps; changed = true; }
  }

  const targets = {};
  let targetsChanged = false;
  if (e.kneeTarget != null) {
    const v = clamp(e.kneeTarget, 0, 180);
    if (v !== null && v !== (oTargets.knee_deg ?? null)) { targets.knee_deg = v; targetsChanged = true; }
  }
  if (e.tempoS != null) {
    const v = clamp(e.tempoS, 1, 10);
    if (v !== null && v !== (oTargets.tempo_s ?? DEFAULT_TEMPO_S)) { targets.tempo_s = v; targetsChanged = true; }
  }
  if (e.restS != null) {
    const bounded = clamp(e.restS, limits.min_rest_s ?? 0, limits.max_rest_s ?? 600);
    const v = bounded === null ? null : roundToStep(bounded, 15);
    if (v !== null && v !== (oTargets.rest_s ?? DEFAULT_REST_S)) { targets.rest_s = v; targetsChanged = true; }
  }
  if (targetsChanged) { out.targets = targets; changed = true; }

  const ruleOverrides = {};
  let ruleChanged = false;
  if (e.weightShiftPct != null) {
    const v = clamp(e.weightShiftPct, 0, 100);
    if (v !== null && v !== (oRule.weight_shift_pct ?? DEFAULT_WEIGHT_SHIFT_PCT)) {
      ruleOverrides.weight_shift_pct = v; ruleChanged = true;
    }
  }
  if (e.kneeCaveDeg != null) {
    const v = clamp(e.kneeCaveDeg, 0, 90);
    if (v !== null && v !== (oRule.knee_cave_deg ?? DEFAULT_KNEE_CAVE_DEG)) {
      ruleOverrides.knee_cave_deg = v; ruleChanged = true;
    }
  }
  if (e.trunkLeanDeg != null) {
    const v = clamp(e.trunkLeanDeg, 0, 90);
    if (v !== null && v !== (oRule.trunk_lean_deg ?? DEFAULT_TRUNK_LEAN_DEG)) {
      ruleOverrides.trunk_lean_deg = v; ruleChanged = true;
    }
  }
  if (ruleChanged) { out.rule_overrides = ruleOverrides; changed = true; }

  return changed ? out : null;
}

// The server replaces `targets`/`rule_overrides` wholesale on a PUT (server/db/writes._insert_version), so a
// partial diff must be merged with the original before it's sent, or untouched fields (sessions_per_week,
// hold_s, the other two flag thresholds, ...) would be silently wiped. `diff` is a diffPrescription() result
// for the SAME exercise as `original` (not null).
export function buildPutBody(original, diff) {
  const o = original || {};
  const body = { exercise_id: diff.exercise_id };
  if (diff.sets !== undefined) body.sets = diff.sets;
  if (diff.reps !== undefined) body.reps = diff.reps;
  if (diff.targets !== undefined) body.targets = { ...(o.targets || {}), ...diff.targets };
  if (diff.rule_overrides !== undefined) {
    body.rule_overrides = { ...(o.rule_overrides || {}), ...diff.rule_overrides };
  }
  return body;
}

// hrLimit changed? (Save flow: PATCH /patients/{id}/limits only when this is true.)
export function hrLimitChanged(original, edited) {
  return typeof edited === "number" && Number.isFinite(edited) && edited !== original;
}
