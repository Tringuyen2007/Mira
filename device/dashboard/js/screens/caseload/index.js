// Caseload screen mount: fetches /caseload + pending proposals, builds the view-model, re-renders on
// filter/search/showAllOnTrack change and on live `events` (debounced). Task brief step 3/4.
import { h, clear } from "../../core/dom.js";
import { loading, errorState } from "../../ui/states.js";
import { caseloadModel } from "./model.js";
import { render as renderView } from "./view.js";

const EVENT_TYPES = new Set(["alert", "proposal_created", "proposal_decided", "summary_ready", "set_done"]);
const DEBOUNCE_MS = 1000;

function openAddPatientDialog(ctx, patients, triggerBtn) {
  const overlay = h("div", { class: "dialog-overlay" });
  const select = h("select", { id: "add-patient-select" },
    patients.map((p) => h("option", { value: p.id }, p.name)));
  const codeEl = h("p", { class: "dialog__code", hidden: true });
  const hintEl = h("p", { class: "meta dialog__hint", hidden: true }, "Read this to the patient in the Mira app.");
  const errorEl = h("p", { class: "dialog__error", role: "alert" });
  const createBtn = h("button", { class: "btn btn--primary", type: "button", onclick: onCreate }, "Create code");

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKeydown);
    triggerBtn?.focus?.();
  }

  function onKeydown(e) {
    if (e.key === "Escape") close();
  }

  async function onCreate() {
    clear(errorEl);
    createBtn.setAttribute("disabled", "");
    try {
      const res = await ctx.api.pairingCode(select.value);
      const code = (res && res.code) || "";
      codeEl.textContent = code;
      codeEl.hidden = false;
      hintEl.hidden = false;
    } catch {
      errorEl.textContent = "Couldn't create a pairing code. Try again.";
    } finally {
      createBtn.removeAttribute("disabled");
    }
  }

  const dialog = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "add-patient-title" },
    h("h2", { id: "add-patient-title", class: "card__title" }, "Add patient"),
    h("label", { class: "dialog__label", for: "add-patient-select" }, "Patient"),
    select,
    errorEl,
    createBtn,
    codeEl,
    hintEl,
    h("button", { class: "btn btn--secondary dialog__close", type: "button", onclick: close }, "Close"));

  overlay.append(dialog);
  document.body.append(overlay);
  document.addEventListener("keydown", onKeydown);
  select.focus?.();
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    let state = {
      filter: (route.query && route.query.filter) || "all",
      search: "",
      showAllOnTrack: false,
      caseloadJson: null,
      proposalsJson: null,
      caseloadErr: null,
      loaded: false,
    };
    let patientsForDialog = [];
    let debounceTimer = null;

    const container = h("div", {});
    root.append(container);

    function draw() {
      if (!alive) return;
      clear(container);
      if (!state.loaded) {
        container.append(loading("Loading caseload…"));
        return;
      }
      if (state.caseloadErr) {
        container.append(errorState(state.caseloadErr, load));
        return;
      }
      const model = caseloadModel(state.caseloadJson, state.proposalsJson,
        { filter: state.filter, search: state.search, showAllOnTrack: state.showAllOnTrack, now: new Date() });
      container.append(renderView(model, {
        search: state.search,
        caps: ctx.caps,
        onTabClick: (key) => {
          state.filter = key;
          state.showAllOnTrack = false;
          ctx.navigate(`/caseload?filter=${encodeURIComponent(key)}`);
          draw();
        },
        onSearchInput: (value) => { state.search = value; draw(); },
        onShowMoreOnTrack: () => { state.showAllOnTrack = true; draw(); },
        onRowClick: (id) => { ctx.store.set({ selectedPatientId: id }); },
        onAddPatient: (e) => openAddPatientDialog(ctx, patientsForDialog, e.currentTarget),
      }));
    }

    async function load() {
      state.loaded = false;
      state.caseloadErr = null;
      draw();
      const [caseloadRes, proposalsRes] = await Promise.allSettled([
        ctx.api.caseload(),
        ctx.api.proposals({ status: "pending" }),
      ]);
      if (!alive) return;
      if (caseloadRes.status === "fulfilled") {
        state.caseloadJson = caseloadRes.value;
        state.caseloadErr = null;
        patientsForDialog = (caseloadRes.value.patients || []).map((p) => ({ id: p.id, name: p.name }));
      } else {
        state.caseloadErr = caseloadRes.reason;
      }
      if (proposalsRes.status === "fulfilled") {
        state.proposalsJson = proposalsRes.value;
        const count = (proposalsRes.value.proposals || []).length;
        ctx.store.set({ proposalsWaiting: count });
      } else {
        state.proposalsJson = null;
      }
      state.loaded = true;
      draw();
    }

    function onEvents(msg) {
      if (!msg || !EVENT_TYPES.has(msg.type)) return;
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(load, DEBOUNCE_MS);
    }

    const unsubEvents = ctx.sockets.subscribe("events", onEvents);
    load();

    return () => {
      alive = false;
      clearTimeout(debounceTimer);
      unsubEvents();
    };
  },
};
