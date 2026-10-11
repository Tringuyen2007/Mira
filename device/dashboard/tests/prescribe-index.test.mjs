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

  const saveBtn = root.querySelector(".prescribe__save-bar")?.children?.find?.((c) => c.textContent === "Save plan")
    || [...root.querySelectorAll("button")].find((b) => b.textContent === "Save plan");
  assert.ok(saveBtn, "Save plan button should be rendered");
  assert.equal(saveBtn.hasAttribute("disabled"), true);

  // Simulate editing the heart-rate limit slider directly through the input's listener.
  const hrInput = [...root.querySelectorAll("input")].find((i) => i.getAttribute("aria-label") === "Heart rate limit");
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
