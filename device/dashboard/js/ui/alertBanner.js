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

// One alert's element, built once per alertId and then updated in place: a `checked_in`/`escalated` step that
// arrives while the therapist is typing a resolution note must not wipe the note, its focus, or a pending
// resolve's disabled button (final review B, I3).
function createAlertItem(alert, resolveAlert) {
  const titleEl = h("p", { class: "alert-banner__title" });
  const stepsEl = h("ol", { class: "alert-banner__steps" });
  const replayEl = h("a", { class: "alert-banner__replay", href: "#/caseload" }, "View replay");
  const resolveEl = resolveControl(alert, resolveAlert);
  const el = h("div", { class: "alert-banner__item", "data-alert-id": alert.alertId }, titleEl, stepsEl);
  let stepsKey = null;

  function update(a) {
    titleEl.textContent = a.title;
    const key = JSON.stringify(a.steps.map((st) => [st.label, Boolean(st.done)]));
    if (key !== stepsKey) {
      stepsKey = key;
      stepsEl.replaceChildren(...a.steps.map(stepItem));
    }
    if (a.replayHref) {
      replayEl.setAttribute("href", a.replayHref);
      if (replayEl.parentNode !== el) el.insertBefore(replayEl, resolveEl.parentNode === el ? resolveEl : null);
    } else {
      replayEl.remove();
    }
    if (a.canResolve) { if (resolveEl.parentNode !== el) el.append(resolveEl); } else resolveEl.remove();
  }

  update(alert);
  return { el, update };
}

export function createAlertBanner(container, { resolveAlert } = {}) {
  let seenIds = new Set();
  const items = new Map(); // alertId -> {el, update}
  const announceEl = h("p", { class: "sr-only", role: "alert" });
  const bannerEl = h("div", { class: "alert-banner" });
  const moreEl = h("p", { class: "alert-banner__more" });

  function render(alerts) {
    const open = alerts || [];
    const newAlert = open.find((a) => !seenIds.has(a.alertId));
    seenIds = new Set(open.map((a) => a.alertId));
    if (open.length === 0) {
      items.clear();
      clear(bannerEl);
      clear(container);
      container.hidden = true;
      return;
    }
    if (announceEl.parentNode !== container || bannerEl.parentNode !== container) {
      container.replaceChildren(announceEl, bannerEl);
    }
    container.hidden = false;
    // Only a brand-new alert changes the live region's text, so a step ticking off doesn't re-announce.
    if (newAlert) announceEl.textContent = `New alert: ${newAlert.title}`;

    const shown = open.slice(0, MAX_SHOWN);
    const shownIds = new Set(shown.map((a) => a.alertId));
    for (const [id, item] of items) {
      if (!shownIds.has(id)) { item.el.remove(); items.delete(id); }
    }
    // Insert new items at their position without moving existing ones (moving a node drops its focus).
    let ref = moreEl.parentNode === bannerEl ? moreEl : null;
    for (let i = shown.length - 1; i >= 0; i--) {
      const a = shown[i];
      let item = items.get(a.alertId);
      if (item) item.update(a);
      else { item = createAlertItem(a, resolveAlert); items.set(a.alertId, item); }
      if (item.el.parentNode !== bannerEl || item.el.nextSibling !== ref) bannerEl.insertBefore(item.el, ref);
      ref = item.el;
    }
    const moreCount = open.length - shown.length;
    moreEl.textContent = moreCount > 0 ? `${moreCount} more` : "";
    if (moreCount > 0) { if (moreEl.parentNode !== bannerEl) bannerEl.append(moreEl); } else moreEl.remove();
  }

  return { render };
}
