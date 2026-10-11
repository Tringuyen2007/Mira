// Caseload screen mount: fetches /caseload + pending proposals, builds the view-model, and keeps three
// persistent regions (head, tabs, body) instead of tearing the whole screen down on every change (review
// I5/I6). The head — and its search `<input>` — and the tablist are built once per mount and updated in
// place, so typing or a tab click never loses focus (I5, final review B I4). Tab clicks only swap the body and sync the URL with
// `history.replaceState` so they don't trigger main.js's hashchange remount (I6a). A background refresh
// (from a live `events` message) never blanks the table to a loading state, and a failed refresh keeps the
// last good data on screen with a toast instead of replacing it with the full error state (I6b).
import { h, clear } from "../../core/dom.js";
import { loading, errorState } from "../../ui/states.js";
import { caseloadModel } from "./model.js";
import { renderHead, createTabs, renderBody } from "./view.js";

const EVENT_TYPES = new Set(["alert", "proposal_created", "proposal_decided", "summary_ready", "set_done"]);
const REFRESH_DEBOUNCE_MS = 1000;
const SEARCH_DEBOUNCE_MS = 120;

function fmtExpiry(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }).format(d);
  } catch {
    return d.toISOString();
  }
}

// A key that identifies the same control across a body rebuild: a row by its patient id, anything else by
// tag, class and text (the "Show N more" button, the rail's links). Used to put focus back after a refresh.
function focusKey(el) {
  if (!el || !el.getAttribute) return null;
  const rowId = el.getAttribute("data-row-id");
  if (rowId) return `row:${rowId}`;
  return `${el.tagName}|${el.getAttribute("class") || ""}|${el.textContent}`;
}

function restoreFocus(host, key) {
  if (!key) return;
  const match = [...host.querySelectorAll("a"), ...host.querySelectorAll("button")].find((el) => focusKey(el) === key);
  match?.focus?.();
}

