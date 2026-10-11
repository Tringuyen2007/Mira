import { test } from "node:test";
import assert from "node:assert/strict";
import {
  prescribeModel, diffPrescription, buildPutBody, hrLimitChanged, exerciseName,
  DEFAULT_REST_S, DEFAULT_TEMPO_S, DEFAULT_WEIGHT_SHIFT_PCT, DEFAULT_KNEE_CAVE_DEG, DEFAULT_TRUNK_LEAN_DEG,
  DEFAULT_HR_LIMIT,
} from "../js/screens/prescribe/model.js";

import { sample } from "./samples.mjs";

const PATIENT = sample("rest_get_patient.json");
const PRESCRIPTIONS = sample("rest_get_prescriptions.json");
const PROGRESS = sample("rest_get_patient_progress.json");

// ---- exerciseName -------------------------------------------------------------------------------------

test("exerciseName: known ids map to the Figma display name", () => {
  assert.equal(exerciseName("squat"), "Squats");
  assert.equal(exerciseName("glute_bridge"), "Glute bridges");
  assert.equal(exerciseName("single_leg_stance"), "Single-leg stance");
  assert.equal(exerciseName("heel_slide"), "Heel slides");
  assert.equal(exerciseName("straight_leg_raise"), "Straight leg raises");
  assert.equal(exerciseName("sit_to_stand"), "Sit to stand");
  assert.equal(exerciseName("step_up"), "Step-ups");
});

test("exerciseName: unknown id falls back to a humanised, sentence-case reading", () => {
  assert.equal(exerciseName("wall_slide"), "Wall slide");
  assert.equal(exerciseName(""), "Exercise");
});

// ---- prescribeModel -----------------------------------------------------------------------------------

test("prescribeModel: lists active exercises with name/dose, selects the first by default", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS);
  assert.equal(m.exercises.length, 3);
  const squat = m.exercises.find((e) => e.id === "squat");
  assert.equal(squat.name, "Squats");
  assert.equal(squat.dose, "3 × 10");
  assert.equal(squat.active, true);
  assert.equal(m.exercises.filter((e) => e.selected).length, 1);
});

test("prescribeModel: selecting a known exercise id loads it into the editor", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "glute_bridge");
  assert.equal(m.editor.exerciseId, "glute_bridge");
  assert.equal(m.editor.sets, 2);
  assert.equal(m.editor.reps, 10);
  const gb = m.exercises.find((e) => e.id === "glute_bridge");
  assert.equal(gb.selected, true);
});

test("prescribeModel: an unknown selected id falls back to the first exercise", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "not_a_real_id");
  assert.equal(m.editor.exerciseId, m.exercises[0].id);
});

test("prescribeModel: knee target comes from targets.knee_deg when present (squat)", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "squat");
  assert.equal(m.editor.kneeTarget, 90);
});

test("prescribeModel: knee target is null when the exercise has no knee_deg target (glute_bridge)", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "glute_bridge");
  assert.equal(m.editor.kneeTarget, null);
});

test("prescribeModel: defaults - rest 60s, tempo 3s, flags 15% / 10deg / 20deg, hrLimit from patient.hr_limit", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "squat");
  assert.equal(m.editor.restS, DEFAULT_REST_S);
  assert.equal(m.editor.tempoS, DEFAULT_TEMPO_S);
  assert.equal(m.flags.weightShiftPct, DEFAULT_WEIGHT_SHIFT_PCT);
  assert.equal(m.flags.kneeCaveDeg, DEFAULT_KNEE_CAVE_DEG);
  assert.equal(m.flags.trunkLeanDeg, DEFAULT_TRUNK_LEAN_DEG);
  assert.equal(m.hrLimit, PATIENT.patient.hr_limit);
});

test("prescribeModel: hrLimit falls back to 120 when the patient has none", () => {
  const patient = { patient: { ...PATIENT.patient, hr_limit: null } };
  const m = prescribeModel(patient, PRESCRIPTIONS, PROGRESS, "squat");
  assert.equal(m.hrLimit, DEFAULT_HR_LIMIT);
});

test("prescribeModel: emergency contact read from the patient record", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS);
  assert.equal(m.emergency.name, PATIENT.patient.emergency_contact_name);
  assert.equal(m.emergency.phone, PATIENT.patient.emergency_contact_phone);
});

test("prescribeModel: todayAvg reads the most recent progress day's p95_knee for that exercise", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, PROGRESS, "squat");
  const expected = Math.round(PROGRESS.series[PROGRESS.series.length - 1].p95_knee);
  assert.equal(m.editor.todayAvg, `Today's average was ${expected}°.`);
});

