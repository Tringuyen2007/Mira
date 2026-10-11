import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { createAlertBanner } = await import("../js/ui/alertBanner.js");

function makeAlert(id, overrides = {}) {
  return {
    alertId: id, patientId: "maya", name: "Maya Ruiz", kind: "fall", title: `Fall — Maya Ruiz (${id})`,
    steps: [
      { step: "detected", label: "Detected", time: "t1", done: true },
      { step: "checked_in", label: "Checked in", time: "t2", done: true },
      { step: "escalated", label: "Escalated", time: "t3", done: true },
      { step: "resolved", label: "Resolved", time: null, done: false },
    ],
    replayHref: "#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6",
    canResolve: true,
    ...overrides,
  };
}

test("renders nothing and stays hidden with no open alerts", () => {
  const root = document.createElement("div");
  const banner = createAlertBanner(root, { resolveAlert: async () => {} });
  banner.render([]);
  assert.equal(root.hidden, true);
  assert.equal(root.querySelectorAll(".alert-banner__item").length, 0);
});

test("shows the escalation timeline, replay link and resolve control for each open alert", () => {
  const root = document.createElement("div");
  const banner = createAlertBanner(root, { resolveAlert: async () => {} });
  banner.render([makeAlert("a1")]);
  assert.equal(root.hidden, false);
  const items = root.querySelectorAll(".alert-banner__item");
  assert.equal(items.length, 1);
  const labels = items[0].querySelectorAll(".alert-banner__step").map((el) => el.textContent);
  assert.deepEqual(labels, ["Detected", "Checked in", "Escalated", "Resolved"]);
  const replay = items[0].querySelector(".alert-banner__replay");
  assert.equal(replay.getAttribute("href"), "#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6");
  assert.ok(items[0].querySelector(".alert-banner__resolve-btn"));
});

test("stacks at most 3 alerts and shows an 'N more' line", () => {
  const root = document.createElement("div");
  const banner = createAlertBanner(root, { resolveAlert: async () => {} });
  banner.render([makeAlert("a1"), makeAlert("a2"), makeAlert("a3"), makeAlert("a4"), makeAlert("a5")]);
  assert.equal(root.querySelectorAll(".alert-banner__item").length, 3);
  assert.match(root.querySelector(".alert-banner__more").textContent, /2 more/);
});

test("announces a brand-new alert via the role=alert live region, without re-announcing on a step update", () => {
  const root = document.createElement("div");
  const banner = createAlertBanner(root, { resolveAlert: async () => {} });
  banner.render([makeAlert("a1")]);
  const announce = root.querySelector('[role="alert"]');
  assert.ok(announce);
  assert.match(announce.textContent, /Fall — Maya Ruiz/);
  announce.textContent = "";
  // Same alert, a step ticked off: not a new arrival, so the live region should not be re-populated.
  const ticked = makeAlert("a1");
  ticked.steps[3] = { step: "resolved", label: "Resolved", time: "t4", done: true };
  banner.render([ticked]);
  assert.equal(announce.textContent, "");
});

test("Mark resolved calls resolveAlert(alertId, note) only on an explicit click", async () => {
  const root = document.createElement("div");
  const calls = [];
  const banner = createAlertBanner(root, { resolveAlert: async (id, note) => { calls.push([id, note]); } });
  banner.render([makeAlert("a1")]);
  const resolveBox = root.querySelector(".alert-banner__resolve");
  const textarea = resolveBox.querySelector("textarea");
  textarea.value = "False alarm, she was gardening.";
  const button = resolveBox.querySelector("button");
  assert.equal(calls.length, 0);
  button.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(calls, [["a1", "False alarm, she was gardening."]]);
});

test("a failed resolve re-enables the button and shows an inline error instead of throwing", async () => {
  const root = document.createElement("div");
  const banner = createAlertBanner(root, { resolveAlert: async () => { throw new Error("offline"); } });
  banner.render([makeAlert("a1")]);
  const resolveBox = root.querySelector(".alert-banner__resolve");
  const button = resolveBox.querySelector("button");
  button.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(button.hasAttribute("disabled"), false);
  assert.match(resolveBox.querySelector(".alert-banner__error").textContent, /Couldn't/);
});
