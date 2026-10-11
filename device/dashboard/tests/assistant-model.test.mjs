import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { answerModel, contextFrom, proposalsTabsModel, fallbackNotice } from "../js/screens/assistant/model.js";

const load = (rel) => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8"));
const realAsk = load("../../../server/samples/rest_post_copilot_ask.json");
const realReview = load("../../../server/samples/rest_post_copilot_review.json");
const realProposals = load("../../../server/samples/rest_get_proposals.json");
const figma = load("./fixtures/assistant-figma.json");

test("answerModel splits the real answer into a paragraph, a numbered list and trailing paragraphs", () => {
  const m = answerModel(realAsk);
  assert.equal(m.blocks[0].type, "p");
  assert.match(m.blocks[0].text, /^Three patients are compensating/);
  assert.equal(m.blocks[1].type, "ol");
  assert.equal(m.blocks[1].items.length, 3);
  assert.match(m.blocks[1].items[0], /^Maya Ellis \(right side\)/);
  assert.doesNotMatch(m.blocks[1].items[0], /^1\./);
  assert.deepEqual(m.blocks.slice(2).map((b) => b.type), ["p", "p"]);
  assert.equal(m.text, realAsk.answer);
});

test("answerModel splits blank-line paragraphs and dash lists (Figma fixture)", () => {
  const m = answerModel(figma);
  assert.deepEqual(m.blocks.map((b) => b.type), ["p", "p", "ul"]);
  assert.equal(m.blocks[1].text, "What I would check next:");
  assert.deepEqual(m.blocks[2].items, ["Pain after back-to-back days", "Weight shift on the last set"]);
});

test("charts become lineChart input with {x, y} points", () => {
  const m = answerModel(realAsk);
  assert.equal(m.charts.length, 1);
  const c = m.charts[0];
  assert.equal(c.title, "Maya Ellis: squat depth vs load on the affected leg");
  assert.equal(c.yLabel, "degrees / % body weight");
  assert.equal(c.series.length, 2);
  assert.equal(c.series[0].name, "p95 knee depth (°)");
  assert.deepEqual(c.series[0].points[0], { x: "2026-09-19", y: 57.7 });
  assert.deepEqual(c.series[1].points.at(-1), { x: "2026-09-24", y: 44.6 });
});

test("charts drop non-finite points and empty series", () => {
  const m = answerModel({ answer: "x", charts: [
    { type: "line", title: "t", series: [{ name: "a", points: [["d", "oops"], ["e", 2]] }, { name: "b", points: [] }] },
    { type: "line", title: "empty", series: [] },
  ] });
  assert.equal(m.charts.length, 1);
  assert.deepEqual(m.charts[0].series, [{ name: "a", points: [{ x: "e", y: 2 }] }]);
});

test("evidence chips, sql queries, cached flag", () => {
  const m = answerModel(realAsk);
  assert.equal(m.evidence.length, 2);
  assert.match(m.evidence[0], /^progress_v/);
  assert.equal(m.sql.length, 3);
  assert.match(m.sql[0], /^WITH w AS/);
  assert.equal(m.cached, false);
  assert.equal(answerModel({ ...realAsk, cached: true }).cached, true);
  assert.deepEqual(answerModel({ answer: "a", sql: ["SELECT 1"] }).sql, ["SELECT 1"]);
});

test("proposals note is neutral and pluralised", () => {
  assert.equal(answerModel(realAsk).proposalsNote, "I drafted 1 change to the plan. See Proposals.");
  assert.equal(answerModel({ ...realAsk, proposals: [1, 2] }).proposalsNote,
    "I drafted 2 changes to the plan. See Proposals.");
  assert.equal(answerModel({ ...realAsk, proposals: [] }).proposalsNote, null);
  assert.deepEqual(answerModel(realReview).proposalIds, [1]);
});

test("drafts are kept with their patient id", () => {
  assert.deepEqual(answerModel(realAsk).drafts, []);
  const m = answerModel(figma);
  assert.equal(m.drafts.length, 1);
  assert.equal(m.drafts[0].patientId, "linda");
  assert.match(m.drafts[0].text, /^Hi Linda/);
  assert.deepEqual(answerModel({ answer: "a", drafts: [{ patient_id: "x" }, null] }).drafts, []);
});

test("fallback notice text", () => {
  assert.equal(answerModel(realAsk).fallback, null);
  const m = answerModel({ answer: "I couldn't answer that right now.", fallback: "step limit", sql: [] });
  assert.equal(m.fallback, "I couldn't finish this analysis: step limit. Here is what I found.");
  assert.equal(fallbackNotice("ai unavailable: timeout."),
    "I couldn't finish this analysis: ai unavailable: timeout. Here is what I found.");
});

test("answerModel tolerates junk", () => {
  const m = answerModel(null);
  assert.deepEqual(m.blocks, []);
  assert.deepEqual(m.charts, []);
  assert.equal(m.proposalsNote, null);
  assert.equal(m.text, "");
});

test("contextFrom keeps the last 3 completed turns, answer capped at 1200", () => {
  const long = "x".repeat(1500);
  const turns = [
    { question: "q1", status: "done", answerText: "a1" },
    { question: "q2", status: "error", answerText: "" },
    { question: "q3", status: "done", answerText: long },
    { question: "q4", status: "done", answerText: "a4" },
    { question: "q5", status: "pending", answerText: "" },
    { question: "q6", status: "done", answerText: "a6" },
  ];
  const ctx = contextFrom(turns);
  assert.deepEqual(ctx.map((t) => t.question), ["q3", "q4", "q6"]);
  assert.equal(ctx[0].answer.length, 1200);
  assert.deepEqual(ctx[1], { question: "q4", answer: "a4" });
  assert.deepEqual(contextFrom([]), []);
  assert.equal(contextFrom([{ question: "q".repeat(600), status: "done", answerText: "" }])[0].question.length, 500);
});

test("proposalsTabsModel counts each tab; approved includes edited", () => {
  const list = [
    ...realProposals.proposals,
    { id: 2, status: "approved", patient_name: "A" },
    { id: 3, status: "edited", patient_name: "B" },
    { id: 4, status: "dismissed", patient_name: "C" },
  ];
  const m = proposalsTabsModel(list, "pending");
  assert.deepEqual(m.tabs.map((t) => [t.key, t.label, t.count, t.active]), [
    ["pending", "Waiting", 1, true], ["approved", "Approved", 2, false], ["dismissed", "Dismissed", 1, false]]);
  assert.deepEqual(m.items.map((p) => p.id), [1]);
  assert.deepEqual(proposalsTabsModel(list, "approved").items.map((p) => p.id), [2, 3]);
  assert.deepEqual(proposalsTabsModel(list, "dismissed").items.map((p) => p.id), [4]);
  assert.equal(proposalsTabsModel(list, "bogus").tab, "pending");
  assert.equal(proposalsTabsModel(null, "pending").items.length, 0);
});
