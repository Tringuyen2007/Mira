import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { renderSidebar, createSidebarView } = await import("../js/ui/sidebar.js");

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

test("the bell shows an unread badge and aria-label, and toggling it calls onBellToggle with the button", () => {
  let clicked = null;
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
    unreadCount: 3, bellOpen: false, onBellToggle: (el) => { clicked = el; },
  });
  const bell = root.querySelector(".sidebar__bell");
  assert.equal(bell.getAttribute("aria-label"), "3 unread notifications");
  assert.equal(bell.getAttribute("aria-expanded"), "false");
  assert.equal(bell.querySelector(".sidebar__bell-badge").textContent, "3");
  bell.dispatch("click");
  assert.equal(clicked, bell);
});

test("the bell has no badge and aria-expanded=true when open with zero unread", () => {
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
    unreadCount: 0, bellOpen: true, onBellToggle: () => {},
  });
  const bell = root.querySelector(".sidebar__bell");
  assert.equal(bell.getAttribute("aria-label"), "0 unread notifications");
  assert.equal(bell.getAttribute("aria-expanded"), "true");
  assert.equal(bell.querySelector(".sidebar__bell-badge"), null);
});

test("the projector-mode switch reflects the current theme and calls onThemeToggle when clicked", () => {
  let toggled = 0;
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
    theme: "light", onThemeToggle: () => { toggled += 1; },
  });
  const toggle = root.querySelector(".sidebar__theme-toggle");
  assert.equal(toggle.getAttribute("role"), "switch");
  assert.equal(toggle.getAttribute("aria-checked"), "false");
  assert.match(toggle.textContent, /Projector mode/);
  toggle.dispatch("click");
  assert.equal(toggled, 1);
});

test("the projector-mode switch is aria-checked=true when the projector theme is active", () => {
  const root = renderSidebar({
    route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null,
    theme: "projector", onThemeToggle: () => {},
  });
  const toggle = root.querySelector(".sidebar__theme-toggle");
  assert.equal(toggle.getAttribute("aria-checked"), "true");
});

test("Mira Assistant links to #/assistant and is active on assistant, copilot and proposal routes", () => {
  for (const name of ["assistant", "copilot", "proposal"]) {
    const root = renderSidebar({ route: { name }, proposalsWaiting: 0, selectedPatientId: null, therapist, live: null });
    const a = navLinks(root).find((l) => l.querySelector(".nav-item__label").textContent === "Mira Assistant");
    assert.equal(a.getAttribute("href"), "#/assistant");
    assert.equal(a.getAttribute("aria-current"), "page", name);
  }
});

test("updates happen in place: the bell, nav links and badges keep their identity (focus is never lost)", () => {
  const view = createSidebarView({ route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null,
    therapist, live: null, unreadCount: 0, bellOpen: false });
  const bell = view.el.querySelector(".sidebar__bell");
  const caseloadLink = navLinks(view.el)[0];
  view.update({ route: { name: "patient" }, proposalsWaiting: 2, selectedPatientId: "maya", therapist,
    live: null, unreadCount: 5, bellOpen: true, theme: "projector" });
  assert.equal(view.el.querySelector(".sidebar__bell"), bell);
  assert.equal(view.bellEl, bell);
  assert.equal(navLinks(view.el)[0], caseloadLink);
  assert.equal(bell.getAttribute("aria-expanded"), "true");
  assert.equal(bell.querySelector(".sidebar__bell-badge").textContent, "5");
  const links = Object.fromEntries(navLinks(view.el).map((a) => [a.querySelector(".nav-item__label").textContent, a]));
  assert.equal(links["Patient review"].getAttribute("aria-current"), "page");
  assert.equal(links["Patient review"].getAttribute("href"), "#/patients/maya");
  assert.equal(links["Patient review"].getAttribute("aria-disabled"), null);
  assert.equal(links["Mira Assistant"].querySelector(".nav-item__badge").textContent, "2");
  assert.equal(view.el.querySelector(".sidebar__theme-toggle").getAttribute("aria-checked"), "true");
  view.update({ route: { name: "patient" }, proposalsWaiting: 0, selectedPatientId: "maya", therapist,
    live: null, unreadCount: 0, bellOpen: false });
  assert.equal(bell.querySelector(".sidebar__bell-badge"), null);
  assert.equal(links["Mira Assistant"].querySelector(".nav-item__badge"), null);
});

test("icons are inline SVG drawn in currentColor (readable in both themes)", () => {
  const root = renderSidebar({ route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null,
    therapist, live: null });
  const svgIcon = root.querySelector(".sidebar__bell").querySelector(".icon");
  assert.equal(svgIcon.tagName.toLowerCase(), "svg");
  assert.equal(svgIcon.getAttribute("aria-hidden"), "true");
  assert.ok(svgIcon.querySelector("path").getAttribute("stroke") === "currentColor");
  assert.equal(root.querySelectorAll("img").filter((i) => /assets\/icons\//.test(i.getAttribute("src") || "")).length, 0);
});
