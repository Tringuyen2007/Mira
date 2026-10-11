// Patient review "Apple Watch" view-models (js/screens/patient/watchModel.js) against server/samples/
// rest_get_patient_watch.json and hand-built edge cases: data shaping, empty states, the Simulated label, and the
// tremor-monitoring flag/event handling.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  hrRecoveryModel, rotationModel, tremorModel, tremorMonitoringOf, watchFeaturesFromEvent, watchGroupModel,
  fmtMinutes,
} from "../js/screens/patient/watchModel.js";

import { sample } from "./samples.mjs";

const WATCH = sample("rest_get_patient_watch.json");
const FEATURES_EVENT = sample("ws_events_watch_features_changed.json");

test("hrRecoveryModel: the sample's one set gives 'Avg drop 22 bpm in 1 min' and the peak -> 60 s line", () => {
  const m = hrRecoveryModel(WATCH);
  assert.equal(m.empty, false);
  assert.equal(m.summary, "Avg drop 22 bpm in 1 min");
  assert.equal(m.latest, "Last set: 126 → 104 bpm");
  assert.deepEqual(m.points, [{ x: 1, y: 22 }]);
});

test("hrRecoveryModel: oldest first, averaged and rounded; rows without a numeric drop are skipped", () => {
  const m = hrRecoveryModel({ hr_recovery: [
    { drop_bpm: 30, peak_bpm: 140, bpm_60s: 110, time: "2026-10-11T09:10:00Z" },
    { drop_bpm: null, time: "2026-10-11T09:05:00Z" },
    { drop_bpm: 15, peak_bpm: 120, bpm_60s: 105, time: "2026-10-10T09:00:00Z" },
    { drop_bpm: 20, time: "2026-10-10T09:05:00Z" },
  ] });
  assert.deepEqual(m.points.map((p) => p.y), [15, 20, 30]);
  assert.equal(m.avgDrop, Math.round((15 + 20 + 30) / 3));
  assert.equal(m.summary, "Avg drop 22 bpm in 1 min");
  assert.equal(m.latest, "Last set: 140 → 110 bpm");
  assert.equal(m.count, 3);
});

test("hrRecoveryModel: empty for a missing or empty list", () => {
  assert.equal(hrRecoveryModel(null).empty, true);
  assert.equal(hrRecoveryModel({ hr_recovery: [] }).summary, null);
});

test("rotationModel: per exercise best/avg vs goal; goal reached carries text, not just a flag", () => {
  const m = rotationModel(WATCH);
  assert.equal(m.empty, false);
  const [ex] = m.exercises;
  assert.equal(ex.name, "Forearm turns");
  assert.equal(ex.best, 82);
  assert.equal(ex.avg, 72);
  assert.equal(ex.goal, 80);
  assert.equal(ex.reached, true);
  assert.equal(ex.status, "Goal reached");
  assert.equal(ex.line, "Best 82° · avg 72° · goal 80°");
});

test("rotationModel: groups sets by exercise, best across sets, goal from the newest set, 'N° short of goal'", () => {
  const m = rotationModel({ rotation_sets: [
    { exercise_id: "wrist_flexion", best_deg: 50, avg_deg: 40, goal_deg: 60, time: "2026-10-10T09:00:00Z" },
    { exercise_id: "wrist_flexion", best_deg: 55, avg_deg: 50, goal_deg: 70, time: "2026-10-11T09:00:00Z" },
    { exercise_id: "forearm_rotation", best_deg: 90, avg_deg: 85, goal_deg: 80, time: "2026-10-11T09:00:00Z" },
  ] });
  assert.deepEqual(m.exercises.map((e) => e.exerciseId), ["forearm_rotation", "wrist_flexion"]);
  const wrist = m.exercises[1];
  assert.equal(wrist.sets, 2);
  assert.equal(wrist.best, 55);
  assert.equal(wrist.avg, 45);
  assert.equal(wrist.goal, 70);
  assert.equal(wrist.reached, false);
  assert.equal(wrist.status, "15° short of goal");
});

test("rotationModel: empty with no sets", () => {
  assert.deepEqual(rotationModel({ rotation_sets: [] }), { empty: true, exercises: [] });
});

test("tremorModel: sample day is stacked minutes, labelled Simulated, with a headline and dyskinesia", () => {
  const m = tremorModel(WATCH, { monitoring: true });
  assert.equal(m.empty, false);
  assert.equal(m.simulated, true);
  assert.equal(m.days.length, 1);
  assert.deepEqual(m.days[0].minutes, { none: 420, slight: 190, mild: 25, strong: 0 });
  assert.equal(m.days[0].total, 635);
  assert.equal(m.maxTotal, 635);
  assert.equal(m.latest, "Oct 11: Mild for 25 min · Extra movements: few");
  assert.match(m.days[0].text, /slight 3 h 10 min/);
});

test("tremorModel: device-sourced data is not labelled Simulated; days sort oldest first", () => {
  const m = tremorModel({ tremor: [
    { day: "2026-10-11", none_min: 600, slight_min: 0, mild_min: 0, strong_min: 0, dyskinesia: "none", source: "device" },
    { day: "2026-10-09", none_min: 500, slight_min: 60, mild_min: 0, strong_min: 0, dyskinesia: "some", source: "device" },
  ] });
  assert.equal(m.simulated, false);
  assert.deepEqual(m.days.map((d) => d.day), ["2026-10-09", "2026-10-11"]);
  assert.equal(m.latest, "Oct 11: No tremor · Extra movements: none");
});

test("tremorModel: the empty state says why (monitoring off vs no data yet)", () => {
  assert.equal(tremorModel({ tremor: [] }, { monitoring: false }).emptyText, "Tremor monitoring is off.");
  assert.equal(tremorModel({ tremor: [] }, { monitoring: true }).emptyText, "No tremor data from the watch yet.");
});

test("fmtMinutes", () => {
  assert.equal(fmtMinutes(190), "3 h 10 min");
  assert.equal(fmtMinutes(120), "2 h");
  assert.equal(fmtMinutes(25), "25 min");
  assert.equal(fmtMinutes(null), "0 min");
});

test("tremorMonitoringOf reads GET /patients/{id}.patient.tremor_monitoring (absent -> off)", () => {
  assert.equal(tremorMonitoringOf({ patient: { tremor_monitoring: true } }), true);
  assert.equal(tremorMonitoringOf({ patient: {} }), false);
  assert.equal(tremorMonitoringOf(null), false);
});

test("watchFeaturesFromEvent: only this patient's watch_features_changed counts", () => {
  assert.equal(watchFeaturesFromEvent(FEATURES_EVENT, "maya"), true);
  assert.equal(watchFeaturesFromEvent(FEATURES_EVENT, "sam"), null);
  assert.equal(watchFeaturesFromEvent({ type: "limits_changed", patient_id: "maya" }, "maya"), null);
  assert.equal(watchFeaturesFromEvent({ ...FEATURES_EVENT, tremor_monitoring: false }, "maya"), false);
});

test("watchGroupModel: empty only when all three are empty", () => {
  assert.equal(watchGroupModel({ hr_recovery: [], rotation_sets: [], tremor: [] }, null).empty, true);
  assert.equal(watchGroupModel(WATCH, { patient: { tremor_monitoring: true } }).empty, false);
});
