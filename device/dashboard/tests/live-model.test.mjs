import { test } from "node:test";
import assert from "node:assert/strict";
import {
  liveModel, cueHistoryModel, breatherModel, hrRecoveryLine, rotationLiveModel,
} from "../js/screens/live/model.js";

import { sample } from "./samples.mjs";

const liveSnapshot = sample("ws_live.json");
const patient = sample("rest_get_patient.json").patient;
const cueEvent = sample("ws_events_cue.json");

test("liveModel: inactive when there's no snapshot", () => {
  const m = liveModel(null, patient, new Date());
  assert.equal(m.active, false);
});

test("liveModel: rep/line/hr text match the Figma copy shape", () => {
  const m = liveModel(liveSnapshot, patient, new Date(liveSnapshot.t * 1000));
  assert.equal(m.active, true);
  assert.equal(m.rep, "Rep 7");
  assert.equal(m.line, "Mini squats · set 1 of 3");
  assert.equal(m.hr, "103 bpm");
  assert.equal(m.paused, false);
  assert.equal(m.incident, false);
});

test("liveModel: hr is null when not provided", () => {
  const m = liveModel({ ...liveSnapshot, hr: null }, patient, new Date(liveSnapshot.t * 1000));
  assert.equal(m.hr, null);
});

test("liveModel: latency names the pose rate and an about-N-ms-behind figure", () => {
  const now = new Date((liveSnapshot.t + 0.14) * 1000);
  const m = liveModel(liveSnapshot, patient, now);
  assert.match(m.latency, /^20 Hz · about 140 ms behind · smoothed$/);
});

test("liveModel: under 50 ms, negative (server clock ahead) or implausibly large reads 'live', never '0 ms'", () => {
  for (const dt of [0.01, -0.3, 30]) {
    const m = liveModel(liveSnapshot, patient, new Date((liveSnapshot.t + dt) * 1000));
    assert.equal(m.latency, "20 Hz · live · smoothed", String(dt));
  }
});

test("liveModel: balance uses the patient's affected side (right for Maya) from load_l_pct", () => {
  const m = liveModel(liveSnapshot, patient, new Date(liveSnapshot.t * 1000));
  // load_l_pct 56.5 -> left 57 (rounded once), right 43: the two labels always add to 100.
  assert.equal(m.balance.affected, 43);
  assert.equal(m.balance.other, 57);
  assert.equal(m.balance.affectedSide, "right");
  assert.equal(m.balance.flagged, false);
});

test("liveModel: balance.flagged is true when the snapshot carries flags", () => {
  const m = liveModel({ ...liveSnapshot, flags: ["weight_shift"] }, patient, new Date(liveSnapshot.t * 1000));
  assert.equal(m.balance.flagged, true);
});

test("liveModel: title uses the first name from the snapshot", () => {
  const m = liveModel(liveSnapshot, patient, new Date(liveSnapshot.t * 1000));
  assert.match(m.title, /Maya/);
});

test("cueHistoryModel: maps cue events to {time, text}, newest first", () => {
  const events = [
    { ...cueEvent, id: "a", text: "Great work, keep going", t: 1791662220 },
    { ...cueEvent, id: "b", text: "Knees over your toes", t: 1791662460 },
    { type: "set_done", t: 1791662500 }, // not a cue — ignored
  ];
  const rows = cueHistoryModel(events);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].text, "Knees over your toes");
  assert.equal(rows[1].text, "Great work, keep going");
  assert.match(rows[0].time, /^\d{1,2}:\d{2}$/);
});

test("cueHistoryModel: empty/missing input is an empty list", () => {
  assert.deepEqual(cueHistoryModel(null), []);
  assert.deepEqual(cueHistoryModel([]), []);
});

// ---- Apple Watch events --------------------------------------------------------------------------------------

test("breatherModel: start -> 'Breather — 128 bpm over limit 120'; end clears the banner with how it ended", () => {
  const start = sample("ws_events_breather.json");
  assert.deepEqual(breatherModel(start), { active: true, text: "Breather — 128 bpm over limit 120" });
  const end = breatherModel({ ...start, state: "end", bpm: 112, resolution: "hr_recovered" });
  assert.equal(end.active, false);
  assert.match(end.text, /heart rate back under the limit/);
  assert.equal(breatherModel({ ...start, state: "end", resolution: "escalated" }).active, false);
  assert.equal(breatherModel({ type: "cue" }), null);
});

test("hrRecoveryLine: the latest set's peak -> 60 s drop", () => {
  assert.equal(hrRecoveryLine(sample("ws_events_hr_recovery.json")),
    "Set 2 recovery: 126 → 104 bpm, dropped 22 bpm in 1 min");
  assert.equal(hrRecoveryLine({ type: "hr_recovery" }), null);
});

test("rotationLiveModel: a rep's angle vs goal (icon + text status) and a set's best/avg", () => {
  const rep = rotationLiveModel(sample("ws_events_rotation_rep.json"));
  assert.equal(rep.text, "Rep 4 of 10 · 68° of 80° goal");
  assert.equal(rep.reached, false);
  assert.equal(rep.status, "12° short");
  assert.equal(rep.exerciseId, "forearm_rotation");
  const hit = rotationLiveModel({ ...sample("ws_events_rotation_rep.json"), angle_deg: 81, reached: true });
  assert.equal(hit.status, "Goal reached");
  const set = rotationLiveModel(sample("ws_events_rotation_set.json"));
  assert.equal(set.text, "Set 1 done · best 82°, avg 74° (goal 80°)");
  assert.equal(set.reached, true);
  assert.equal(rotationLiveModel({ type: "rep" }), null);
});
