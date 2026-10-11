import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { sessionCardModel, repTimelineModel, trendModel, memoryModel, noteModel } from "../js/screens/patient/model.js";

function sample(name) {
  return JSON.parse(readFileSync(new URL(`../../../server/samples/${name}`, import.meta.url), "utf8"));
}

const session = sample("rest_get_session.json");
const patient = sample("rest_get_patient.json");
const progress = sample("rest_get_patient_progress.json");
const memory = sample("rest_get_patient_memory.json");
const progressNote = sample("rest_get_progress_note.json");

test("sessionCardModel: good/total reps from summary", () => {
  const m = sessionCardModel(session, patient);
  assert.equal(m.good, 4);
  assert.equal(m.total, 4);
});

test("sessionCardModel: avgDepth is null when every rep's peak_angle is null (sample has none yet)", () => {
  const m = sessionCardModel(session, patient);
  assert.equal(m.avgDepth, null);
});

test("sessionCardModel: affected load is 100 - tw_load_left_pct for a right-affected patient, not flagged above threshold", () => {
  const m = sessionCardModel(session, patient);
  const expectedAffected = 100 - session.tw_load_left_pct;
  assert.ok(Math.abs(m.load.affected - expectedAffected) < 0.001);
  assert.equal(m.load.affectedSide, "right");
  assert.equal(m.load.other, session.tw_load_left_pct);
  assert.equal(m.load.flagged, false); // 46.65 is not below 50 - 15 = 35
});

test("sessionCardModel: flagged when affected load drops below 50 - threshold", () => {
  const low = { ...session, tw_load_left_pct: 90 }; // right affected -> affected = 10
  const m = sessionCardModel(low, patient);
  assert.equal(m.load.affected, 10);
  assert.equal(m.load.flagged, true);
});

test("sessionCardModel: a prescription's rule_overrides.weight_shift_pct overrides the default 15% threshold", () => {
  const strict = {
    ...patient,
    prescriptions: patient.prescriptions.map((p) =>
      p.exercise_id === "squat" ? { ...p, rule_overrides: { weight_shift_pct: 2 } } : p),
  };
  // affected = 100 - 53.3484... ~= 46.65, which is below 50 - 2 = 48 with the stricter threshold
  const m = sessionCardModel(session, strict);
  assert.equal(m.load.flagged, true);
});

test("sessionCardModel: flags count and label come from session + rep flags", () => {
  const flagged = {
    ...session,
    flags: ["trunk_lean"],
    reps: session.reps.map((r, i) => (i === 0 ? { ...r, flags: ["weight_shift"] } : r)),
  };
  const m = sessionCardModel(flagged, patient);
  assert.equal(m.flags.count, 2);
  assert.equal(m.flags.label, "2 trunk leans");
});

test("sessionCardModel: pain/effort pass through null when not recorded", () => {
  const m = sessionCardModel(session, patient);
  assert.equal(m.pain, null);
  assert.equal(m.effort, null);
});

test("repTimelineModel: tStart/tEnd are ms elapsed since session start, chained rep to rep", () => {
  const rows = repTimelineModel(session);
  const startMs = Date.parse(session.session.started_at);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].rep, 1);
  assert.equal(rows[0].set, 1);
  assert.equal(rows[0].tStart, 0);
  assert.ok(Math.abs(rows[0].tEnd - (Date.parse(session.reps[0].ended_at) - startMs)) < 1);
  assert.equal(rows[1].tStart, rows[0].tEnd);
  assert.ok(Math.abs(rows[1].tEnd - (Date.parse(session.reps[1].ended_at) - startMs)) < 1);
  assert.equal(rows[0].flagged, false);
  assert.equal(rows[0].label, "Rep 1");
});

test("repTimelineModel: flagged when a rep has flags or quality below 1", () => {
  const withFlag = { ...session, reps: [{ ...session.reps[0], flags: ["weight_shift"] }, session.reps[1]] };
  const rows = repTimelineModel(withFlag);
  assert.equal(rows[0].flagged, true);
  assert.equal(rows[0].label, "Rep 1, flagged");

  const lowQuality = { ...session, reps: [{ ...session.reps[0], quality: 0.5 }, session.reps[1]] };
  assert.equal(repTimelineModel(lowQuality)[0].flagged, true);
});

test("trendModel: reproduces the Figma numbers (depth 70 -> 92, load 47 -> 36) and flags compensation", () => {
  const now = new Date("2026-10-21T00:00:00Z");
  const series = [];
  for (let i = 0; i <= 20; i++) {
    const day = new Date(Date.UTC(2026, 9, 1 + i)).toISOString();
    series.push({
      day,
      p95_knee: 70 + ((92 - 70) * i) / 20,
      affected_load_pct: 47 - ((47 - 36) * i) / 20,
    });
  }
  const m = trendModel({ series }, "right", now, 21);
  assert.equal(m.caption, "Depth 70° → 92°");
  assert.equal(m.loadCaption, "Right leg load 47% → 36%");
  assert.equal(m.compensating, true);
  assert.equal(m.insight, "Depth is up while load on the injured leg drops. Maya is compensating.");
  assert.equal(m.labels.length, 4);
  assert.equal(m.points.depth.length, 21);
  assert.equal(m.points.load.length, 21);
});

test("trendModel: no insight when the patient isn't compensating", () => {
  const now = new Date("2026-10-21T00:00:00Z");
  const series = [
    { day: "2026-10-01T00:00:00Z", p95_knee: 80, affected_load_pct: 45 },
    { day: "2026-10-20T00:00:00Z", p95_knee: 81, affected_load_pct: 44 },
  ];
  const m = trendModel({ series }, "right", now, 21);
  assert.equal(m.compensating, false);
  assert.equal(m.insight, null);
});

test("trendModel: the real progress sample doesn't crash and returns finite captions or null", () => {
  const m = trendModel(progress, "right", new Date("2026-10-11T12:00:00Z"), 21);
  assert.ok(m.points.depth.length > 0);
  assert.ok(typeof m.caption === "string" || m.caption === null);
});

test("memoryModel: subtitle names the last-visit date; items carry an AI-draft flag", () => {
  const m = memoryModel(memory, "2026-10-04T00:00:00Z");
  assert.equal(m.subtitle, "What changed since your visit on Oct 4");
  assert.equal(m.items.length, 3);
  assert.equal(m.items[0].text, memory.events[0].message);
  // The sample events carry no `agent` field; the server's only current writer is agent="insight" (AI).
  assert.equal(m.items[0].aiDraft, true);
});

test("memoryModel: falls back to a relative subtitle with no last-visit date", () => {
  const m = memoryModel(memory, null);
  assert.equal(m.subtitle, "What changed in the last 3 weeks");
});

test("noteModel: an unapproved session shows the AI draft label and no approve-ready text", () => {
  const m = noteModel(session, null);
  assert.equal(m.text, "");
  assert.equal(m.label, "AI draft — review before use");
  assert.equal(m.canApprove, false);
  assert.equal(m.approvedAt, null);
});

test("noteModel: an approved note shows the approval label and text", () => {
  const m = noteModel(session, progressNote);
  assert.equal(m.text, progressNote.text);
  assert.equal(m.label, "Approved by you");
  assert.equal(m.canApprove, false);
  assert.equal(m.approvedAt, progressNote.approved_at);
});

test("noteModel: a draft with text can be approved", () => {
  const withDraft = { ...session, session: { ...session.session, ai_note: "Draft text here." } };
  const m = noteModel(withDraft, null);
  assert.equal(m.text, "Draft text here.");
  assert.equal(m.canApprove, true);
});