test("prescribeModel: todayAvg is null with no matching progress series", () => {
  const m = prescribeModel(PATIENT, PRESCRIPTIONS, { series: [] }, "squat");
  assert.equal(m.editor.todayAvg, null);
});

// ---- diffPrescription -----------------------------------------------------------------------------------

const SQUAT_RX = PRESCRIPTIONS.prescriptions.find((p) => p.exercise_id === "squat");

test("diffPrescription: no changes -> null", () => {
  const edited = { sets: SQUAT_RX.sets, reps: SQUAT_RX.reps, restS: DEFAULT_REST_S, kneeTarget: SQUAT_RX.targets.knee_deg,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: DEFAULT_WEIGHT_SHIFT_PCT, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  assert.equal(diffPrescription(SQUAT_RX, edited), null);
});

test("diffPrescription: only emits the fields that actually changed", () => {
  const edited = { sets: SQUAT_RX.sets, reps: 8, restS: DEFAULT_REST_S, kneeTarget: SQUAT_RX.targets.knee_deg,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: DEFAULT_WEIGHT_SHIFT_PCT, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  const diff = diffPrescription(SQUAT_RX, edited);
  assert.deepEqual(diff, { exercise_id: "squat", reps: 8 });
});

test("diffPrescription: a knee-target change goes to targets.knee_deg", () => {
  const edited = { sets: SQUAT_RX.sets, reps: SQUAT_RX.reps, restS: DEFAULT_REST_S, kneeTarget: 100,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: DEFAULT_WEIGHT_SHIFT_PCT, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  const diff = diffPrescription(SQUAT_RX, edited);
  assert.deepEqual(diff, { exercise_id: "squat", targets: { knee_deg: 100 } });
});

test("diffPrescription: a flag-threshold change goes to rule_overrides", () => {
  const edited = { sets: SQUAT_RX.sets, reps: SQUAT_RX.reps, restS: DEFAULT_REST_S, kneeTarget: SQUAT_RX.targets.knee_deg,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: 25, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG, trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  const diff = diffPrescription(SQUAT_RX, edited);
  assert.deepEqual(diff, { exercise_id: "squat", rule_overrides: { weight_shift_pct: 25 } });
});

test("diffPrescription: rest is clamped to the prescription's limits and rounded to a 15s step", () => {
  const edited = { sets: SQUAT_RX.sets, reps: SQUAT_RX.reps, restS: 9999, kneeTarget: SQUAT_RX.targets.knee_deg,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: DEFAULT_WEIGHT_SHIFT_PCT, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  const diff = diffPrescription(SQUAT_RX, edited);
  assert.equal(diff.targets.rest_s, SQUAT_RX.limits.max_rest_s);
});

test("diffPrescription: reps is clamped within min_reps/max_reps (limits respected)", () => {
  const edited = { sets: SQUAT_RX.sets, reps: 999, restS: DEFAULT_REST_S, kneeTarget: SQUAT_RX.targets.knee_deg,
    tempoS: DEFAULT_TEMPO_S, weightShiftPct: DEFAULT_WEIGHT_SHIFT_PCT, kneeCaveDeg: DEFAULT_KNEE_CAVE_DEG,
    trunkLeanDeg: DEFAULT_TRUNK_LEAN_DEG };
  const diff = diffPrescription(SQUAT_RX, edited);
  assert.equal(diff.reps, SQUAT_RX.limits.max_reps);
});

test("diffPrescription: untouched exercise (no original) -> null", () => {
  assert.equal(diffPrescription(null, { sets: 3 }), null);
});

// ---- buildPutBody ---------------------------------------------------------------------------------------

test("buildPutBody: merges a partial targets diff onto the original so other target keys survive", () => {
  const diff = { exercise_id: "squat", targets: { knee_deg: 100 } };
  const body = buildPutBody(SQUAT_RX, diff);
  assert.equal(body.targets.knee_deg, 100);
  assert.equal(body.targets.sessions_per_week, SQUAT_RX.targets.sessions_per_week);
});

test("buildPutBody: a sets-only diff carries no targets/rule_overrides key", () => {
  const diff = { exercise_id: "squat", sets: 4 };
  const body = buildPutBody(SQUAT_RX, diff);
  assert.equal(body.sets, 4);
  assert.equal("targets" in body, false);
  assert.equal("rule_overrides" in body, false);
});

// ---- hrLimitChanged ---------------------------------------------------------------------------------------

test("hrLimitChanged", () => {
  assert.equal(hrLimitChanged(130, 130), false);
  assert.equal(hrLimitChanged(130, 140), true);
  assert.equal(hrLimitChanged(130, null), false);
});
