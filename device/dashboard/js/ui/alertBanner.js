// Persistent safety-alerts banner (decisions: lives above #main's screen content, never re-created on route
// change, so no screen mount/unmount can wipe it). Multiple open alerts stack, max 3 shown + "N more". A brand
// new alert is announced through a visually-hidden role="alert" live region (never by stealing focus); a step
// ticking off on an already-visible alert does not re-announce.
import { h, clear } from "../core/dom.js";

const MAX_SHOWN = 3;

function stepItem(step) {
  return h("li", { class: `alert-banner__step${step.done ? " alert-banner__step--done" : ""}` }, step.label);
}

function resolveControl(alert, resolveAlert) {
  const textarea = h("textarea", { class: "alert-banner__note", rows: "2",
    "aria-label": `Resolution note for ${alert.title}` });
  const errorEl = h("p", { class: "alert-banner__error", role: "alert" });
  const button = h("button", { class: "btn btn--secondary alert-banner__resolve-btn", type: "button",
    onclick: () => doResolve() }, "Mark resolved");

  async function doResolve() {
    errorEl.textContent = "";
    button.setAttribute("disabled", "");
    try {
      await resolveAlert(alert.alertId, textarea.value.trim() || null);
      // Optimistic UI only after success: the caller re-renders the banner once the server's `alert` event
      // (resolved_by_therapist) comes back over /ws/events; nothing else to do here on success.
    } catch {
      button.removeAttribute("disabled");
      errorEl.textContent = "Couldn't resolve. Try again.";
    }
  }

  return h("div", { class: "alert-banner__resolve" }, textarea, button, errorEl);
}

function alertItem(alert, resolveAlert) {
  return h("div", { class: "alert-banner__item", "data-alert-id": alert.alertId },
    h("p", { class: "alert-banner__title" }, alert.title),
    h("ol", { class: "alert-banner__steps" }, ...alert.steps.map(stepItem)),
    alert.replayHref ? h("a", { class: "alert-banner__replay", href: alert.replayHref }, "View replay") : null,
    alert.canResolve ? resolveControl(alert, resolveAlert) : null);
}

export function createAlertBanner(container, { resolveAlert } = {}) {
  let seenIds = new Set();

  function render(alerts) {
    const open = alerts || [];
    const newAlert = open.find((a) => !seenIds.has(a.alertId));
    seenIds = new Set(open.map((a) => a.alertId));
    clear(container);
    if (open.length === 0) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const shown = open.slice(0, MAX_SHOWN);
    const moreCount = open.length - shown.length;
    const announce = h("p", { class: "sr-only", role: "alert" }, newAlert ? `New alert: ${newAlert.title}` : "");
    container.append(
      announce,
      h("div", { class: "alert-banner" },
        ...shown.map((a) => alertItem(a, resolveAlert)),
        moreCount > 0 ? h("p", { class: "alert-banner__more" }, `${moreCount} more`) : null));
  }

  return { render };
}
