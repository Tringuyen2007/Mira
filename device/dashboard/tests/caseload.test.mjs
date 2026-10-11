import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { caseloadModel } from "../js/screens/caseload/model.js";
import { proposalSummary } from "../js/ui/proposalCard.js";

const figma = JSON.parse(
  readFileSync(new URL("./fixtures/caseload-figma.json", import.meta.url), "utf8"));
const realCaseload = JSON.parse(
  readFileSync(new URL("../../../server/samples/rest_get_caseload.json", import.meta.url), "utf8"));
const realProposals = JSON.parse(
  readFileSync(new URL("../../../server/samples/rest_get_proposals.json", import.meta.url), "utf8"));

const now = new Date("2026-10-11T15:00:00");

const figmaProposals = {
  proposals: [
    {
      id: 1, patient_id: "maya", patient_name: "Maya Ruiz", created_at: "2026-10-11T10:00:00",
      rationale: "Maya is compensating onto her left leg during squats.",
      change: { add: ["weight_shift_drill"] }, status: "pending",
    },
    {
      id: 2, patient_id: "robert", patient_name: "Robert Okafor", created_at: "2026-10-11T09:00:00",
      rationale: "Robert's pain rose after back-to-back squat days.",
      change: { exercise_id: "squat", sets: 3, reps: 8 }, status: "pending",
    },
    {
      id: 3, patient_id: "george", patient_name: "George Alvarez", created_at: "2026-10-11T08:00:00",
      rationale: "George's knee bend target should rise as form stabilizes.",
      change: { targets: { knee_deg: 95 } }, status: "pending",
    },
  ],
};

test("subtitle uses counts.all", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  assert.equal(m.subtitle, "9 patients, sorted by who needs you first");
});

test("tabs carry counts and mark the active one, 'needs' maps to needs_you", () => {
  const m = caseloadModel(figma, figmaProposals, { filter: "needs", now });
  const byKey = Object.fromEntries(m.tabs.map((t) => [t.key, t]));
  assert.equal(byKey.all.label, "All");
  assert.equal(byKey.all.count, 9);
  assert.equal(byKey.needs.label, "Needs you");
  assert.equal(byKey.needs.count, 3);
  assert.equal(byKey.watch.count, 2);
  assert.equal(byKey.on_track.label, "On track");
  assert.equal(byKey.on_track.count, 4);
  assert.equal(byKey.needs.active, true);
  assert.equal(byKey.all.active, false);
});

test("default filter is 'all'", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const byKey = Object.fromEntries(m.tabs.map((t) => [t.key, t]));
  assert.equal(byKey.all.active, true);
});

function allRows(m) {
  return m.groups.flatMap((g) => g.rows);
}

test("row sub capitalises the condition and appends the week", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const maya = allRows(m).find((r) => r.id === "maya");
  assert.equal(maya.sub, "Right knee replacement · wk 3");
});

test("row when: last session today, or yesterday, or live now", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const rows = allRows(m);
  assert.equal(rows.find((r) => r.id === "maya").when, "Last session today, 9:10");
  assert.equal(rows.find((r) => r.id === "robert").when, "Last session yesterday");
  assert.equal(rows.find((r) => r.id === "linda").when, "Last session Oct 7");
  assert.equal(rows.find((r) => r.id === "george").when, "Live now");
});

test("evidence tone is alert for needs-you rows, plain otherwise", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const rows = allRows(m);
  assert.equal(rows.find((r) => r.id === "maya").evidenceTone, "alert");
  assert.equal(rows.find((r) => r.id === "george").evidenceTone, "plain");
  assert.equal(rows.find((r) => r.id === "james").evidenceTone, "plain");
});

test("row href points at the patient route", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const maya = allRows(m).find((r) => r.id === "maya");
  assert.equal(maya.href, "#/patients/maya");
});

test("filter on_track shows only that group, 1 row + moreOnTrack: 3 until showAllOnTrack", () => {
  const m = caseloadModel(figma, figmaProposals, { filter: "on_track", now });
  assert.equal(m.groups.length, 1);
  assert.equal(m.groups[0].key, "on_track");
  assert.equal(m.groups[0].rows.length, 1);
  assert.equal(m.groups[0].rows[0].id, "james");
  assert.equal(m.moreOnTrack, 3);

  const mAll = caseloadModel(figma, figmaProposals, { filter: "on_track", now, showAllOnTrack: true });
  assert.equal(mAll.groups[0].rows.length, 4);
  assert.equal(mAll.moreOnTrack, 0);
});

