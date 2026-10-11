import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { proposalCard, changeRows, editChangeFrom } = await import("../js/ui/proposalCard.js");

const real = JSON.parse(readFileSync(new URL("../../../server/samples/rest_get_proposals.json", import.meta.url), "utf8"))
  .proposals[0];
const robert = {
  id: 2, patient_id: "robert", patient_name: "Robert Okafor", status: "pending",
  rationale: "Pain 7 out of 10 after yesterday, up from 3. Lower the load for a week.",
  evidence: { items: ["sessions.pain_post: 7 vs 3"], sql: [] },
  change: { exercise_id: "squat", sets: 3, reps: 8 },
};
const squatRx = { exercise_id: "squat", sets: 3, reps: 12, targets: { knee_deg: 90 } };

const button = (el, label) => el.querySelectorAll("button").find((b) => b.textContent === label);
const flush = () => new Promise((r) => setTimeout(r, 0));

test("change rows show current → new when the current plan is known", () => {
  assert.deepEqual(changeRows(robert.change, squatRx), ["Squats: 3 × 12 → 3 × 8"]);
  assert.deepEqual(changeRows(robert.change, null), ["Squats: 3 × 8"]);
  assert.deepEqual(changeRows(real.change, { exercise_id: "squat", sets: 3, reps: 10, targets: {} }),
    ["Rest days between squat days: 0 → 1", "Add: weight-shift drill"]);
  assert.deepEqual(changeRows({ exercise_id: "squat", targets: { knee_deg: 95 } }, squatRx),
    ["Knee bend target: 90° → 95°"]);
  assert.deepEqual(changeRows({ exercise_id: "squat", targets: { knee_deg: 95 } }, null), ["Knee bend target: 95°"]);
});

test("card renders name, status, AI draft chip, title, change, why and actions", () => {
  const el = proposalCard(robert, { current: squatRx });
  assert.match(el.querySelector(".proposal-card__name").textContent, /Robert Okafor/);
  assert.ok(el.querySelector(".status-icon"));
  assert.ok(el.querySelector(".chip--ai"));
  assert.equal(el.querySelector(".proposal-card__title").textContent, "Change squats to 3 × 8");
  assert.equal(el.querySelector(".proposal-card__change").textContent, "Squats: 3 × 12 → 3 × 8");
  assert.equal(el.querySelector(".proposal-card__why").textContent, "Why: Pain 7 out of 10 after yesterday, up from 3.");
  assert.ok(el.querySelector("details"));
  assert.match(el.querySelector("details").textContent, /sessions\.pain_post: 7 vs 3/);
  for (const l of ["Dismiss", "Edit", "Approve"]) assert.ok(button(el, l), l);
  assert.equal(el.getAttribute("data-proposal-id"), "2");
});

test("decided cards show the decision, no actions and no AI draft chip", () => {
  const el = proposalCard({ ...robert, status: "approved", decided_by: "okafor", decided_at: "2026-10-11T10:00:00Z" });
  assert.equal(button(el, "Approve"), undefined);
  assert.equal(el.querySelector(".chip--ai"), null);
  assert.match(el.textContent, /Approved by okafor/);
});

test("approve disables every action while pending and re-enables when it fails", async () => {
  let resolve;
  const el = proposalCard(robert, { onApprove: () => new Promise((r) => { resolve = r; }) });
  button(el, "Approve").dispatch("click");
  for (const l of ["Dismiss", "Edit", "Approve"]) assert.ok(button(el, l).hasAttribute("disabled"), l);
  resolve(false);
  await flush();
  for (const l of ["Dismiss", "Edit", "Approve"]) assert.ok(!button(el, l).hasAttribute("disabled"), l);
});

test("dismiss calls onDismiss with the proposal", async () => {
  const seen = [];
  const el = proposalCard(robert, { onDismiss: async (p) => { seen.push(p.id); return true; } });
  button(el, "Dismiss").dispatch("click");
  await flush();
  assert.deepEqual(seen, [2]);
  assert.ok(button(el, "Dismiss").hasAttribute("disabled"), "stays disabled after success (card is leaving)");
});

test("edit opens an inline form prefilled from the proposal and current plan, and posts a PlanChange", async () => {
  const sent = [];
  const el = proposalCard(robert, { current: squatRx, onEdit: async (p, change) => { sent.push(change); return true; } });
  button(el, "Edit").dispatch("click");
  const form = el.querySelector("form");
  assert.ok(form);
  const field = (name) => form.querySelector(`[name="${name}"]`);
  assert.equal(field("sets").value, "3");
  assert.equal(field("reps").value, "8");
  assert.equal(field("rest_days_between").value, "");
  assert.equal(field("knee_deg").value, "90");
  field("reps").value = "10";
  field("rest_days_between").value = "1";
  form.dispatch("submit");
  await flush();
  assert.deepEqual(sent, [{ exercise_id: "squat", sets: 3, reps: 10, rest_days_between: 1, targets: { knee_deg: 90 } }]);
});

test("edit keeps the proposal's add/remove and rejects out-of-range values", async () => {
  assert.deepEqual(editChangeFrom(real.change, { sets: "", reps: "", rest_days_between: "2", knee_deg: "" }),
    { ok: true, change: { exercise_id: "squat", rest_days_between: 2, add: ["weight_shift_drill"] } });
  const bad = editChangeFrom(robert.change, { sets: "11", reps: "8", rest_days_between: "", knee_deg: "" });
  assert.equal(bad.ok, false);
  assert.match(bad.error, /Sets/);

  let called = false;
  const el = proposalCard(robert, { onEdit: async () => { called = true; return true; } });
  button(el, "Edit").dispatch("click");
  const form = el.querySelector("form");
  form.querySelector('[name="sets"]').value = "0";
  form.dispatch("submit");
  await flush();
  assert.equal(called, false);
  assert.match(form.querySelector(".proposal-card__error").textContent, /Sets/);
});

test("rationale and names stay text, never markup", () => {
  const el = proposalCard({ ...robert, patient_name: "<img src=x>", rationale: "<script>x</script>." });
  assert.equal(el.querySelector(".proposal-card__name").textContent.includes("<img src=x>"), true);
  assert.equal(el.querySelector("script"), null);
  for (const img of el.querySelectorAll("img")) assert.match(img.getAttribute("src"), /^assets\//);
});
