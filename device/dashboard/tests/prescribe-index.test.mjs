// Smoke coverage for the Prescribe screen mount: stepper bounds, Save disabled while clean / enabled once
// something changes, and a clean teardown.
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/prescribe/index.js");
const { stepper } = await import("../js/ui/stepper.js");

import { sample } from "./samples.mjs";

const PATIENT = sample("rest_get_patient.json");
const PRESCRIPTIONS = sample("rest_get_prescriptions.json");
const PROGRESS = sample("rest_get_patient_progress.json");

function makeCtx(overrides = {}) {
  return {
    api: {
      patient: async () => PATIENT,
      prescriptions: async () => PRESCRIPTIONS,
      progress: async () => PROGRESS,
      proposals: async () => ({ proposals: [] }),
      putPrescription: async () => ({ prescription: {} }),
      setLimits: async (_pid, { hrLimit }) => ({ patient_id: "maya", hr_limit: hrLimit }),
      approveProposal: async () => ({}),
      editProposal: async () => ({}),
      dismissProposal: async () => ({}),
      exercises: async () => ({ exercises: [] }),
      ...overrides,
    },
    sockets: { subscribe: () => () => {} },
    store: { set() {}, get() { return {}; }, subscribe() { return () => {}; } },
    caps: { has: () => false },
    toast: () => {},
    navigate: () => {},
  };
}

async function flush() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

test("stepper: clamps to [min, max] and never goes out of range", () => {
  let last = null;
  const s = stepper({ label: "sets", min: 1, max: 3, value: 2, onChange: (v) => { last = v; } });
  const [decBtn, , incBtn] = s.el.childNodes;
  incBtn.dispatch("click");
  assert.equal(last, 3);
  incBtn.dispatch("click"); // already at max
  assert.equal(last, 3);
  decBtn.dispatch("click");
  decBtn.dispatch("click");
  decBtn.dispatch("click"); // already at min
  assert.equal(last, 1);
});

test("stepper: rounds to the given step", () => {
  let last = null;
  const s = stepper({ label: "rest", min: 30, max: 120, step: 15, value: 60, onChange: (v) => { last = v; } });
  const [, , incBtn] = s.el.childNodes;
  incBtn.dispatch("click");
  assert.equal(last, 75);
});

test("mounts the Prescribe screen, Save is disabled until something changes, then enabled", async () => {
  const root = document.createElement("div");
  const route = { params: { pid: "maya" }, query: {} };
  const ctx = makeCtx();
  const unmount = screen.mount(root, ctx, route);
  await flush();

  const saveBtn = root.querySelector(".prescribe__save-bar").children.find((c) => c.textContent === "Save plan");
  // Save / Discard sit in the page head (Figma: top right), not at the bottom of the editor.
  assert.ok(root.querySelector(".prescribe__head").contains(saveBtn));
  assert.ok(saveBtn, "Save plan button should be rendered");
  assert.equal(saveBtn.hasAttribute("disabled"), true);

  // Simulate editing the heart-rate limit slider directly through the input's listener.
  const hrInput = [...root.querySelectorAll("input")].find((i) => i.getAttribute("aria-label") === "Heart rate limit: pause and check in above");
  assert.ok(hrInput, "heart-rate slider should be rendered");
  hrInput.dispatch("input", { target: { value: "150" } });

  const saveBtnAfter = [...root.querySelectorAll("button")].find((b) => b.textContent === "Save plan");
  assert.equal(saveBtnAfter.hasAttribute("disabled"), false);

  unmount();
});

test("mounts cleanly with no patient id", async () => {
  const root = document.createElement("div");
  const route = { params: {}, query: {} };
  const ctx = makeCtx();
  const unmount = screen.mount(root, ctx, route);
  await flush();
  assert.equal(root.textContent, "No patient selected.");
  unmount();
});

