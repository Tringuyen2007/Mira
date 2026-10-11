// Sidebar (Figma components frame 203:109 / caseload frame 1). Pure-ish: renderSidebar(state) -> HTMLElement.
// state = { route, proposalsWaiting, selectedPatientId, therapist, live }.
import { h } from "../core/dom.js";
import { href } from "../core/router.js";
import { icon } from "./icons.js";

const TITLE_RE = /^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i;

function initials(name) {
  const cleaned = String(name || "").split(",")[0].replace(TITLE_RE, "").trim();
  const words = cleaned.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

const ACTIVE_FOR = {
  caseload: "caseload",
  patient: "patient",
  replay: "patient",
  prescribe: "prescribe",
  timelapse: "timelapse",
  live: "live",
  assistant: "assistant",
  copilot: "assistant",
  proposal: "assistant",
};

function navItem({ id, label, iconName, active, href: linkHref, disabled, badge }) {
  return h("a", {
    class: "nav-item",
    href: linkHref,
    "data-nav": id,
    "aria-current": active ? "page" : null,
    "aria-disabled": disabled ? "true" : null,
  },
    icon(iconName),
    h("span", { class: "nav-item__label" }, label),
    badge != null
      ? h("span", { class: "nav-item__badge", "aria-label": `${badge} proposals waiting` }, String(badge))
      : null);
}

export function renderSidebar(state) {
  const { route, proposalsWaiting, selectedPatientId, therapist, live } = state || {};
  const activeKey = ACTIVE_FOR[route && route.name] || null;
  const patientHref = selectedPatientId ? href("patient", { pid: selectedPatientId }) : href("caseload");
  const prescribeHref = selectedPatientId ? href("prescribe", { pid: selectedPatientId }) : href("caseload");
  const timelapseHref = selectedPatientId ? href("timelapse", { pid: selectedPatientId }) : href("caseload");
  const liveHref = live && live.pid ? href("live", { pid: live.pid }) : href("live");
  const hasPatient = Boolean(selectedPatientId);
  const waiting = typeof proposalsWaiting === "number" && proposalsWaiting > 0 ? proposalsWaiting : null;
  const name = (therapist && therapist.name) || "";
  const clinic = (therapist && therapist.clinic) || "";

  return h("div", { class: "sidebar__inner" },
    h("div", { class: "sidebar__header" },
      h("img", { class: "sidebar__mark", src: "assets/mira-mark.png", alt: "" }),
      h("div", {},
        h("p", { class: "sidebar__brand" }, "Mira"),
        h("p", { class: "sidebar__subbrand" }, "Care team"))),
    h("nav", { class: "sidebar__nav", "aria-label": "Main" },
      navItem({ id: "caseload", label: "Caseload", iconName: "caseload",
        active: activeKey === "caseload", href: href("caseload") }),
      navItem({ id: "patient", label: "Patient review", iconName: "patient",
        active: activeKey === "patient", href: patientHref, disabled: !hasPatient }),
      navItem({ id: "prescribe", label: "Prescribe", iconName: "prescribe",
        active: activeKey === "prescribe", href: prescribeHref, disabled: !hasPatient }),
      navItem({ id: "timelapse", label: "Time-lapse", iconName: "timelapse",
        active: activeKey === "timelapse", href: timelapseHref, disabled: !hasPatient }),
      navItem({ id: "live", label: "Live sessions", iconName: "live",
        active: activeKey === "live", href: liveHref }),
      navItem({ id: "assistant", label: "Mira Assistant", iconName: "copilot",
        active: activeKey === "assistant", href: href("assistant"), badge: waiting })),
    h("div", { class: "sidebar__spacer" }),
    h("div", { class: "sidebar__privacy" },
      icon("lock", { label: "" }),
      h("p", {}, "No video. Mira shares skeleton data only.")),
    h("div", { class: "sidebar__footer" },
      h("span", { class: "sidebar__avatar" }, initials(name)),
      h("div", {},
        h("p", { class: "sidebar__name" }, name || "—"),
        clinic ? h("p", { class: "sidebar__clinic" }, clinic) : null)));
}
