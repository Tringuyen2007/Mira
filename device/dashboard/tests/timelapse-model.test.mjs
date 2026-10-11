import { test } from "node:test";
import assert from "node:assert/strict";
import { bestRep, timelapseModel, syncClock } from "../js/screens/timelapse/model.js";

import { sample } from "./samples.mjs";

const session = sample("rest_get_session.json");
const patientJson = sample("rest_get_patient.json");

test("bestRep: picks the unflagged rep with the highest peak_angle", () => {
  const s = {
    ...session,
    reps: [
      { ...session.reps[0], rep: 1, peak_angle: 70, flags: [] },
      { ...session.reps[1], rep: 2, peak_angle: 92, flags: [] },
      { ...session.reps[1], rep: 3, peak_angle: 99, flags: ["knee_cave_r"] },
    ],
  };
  const best = bestRep(s);
  assert.equal(best.rep, 2);
  assert.ok(Number.isFinite(best.tStart));
  assert.ok(best.tEnd > best.tStart);
});

test("bestRep: falls back to any rep (by peak_angle) when every rep is flagged", () => {
  const s = {
    ...session,
    reps: [
      { ...session.reps[0], rep: 1, peak_angle: 70, flags: ["weight_shift"] },
      { ...session.reps[1], rep: 2, peak_angle: 92, flags: ["trunk_lean"] },
    ],
  };
  const best = bestRep(s);
  assert.equal(best.rep, 2);
});

test("bestRep: null when the session has no reps", () => {
  assert.equal(bestRep({ ...session, reps: [] }), null);
});

test("syncClock: maps one shared 0..1 progress onto two rep durations", () => {
  assert.deepEqual(syncClock(4000, 2000, 0), { tA: 0, tB: 0 });
  assert.deepEqual(syncClock(4000, 2000, 0.5), { tA: 2000, tB: 1000 });
  assert.deepEqual(syncClock(4000, 2000, 1), { tA: 4000, tB: 2000 });
});

test("syncClock: clamps t outside [0,1]", () => {
  assert.deepEqual(syncClock(1000, 1000, -1), { tA: 0, tB: 0 });
  assert.deepEqual(syncClock(1000, 1000, 2), { tA: 1000, tB: 1000 });
});

test("timelapseModel: left/right labels, dates and the depth/load headline", () => {
  const sessA = {
    ...session,
    session: { ...session.session, started_at: "2026-09-20T10:00:00Z" },
    reps: [{ ...session.reps[0], rep: 1, peak_angle: 70, flags: [] }],
    tw_load_left_pct: 64, // right-affected patient -> affected (right) = 36
  };
  const sessB = {
    ...session,
    session: { ...session.session, started_at: "2026-10-11T10:00:00Z" },
    reps: [{ ...session.reps[0], rep: 1, peak_angle: 92, flags: [] }],
    tw_load_left_pct: 53, // affected (right) = 47
  };
  const m = timelapseModel(sessA, sessB, patientJson);
  assert.equal(m.left.date, "Sep 20");
  assert.equal(m.right.date, "Oct 11");
  assert.equal(m.left.depth, 70);
  assert.equal(m.right.depth, 92);
  assert.equal(Math.round(m.left.load), 36);
  assert.equal(Math.round(m.right.load), 47);
  assert.equal(m.headline, "Depth +22°, right-leg load 36% → 47%");
});

test("framesDepth, weekLabel and a capitalised headline when depth comes only from the frames", async () => {
  const { framesDepth, weekLabel } = await import("../js/screens/timelapse/model.js");
  assert.equal(framesDepth([{ knee_l: 60, knee_r: 70 }, { knee_l: 90, knee_r: 94 }, { knee_l: null, knee_r: 80 }]), 92);
  assert.equal(framesDepth([]), null);
  assert.equal(weekLabel("2026-10-11T10:00:00Z", "2026-07-23T10:00:00Z", "2026-01-01T00:00:00Z"), "Week 12");
  assert.equal(weekLabel("2026-07-23T10:00:00Z", "2026-07-23T10:00:00Z", null), "Week 1");
  const noPeak = (s, load) => ({ ...s, reps: s.reps.map((r) => ({ ...r, peak_angle: null })), tw_load_left_pct: load });
  const m = timelapseModel(noPeak(session, 64), noPeak(session, 53), patientJson, { depthA: 70, depthB: 92 });
  assert.equal(m.headline, "Depth +22°, right-leg load 36% → 47%");
  const loadOnly = timelapseModel(noPeak(session, 64), noPeak(session, 53), patientJson);
  assert.equal(loadOnly.headline, "Right-leg load 36% → 47%");
});