test("a library search result only stages the exercise; nothing is written until Save plan", async () => {
  const root = document.createElement("div");
  const calls = [];
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn) => { fn(); return 0; };
  try {
    const ctx = makeCtx({
      exercises: async () => ({ exercises: [{ id: "wall_slide", name: "Wall slides" }] }),
      putPrescription: async (pid, body) => { calls.push([pid, body]); return { prescription: {} }; },
    });
    ctx.caps = { has: (p) => p === "/exercises" };
    const unmount = screen.mount(root, ctx, { params: { pid: "maya" }, query: {} });
    await flush();
    const search = root.querySelector(".prescribe__search");
    search.dispatch("input", { target: { value: "wall" } });
    await flush();
    const add = root.querySelectorAll("button").find((b) => b.textContent === "Add Wall slides to the plan");
    assert.ok(add, "search result rendered");
    add.dispatch("click");
    await flush();
    assert.equal(calls.length, 0, "no PUT on a single click");
    assert.match(root.textContent, /Wall slides/);
    assert.match(root.textContent, /new, not saved yet/);
    const saveBtn = root.querySelectorAll("button").find((b) => b.textContent === "Save plan");
    assert.equal(saveBtn.hasAttribute("disabled"), false);
    saveBtn.dispatch("click");
    await flush();
    assert.deepEqual(calls.map(([pid, b]) => [pid, b.exercise_id, b.sets, b.reps]), [["maya", "wall_slide", 3, 10]]);
    unmount();
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test("Discard changes drops a staged library pick", async () => {
  const root = document.createElement("div");
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn) => { fn(); return 0; };
  try {
    const ctx = makeCtx({ exercises: async () => ({ exercises: [{ id: "wall_slide", name: "Wall slides" }] }) });
    ctx.caps = { has: (p) => p === "/exercises" };
    const unmount = screen.mount(root, ctx, { params: { pid: "maya" }, query: {} });
    await flush();
    root.querySelector(".prescribe__search").dispatch("input", { target: { value: "wall" } });
    await flush();
    root.querySelectorAll("button").find((b) => b.textContent === "Add Wall slides to the plan").dispatch("click");
    root.querySelectorAll("button").find((b) => b.textContent === "Discard changes").dispatch("click");
    assert.doesNotMatch(root.textContent, /new, not saved yet/);
    assert.equal(root.querySelectorAll("button").find((b) => b.textContent === "Save plan").hasAttribute("disabled"), true);
    unmount();
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test("a failed or forbidden load shows an error with Retry and keeps Save disabled (no editable defaults)", async () => {
  const root = document.createElement("div");
  const err = Object.assign(new Error("403"), { status: 403, detail: "this patient belongs to another therapist" });
  const ctx = makeCtx({ prescriptions: async () => { throw err; } });
  const unmount = screen.mount(root, ctx, { params: { pid: "maya" }, query: {} });
  await flush();
  assert.equal([...root.querySelectorAll("input")].find((i) => i.getAttribute("type") === "range"), undefined);
  const saveBtn = root.querySelectorAll("button").find((b) => b.textContent === "Save plan");
  assert.equal(saveBtn.hasAttribute("disabled"), true);
  assert.ok(root.querySelectorAll("button").find((b) => /Try again|Retry/.test(b.textContent)));
  unmount();
});

test("emergency relationship row is hidden when there is no data", async () => {
  const root = document.createElement("div");
  const unmount = screen.mount(root, makeCtx(), { params: { pid: "maya" }, query: {} });
  await flush();
  const card = root.querySelector('[aria-label="Emergency contact"]');
  assert.doesNotMatch(card.textContent, /Relationship|—/);
  unmount();
});

test("a 409 on a proposal decision says 'Already decided' and reloads instead of re-enabling a dead retry", async () => {
  const { decideProposal } = await import("../js/ui/proposalDecision.js");
  const toasts = [];
  let reloaded = 0;
  const ok = await decideProposal({ toast: (t) => toasts.push(t) },
    async () => { throw Object.assign(new Error("409"), { status: 409 }); },
    { successText: "Approved.", onConflict: () => { reloaded += 1; } });
  assert.equal(ok, false);
  assert.equal(reloaded, 1);
  assert.deepEqual(toasts, [{ kind: "info", text: "Already decided" }]);
});