test("filter all also truncates the on_track group to 1 row", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  const onTrack = m.groups.find((g) => g.key === "on_track");
  assert.equal(onTrack.rows.length, 1);
  assert.equal(m.moreOnTrack, 3);
});

test("search matches case-insensitively across all buckets", () => {
  const m = caseloadModel(figma, figmaProposals, { search: "lin", now });
  const rows = allRows(m);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "Linda Park");
});

test("search bypasses the on_track truncation", () => {
  const m = caseloadModel(figma, figmaProposals, { search: "james", now });
  const onTrack = m.groups.find((g) => g.key === "on_track");
  assert.equal(onTrack.rows.length, 1);
  assert.equal(m.moreOnTrack, 0);
});

test("today reflects sessions_done and whether anyone is live", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  assert.equal(m.today.done, 4);
  assert.equal(m.today.live, 1);
});

test("live card is built from today.live_now", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  assert.deepEqual(m.live, { name: "George Alvarez", line: "Squats · set 2 of 3 · rep 7", href: "#/live/george" });
});

test("live is null when nobody is live", () => {
  const noLive = { ...figma, today: { ...figma.today, live_now: null } };
  const m = caseloadModel(noLive, figmaProposals, { now });
  assert.equal(m.live, null);
});

test("copilot items: first name + proposalSummary, up to 3", () => {
  const m = caseloadModel(figma, figmaProposals, { now });
  assert.equal(m.copilot.count, 3);
  assert.deepEqual(m.copilot.items, [
    { text: "Maya: add a weight-shift drill" },
    { text: "Robert: change squats to 3 × 8" },
    { text: "George: raise knee bend target to 95°" },
  ]);
});

test("copilot is null when proposals failed to load", () => {
  const m = caseloadModel(figma, null, { now });
  assert.equal(m.copilot, null);
});

test("proposalSummary: add", () => {
  assert.equal(proposalSummary({ change: { add: ["weight_shift_drill"] } }), "add a weight-shift drill");
});

test("proposalSummary: sets/reps", () => {
  assert.equal(proposalSummary({ change: { exercise_id: "squat", sets: 3, reps: 8 } }), "change squats to 3 × 8");
});

test("proposalSummary: targets.knee_deg", () => {
  assert.equal(proposalSummary({ change: { targets: { knee_deg: 95 } } }), "raise knee bend target to 95°");
});

test("proposalSummary falls back to the first sentence of the rationale", () => {
  assert.equal(
    proposalSummary({ change: {}, rationale: "Something changed. More detail here." }),
    "Something changed.");
});

// Shape-level assertions against the real (3-patient) server sample, so a server contract change breaks this test.
test("model runs against the real server sample and keeps the response shape", () => {
  const m = caseloadModel(realCaseload, realProposals, { now: new Date(realCaseload.generated_at) });
  assert.equal(typeof m.dateLabel, "string");
  assert.equal(typeof m.subtitle, "string");
  assert.equal(m.tabs.length, 4);
  for (const t of m.tabs) {
    assert.equal(typeof t.key, "string");
    assert.equal(typeof t.label, "string");
    assert.equal(typeof t.count, "number");
    assert.equal(typeof t.active, "boolean");
  }
  const totalRows = m.groups.reduce((n, g) => n + g.count, 0);
  assert.equal(totalRows, realCaseload.counts.all);
  for (const g of m.groups) {
    assert.equal(typeof g.key, "string");
    assert.equal(typeof g.label, "string");
    assert.equal(typeof g.count, "number");
    for (const r of g.rows) {
      assert.equal(typeof r.id, "string");
      assert.equal(typeof r.name, "string");
      assert.equal(typeof r.age, "number");
      assert.equal(typeof r.sub, "string");
      assert.ok(["needs", "watch", "ok"].includes(r.statusIcon));
      assert.equal(typeof r.reason, "string");
      assert.equal(typeof r.when, "string");
      assert.equal(typeof r.evidence, "string");
      assert.ok(["alert", "plain"].includes(r.evidenceTone));
      assert.match(r.href, /^#\/patients\//);
    }
  }
  assert.equal(typeof m.moreOnTrack, "number");
  assert.equal(typeof m.today.done, "number");
  assert.equal(typeof m.today.live, "number");
  if (m.copilot) {
    assert.equal(typeof m.copilot.count, "number");
    for (const item of m.copilot.items) assert.equal(typeof item.text, "string");
  }
  if (m.live) {
    assert.equal(typeof m.live.name, "string");
    assert.equal(typeof m.live.line, "string");
    assert.match(m.live.href, /^#\/live\//);
  }
});