// Updates the URL to reflect the active tab without firing `hashchange` (I6a), so main.js never remounts
// this screen just because the query string changed. Browsers only; a no-op where `history` isn't global
// (e.g. node:test), which is fine since those tests assert on rendered state, not the URL.
function syncFilterQuery(key) {
  if (typeof globalThis.history === "undefined" || typeof globalThis.location === "undefined") return;
  const path = globalThis.location.hash.replace(/^#/, "").split("?")[0] || "/caseload";
  globalThis.history.replaceState(null, "", `#${path}?filter=${encodeURIComponent(key)}`);
}

// A native <dialog> (review Minor 11): showModal() provides a focus trap, Escape-to-close and an inert
// background for free. Returns the dialog element (for the caller to close on unmount/sign-out), or null
// when there's nobody to pair (also fixes "posts to /patients//pairing-code" with an empty select).
function openAddPatientDialog(ctx, patients, triggerBtn) {
  if (patients.length === 0) {
    ctx.toast({ kind: "info", text: "No patients to add yet." });
    return null;
  }
  const select = h("select", { id: "add-patient-select" },
    patients.map((p) => h("option", { value: p.id }, p.name)));
  const codeEl = h("p", { class: "dialog__code", hidden: true });
  const expiryEl = h("p", { class: "meta dialog__expiry", hidden: true });
  const hintEl = h("p", { class: "meta dialog__hint", hidden: true }, "Read this to the patient in the Mira app.");
  const errorEl = h("p", { class: "dialog__error", role: "alert" });
  const createBtn = h("button", { class: "btn btn--primary", type: "button", onclick: onCreate }, "Create code");

  function close() {
    dialogEl.close();
  }

  async function onCreate() {
    if (!select.value) return;
    clear(errorEl);
    createBtn.setAttribute("disabled", "");
    try {
      const res = await ctx.api.pairingCode(select.value);
      codeEl.textContent = (res && res.code) || "";
      codeEl.hidden = false;
      if (res && res.expires_at) {
        expiryEl.textContent = `Valid until ${fmtExpiry(res.expires_at)}`;
        expiryEl.hidden = false;
      }
      hintEl.hidden = false;
    } catch {
      errorEl.textContent = "Couldn't create a pairing code. Try again.";
    } finally {
      createBtn.removeAttribute("disabled");
    }
  }

  const dialogEl = h("dialog", { class: "dialog", "aria-labelledby": "add-patient-title" },
    h("h2", { id: "add-patient-title", class: "card__title" }, "Add patient"),
    h("label", { class: "dialog__label", for: "add-patient-select" }, "Patient"),
    select,
    errorEl,
    createBtn,
    codeEl,
    expiryEl,
    hintEl,
    h("button", { class: "btn btn--secondary dialog__close", type: "button", onclick: close }, "Close"));

  dialogEl.addEventListener("close", () => {
    dialogEl.remove();
    triggerBtn?.focus?.();
  });

  document.body.append(dialogEl);
  dialogEl.showModal();
  select.focus?.();
  return dialogEl;
}

function normalizeFilter(filter) {
  return ["all", "needs", "watch", "on_track"].includes(filter) ? filter : "all";
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    let seq = 0;
    const state = {
      filter: normalizeFilter(route.query && route.query.filter),
      search: "",
      showAllOnTrack: false,
      caseloadJson: null,
      proposalsJson: null,
      caseloadErr: null,
      loaded: false,
    };
    let patientsForDialog = [];
    let refreshTimer = null;
    let searchTimer = null;
    let openDialog = null;
    let headRefs = null;

    const headHost = h("div", {});
    const tabs = createTabs({ onTabClick: (key) => onTabClick(key) });
    const tabsHost = h("div", {}, tabs.el);
    let lastBodyKey = null;
    const bodyHost = h("div", {});
    root.append(headHost, tabsHost, bodyHost);

    function currentModel() {
      return caseloadModel(state.caseloadJson, state.proposalsJson,
        { filter: state.filter, search: state.search, showAllOnTrack: state.showAllOnTrack, now: new Date() });
    }

    function onSearchInput(value) {
      state.search = value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(refreshViews, SEARCH_DEBOUNCE_MS);
    }

    function onAddPatient(e) {
      openDialog = openAddPatientDialog(ctx, patientsForDialog, e.currentTarget);
    }

    function onTabClick(key) {
      state.filter = key;
      state.showAllOnTrack = false;
      syncFilterQuery(key);
      refreshViews();
    }

    function onShowMoreOnTrack() {
      state.showAllOnTrack = true;
      refreshViews();
    }

    function onRowClick(id) {
      ctx.store.set({ selectedPatientId: id });
    }

    function buildHead() {
      headRefs = renderHead(currentModel(), { search: state.search, caps: ctx.caps, onSearchInput, onAddPatient });
      headHost.replaceChildren(headRefs.el);
    }

    // Re-renders only the tabs and the table/rail body; the head (and its search input) is untouched, so
    // typing, caret position and focus all survive every redraw (I5), and a tab click never loses the
    // keyboard's place on the tablist (I6a).
    function refreshViews() {
      if (!alive) return;
      const model = currentModel();
      if (headRefs) headRefs.subtitleEl.textContent = model.subtitle;
      tabs.update(model);
      if (!state.loaded) {
        lastBodyKey = null;
        bodyHost.replaceChildren(loading("Loading caseload…"));
      } else if (state.caseloadErr && !state.caseloadJson) {
        lastBodyKey = null;
        bodyHost.replaceChildren(errorState(state.caseloadErr, () => load(false)));
      } else {
        // A background refresh (every set_done during a live session) usually changes nothing visible: skip
        // the rebuild then; otherwise rebuild and put focus back on the same row/control (final review B, I4).
        const bodyKey = JSON.stringify({ groups: model.groups, moreOnTrack: model.moreOnTrack, today: model.today,
          copilot: model.copilot, live: model.live });
        if (bodyKey === lastBodyKey) return;
        lastBodyKey = bodyKey;
        const active = globalThis.document && globalThis.document.activeElement;
        const key = active && bodyHost.contains(active) ? focusKey(active) : null;
        bodyHost.replaceChildren(renderBody(model, { onShowMoreOnTrack, onRowClick }));
        restoreFocus(bodyHost, key);
      }
    }

    async function load(isRefresh) {
      const mySeq = ++seq;
      if (!isRefresh) {
        state.loaded = false;
        state.caseloadErr = null;
        refreshViews();
      }
      const [caseloadRes, proposalsRes] = await Promise.allSettled([
        ctx.api.caseload(),
        ctx.api.proposals({ status: "pending" }),
      ]);
      if (!alive || mySeq !== seq) return; // superseded by a newer load()
      if (caseloadRes.status === "fulfilled") {
        state.caseloadJson = caseloadRes.value;
        state.caseloadErr = null;
        patientsForDialog = (caseloadRes.value.patients || []).map((p) => ({ id: p.id, name: p.name }));
      } else {
        state.caseloadErr = caseloadRes.reason;
        if (isRefresh && state.caseloadJson) {
          ctx.toast({ kind: "error", text: "Couldn't refresh the caseload. Showing the last loaded data." });
        }
      }
      if (proposalsRes.status === "fulfilled") {
        state.proposalsJson = proposalsRes.value;
        ctx.store.set({ proposalsWaiting: (proposalsRes.value.proposals || []).length });
      } else if (!isRefresh) {
        state.proposalsJson = null;
      }
      state.loaded = true;
      refreshViews();
    }

    function onEvents(msg) {
      if (!msg || !EVENT_TYPES.has(msg.type)) return;
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => load(true), REFRESH_DEBOUNCE_MS);
    }

    const unsubEvents = ctx.sockets.subscribe("events", onEvents);
    buildHead();
    refreshViews();
    load(false);

    return () => {
      alive = false;
      clearTimeout(refreshTimer);
      clearTimeout(searchTimer);
      unsubEvents();
      openDialog?.close();
    };
  },
};
