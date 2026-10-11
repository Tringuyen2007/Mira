// Sidebar (Figma components frame 203:109 / caseload frame 1). Pure-ish: renderSidebar(state) -> HTMLElement.
// state = { route, proposalsWaiting, selectedPatientId, therapist, live, unreadCount, bellOpen, onBellToggle,
// theme, onThemeToggle }. createSidebarView(state) -> {el, update(state), bellEl} for in-place updates.
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

const NAV = [
  { id: "caseload", label: "Caseload", iconName: "caseload" },
  { id: "patient", label: "Patient review", iconName: "patient" },
  { id: "prescribe", label: "Prescribe", iconName: "prescribe" },
  { id: "timelapse", label: "Time-lapse", iconName: "timelapse" },
  { id: "live", label: "Live sessions", iconName: "live" },
  { id: "assistant", label: "Mira Assistant", iconName: "copilot" },
];

function setAttr(el, name, value) {
  if (value === null || value === undefined) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

// Show/hide an optional child (a badge) without rebuilding its parent.
function toggleChild(parent, child, show) {
  if (show && child.parentNode !== parent) parent.append(child);
  else if (!show && child.parentNode === parent) child.remove();
}

// Built once, then updated in place: route changes, store updates, the bell's open/unread state and the theme
// toggle only change text, attributes and optional badges, so the focused control (the bell, a nav link, the
// projector switch) is never detached (final review B, I4: closing the bell used to focus a detached node).
// Handlers are read at click time, so update() can swap them too.
export function createSidebarView(initial) {
  let current = initial || {};

  const navRefs = NAV.map((def) => {
    const badgeEl = h("span", { class: "nav-item__badge" });
    const a = h("a", { class: "nav-item", href: href("caseload"), "data-nav": def.id },
      icon(def.iconName), h("span", { class: "nav-item__label" }, def.label));
    return { def, a, badgeEl };
  });

  const bellBadgeEl = h("span", { class: "sidebar__bell-badge" });
  const bellEl = h("button", {
    class: "sidebar__bell", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
    onclick: () => current.onBellToggle?.(bellEl),
  }, icon("bell"));

  const themeEl = h("button", {
    class: "sidebar__theme-toggle", type: "button", role: "switch", "aria-checked": "false",
    onclick: () => current.onThemeToggle?.(),
  },
    h("span", { class: "sidebar__theme-toggle-label" }, "Projector mode"),
    h("span", { class: "sidebar__theme-toggle-track" }, h("span", { class: "sidebar__theme-toggle-thumb" })));

  const avatarEl = h("span", { class: "sidebar__avatar" });
  const nameEl = h("p", { class: "sidebar__name" });
  const clinicEl = h("p", { class: "sidebar__clinic" });
  const identityTextEl = h("div", {}, nameEl);

  const el = h("div", { class: "sidebar__inner" },
    h("div", { class: "sidebar__header" },
      h("img", { class: "sidebar__mark", src: "assets/mira-mark.png", alt: "" }),
      h("div", { class: "sidebar__brand-block" },
        h("p", { class: "sidebar__brand" }, "Mira"),
        h("p", { class: "sidebar__subbrand" }, "Care team")),
      bellEl),
    h("nav", { class: "sidebar__nav", "aria-label": "Main" }, navRefs.map((r) => r.a)),
    h("div", { class: "sidebar__spacer" }),
    h("div", { class: "sidebar__privacy" },
      icon("lock"),
      h("p", {}, "No video. Mira shares skeleton data only.")),
    h("div", { class: "sidebar__footer" },
      themeEl,
      h("div", { class: "sidebar__identity" }, avatarEl, identityTextEl)));

  function update(state) {
    current = state || {};
    const { route, proposalsWaiting, selectedPatientId, therapist, live, unreadCount, bellOpen, theme } = current;
    const activeKey = ACTIVE_FOR[route && route.name] || null;
    const hasPatient = Boolean(selectedPatientId);
    const scoped = (name) => (hasPatient ? href(name, { pid: selectedPatientId }) : href("caseload"));
    const hrefs = {
      caseload: href("caseload"), patient: scoped("patient"), prescribe: scoped("prescribe"),
      timelapse: scoped("timelapse"), live: live && live.pid ? href("live", { pid: live.pid }) : href("live"),
      assistant: href("assistant"),
    };
    const waiting = typeof proposalsWaiting === "number" && proposalsWaiting > 0 ? proposalsWaiting : null;
    for (const { def, a, badgeEl } of navRefs) {
      setAttr(a, "href", hrefs[def.id]);
      setAttr(a, "aria-current", activeKey === def.id ? "page" : null);
      const patientScoped = def.id === "patient" || def.id === "prescribe" || def.id === "timelapse";
      setAttr(a, "aria-disabled", patientScoped && !hasPatient ? "true" : null);
      if (def.id === "assistant") {
        if (waiting) {
          badgeEl.textContent = String(waiting);
          badgeEl.setAttribute("aria-label", `${waiting} proposals waiting`);
        }
        toggleChild(a, badgeEl, Boolean(waiting));
      }
    }

    const count = typeof unreadCount === "number" && unreadCount > 0
      ? (unreadCount > 99 ? "99+" : String(unreadCount)) : null;
    bellEl.setAttribute("aria-expanded", bellOpen ? "true" : "false");
    bellEl.setAttribute("aria-label", `${unreadCount || 0} unread notifications`);
    if (count) bellBadgeEl.textContent = count;
    toggleChild(bellEl, bellBadgeEl, Boolean(count));

    themeEl.setAttribute("aria-checked", theme === "projector" ? "true" : "false");

    const name = (therapist && therapist.name) || "";
    const clinic = (therapist && therapist.clinic) || "";
    avatarEl.textContent = initials(name);
    nameEl.textContent = name || "—";
    clinicEl.textContent = clinic;
    toggleChild(identityTextEl, clinicEl, Boolean(clinic));
  }

  update(current);
  return { el, update, bellEl };
}

export function renderSidebar(state) {
  return createSidebarView(state).el;
}
