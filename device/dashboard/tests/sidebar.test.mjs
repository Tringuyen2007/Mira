import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { renderSidebar } = await import("../js/ui/sidebar.js");

const therapist = { name: "Dr. Sam Lee, PT", clinic: "Lakeside Rehab" };

function navLinks(root) {
  return root.querySelectorAll(".nav-item");
}

test("renders six nav links in order", () => {
  const root = renderSidebar({
    route: { name: "prescribe" },
    proposalsWaiting: 3,
    selectedPatientId: "maya",
    therapist,
    live: null,
  });
  const links = navLinks(root);
  const labels = links.map((a) => a.querySelector(".nav-item__label").textContent);
  assert.deepEqual(labels,
    ["Caseload", "Patient review", "Prescribe", "Time-lapse", "Live sessions", "Mira Assistant"]);
});

test("the active route carries aria-current=page", () => {
  const root = renderSidebar({
    route: { name: "prescribe" },
    proposalsWaiting: 3,
    selectedPatientId: "maya",
    therapist,
    live: null,
  });
  const links = navLinks(root);
  const byLabel = Object.fromEntries(links.map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  assert.equal(byLabel["Prescribe"].getAttribute("aria-current"), "page");
  assert.equal(byLabel["Caseload"].getAttribute("aria-current"), null);
});

test("Patient review links to the selected patient", () => {
  const root = renderSidebar({
    route: { name: "prescribe" },
    proposalsWaiting: 3,
    selectedPatientId: "maya",
    therapist,
    live: null,
  });
  const links = navLinks(root);
  const byLabel = Object.fromEntries(links.map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  assert.equal(byLabel["Patient review"].getAttribute("href"), "#/patients/maya");
});

test("Mira Assistant shows a proposals-waiting badge", () => {
  const root = renderSidebar({
    route: { name: "prescribe" },
    proposalsWaiting: 3,
    selectedPatientId: "maya",
    therapist,
    live: null,
  });
  const links = navLinks(root);
  const byLabel = Object.fromEntries(links.map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  const badge = byLabel["Mira Assistant"].querySelector(".nav-item__badge");
  assert.equal(badge.textContent, "3");
  assert.equal(badge.getAttribute("aria-label"), "3 proposals waiting");
});

test("with no selected patient, patient-scoped items fall back to caseload and are disabled", () => {
  const root = renderSidebar({
    route: { name: "caseload" },
    proposalsWaiting: 0,
    selectedPatientId: null,
    therapist,
    live: null,
  });
  const links = navLinks(root);
  const byLabel = Object.fromEntries(links.map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  for (const label of ["Patient review", "Prescribe", "Time-lapse"]) {
    assert.equal(byLabel[label].getAttribute("href"), "#/caseload");
    assert.equal(byLabel[label].getAttribute("aria-disabled"), "true");
  }
});

test("privacy note and footer initials", () => {
  const root = renderSidebar({
    route: { name: "caseload" },
    proposalsWaiting: 0,
    selectedPatientId: null,
    therapist,
    live: null,
  });
  assert.match(root.textContent, /No video\. Mira shares skeleton data only\./);
  const avatar = root.querySelector(".sidebar__avatar");
  assert.equal(avatar.textContent, "SL");
});

test("Live sessions links to the pid-less route when nothing is live, and to the live patient otherwise", () => {
  const root1 = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
  });
  const byLabel1 = Object.fromEntries(
    navLinks(root1).map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  assert.equal(byLabel1["Live sessions"].getAttribute("href"), "#/live");

  const root2 = renderSidebar({
    route: { name: "live" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: { pid: "maya" },
  });
  const byLabel2 = Object.fromEntries(
    navLinks(root2).map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  assert.equal(byLabel2["Live sessions"].getAttribute("href"), "#/live/maya");
});

test("the clinic line is omitted when the therapist has no clinic", () => {
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null,
    therapist: { name: "Dr. Sam Lee, PT", clinic: "" }, live: null,
  });
  assert.equal(root.querySelector(".sidebar__clinic"), null);
});

test("the clinic line is rendered when the therapist has a clinic", () => {
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
  });
  assert.equal(root.querySelector(".sidebar__clinic").textContent, "Lakeside Rehab");
});

test("Mira Assistant links to #/assistant and is active on assistant, copilot and proposal routes", () => {
  for (const name of ["assistant", "copilot", "proposal"]) {
    const root = renderSidebar({ route: { name }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null });
    const a = navLinks(root).find((l) => l.querySelector(".nav-item__label").textContent === "Mira Assistant");
    assert.equal(a.getAttribute("href"), "#/assistant");
    assert.equal(a.getAttribute("aria-current"), "page", name);
  }
});
