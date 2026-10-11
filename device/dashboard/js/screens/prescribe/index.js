// Prescribe screen mount (Figma 207:282): exercise plan, editor, "When should Mira flag a rep?", heart-rate
// limit, emergency contact (read-only) and the Mira Assistant proposals rail for this patient. Persistent
// regions are built once; the editor/flags/limit cards are rebuilt only on a deliberate exercise switch or a
// data reload, never while a stepper/slider is mid-interaction (js/ui/stepper.js and slider.js keep their own
// element alive across value changes).
import { h, clear } from "../../core/dom.js";
import { proposalCard } from "../../ui/proposalCard.js";
import { prescribeModel, diffPrescription, buildPutBody, hrLimitChanged } from "./model.js";
import {
  renderHead, renderExerciseCard, renderEditorCard, renderFlagsCard, renderHrLimitCard, renderEmergencyCard,
  renderProposalsRail, renderSaveBar, renderUnsavedBar,
} from "./view.js";

const EXERCISES_CAP = "/exercises";
const PID_RE = /^[A-Za-z0-9_-]{1,64}$/;

// The editor/flags fields the therapist can actually touch (keys this screen's `edits` map stores per
// exercise id — a subset merged on top of prescribeModel()'s computed defaults for that exercise).
function editorValuesFor(patientJson, prescriptionsJson, progressJson, exerciseId, edits) {
  const m = prescribeModel(patientJson, prescriptionsJson, progressJson, exerciseId);
  const override = edits.get(exerciseId) || {};
  return {
    editor: { ...m.editor, ...override },
    flags: { ...m.flags, ...override },
    limits: m.limits,
  };
}

function originalRxFor(prescriptionsJson, exerciseId) {
  const list = (prescriptionsJson && prescriptionsJson.prescriptions) || [];
  return list.find((r) => r.exercise_id === exerciseId && r.active !== false) || null;
}

function firstNameOf(name) {
  return String(name || "").trim().split(/\s+/)[0] || "your patient";
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    const pid = PID_RE.test((route && route.params && route.params.pid) || "") ? route.params.pid : null;

    if (!pid) {
      root.append(h("p", { class: "meta" }, "No patient selected."));
      return () => {};
    }

    const state = {
      patientJson: null,
      prescriptionsJson: null,
      progressJson: null,
      selectedExerciseId: null,
      edits: new Map(), // exerciseId -> partial editor/flags overrides
      hrLimitEdit: null, // number once touched, else null (untouched)
      proposals: [],
      pendingHref: null,
    };
    const proposalCards = new Map();

    // ---- persistent regions ----
    const head = renderHead(null);
    const exerciseHost = h("div", {});
    const editorHost = h("div", {});
    const flagsHost = h("div", {});
    const limitHost = h("div", {});
    const emergencyHost = h("div", {});
    const rail = renderProposalsRail();
    const saveBar = renderSaveBar({ onSave: () => save(), onDiscard: () => discard() });
    const unsavedHost = h("div", {});
    const mainCol = h("div", { class: "prescribe__main" },
      exerciseHost, editorHost, flagsHost, limitHost, emergencyHost, saveBar.el);
    root.append(head.el, unsavedHost, h("div", { class: "prescribe__grid" }, mainCol, rail.el));

    function isDirty() {
      if (hrLimitChanged(baseHrLimit(), state.hrLimitEdit)) return true;
      for (const [exerciseId] of state.edits) {
        const original = originalRxFor(state.prescriptionsJson, exerciseId);
        if (!original) continue;
        const vals = editorValuesFor(state.patientJson, state.prescriptionsJson, state.progressJson,
          exerciseId, state.edits);
        if (diffPrescription(original, { ...vals.editor, ...vals.flags }) != null) return true;
      }
      return false;
    }

    function baseHrLimit() {
      const m = prescribeModel(state.patientJson, state.prescriptionsJson, state.progressJson,
        state.selectedExerciseId);
      return m.hrLimit;
    }

    function refreshDirty() {
      saveBar.setDirty(isDirty());
    }

    // ---- exercise plan card ----
    function buildExerciseCard() {
      const m = prescribeModel(state.patientJson, state.prescriptionsJson, state.progressJson,
        state.selectedExerciseId);
      const canAdd = Boolean(ctx.caps && ctx.caps.has && ctx.caps.has(EXERCISES_CAP));
      const card = renderExerciseCard(m.exercises, {
        onSelect: (id) => { state.selectedExerciseId = id; buildEditorFlagsLimit(); },
        onSearch: (q) => searchLibrary(q),
        canAddFromLibrary: canAdd,
      });
      exerciseHost.replaceChildren(card.el);
    }

    async function searchLibrary(q) {
      if (!q || !q.trim()) return;
      try {
        const res = await ctx.api.exercises(q.trim());
        if (!alive) return;
        const items = (res && Array.isArray(res.exercises)) ? res.exercises : [];
        // Minimal "add" affordance: picking a result prescribes it with sensible defaults, then reloads.
        const list = h("ul", { class: "prescribe__search-list" }, items.slice(0, 8).map((x) => h("li", {},
          h("button", { class: "btn btn--ghost", type: "button",
            onclick: () => addFromLibrary(x.id || x) }, String(x.name || x.id || x)))));
        exerciseHost.querySelector?.(".prescribe__search-results")?.replaceChildren(list);
      } catch { /* search is a convenience; a failed lookup just shows nothing */ }
    }

    async function addFromLibrary(exerciseId) {
      try {
        await ctx.api.putPrescription(pid, { exercise_id: exerciseId, sets: 3, reps: 10 });
      } catch {
        if (alive) ctx.toast({ kind: "error", text: "Couldn't add that exercise. Try again." });
        return;
      }
      if (!alive) return;
      state.selectedExerciseId = exerciseId;
      await reloadPrescriptions();
    }

    // ---- editor / flags / limit cards ----
    function buildEditorFlagsLimit() {
      const vals = editorValuesFor(state.patientJson, state.prescriptionsJson, state.progressJson,
        state.selectedExerciseId, state.edits);
      const editorCard = renderEditorCard(vals.editor, vals.limits, {
        onChange: (partial) => {
          const cur = state.edits.get(state.selectedExerciseId) || {};
          state.edits.set(state.selectedExerciseId, { ...cur, ...partial });
          refreshDirty();
        },
      });
      editorHost.replaceChildren(editorCard.el);

      const flagsCard = renderFlagsCard(vals.flags, {
        onChange: (partial) => {
          const cur = state.edits.get(state.selectedExerciseId) || {};
          state.edits.set(state.selectedExerciseId, { ...cur, ...partial });
          refreshDirty();
        },
      });
      flagsHost.replaceChildren(flagsCard.el);

      const m = prescribeModel(state.patientJson, state.prescriptionsJson, state.progressJson,
        state.selectedExerciseId);
      const hrValue = state.hrLimitEdit != null ? state.hrLimitEdit : m.hrLimit;
      const limitCard = renderHrLimitCard(hrValue, {
        onChange: (v) => { state.hrLimitEdit = v; refreshDirty(); },
      });
      limitHost.replaceChildren(limitCard.el);

      buildExerciseCard(); // re-highlight the selected row
      refreshDirty();
    }

    // ---- save / discard ----
    async function save() {
      if (!isDirty()) return;
      saveBar.saveBtn.setAttribute("disabled", "");
      saveBar.discardBtn.setAttribute("disabled", "");
      try {
        for (const [exerciseId] of state.edits) {
          const original = originalRxFor(state.prescriptionsJson, exerciseId);
          if (!original) continue;
          const vals = editorValuesFor(state.patientJson, state.prescriptionsJson, state.progressJson,
            exerciseId, state.edits);
          const diff = diffPrescription(original, { ...vals.editor, ...vals.flags });
          if (diff == null) continue;
          await ctx.api.putPrescription(pid, buildPutBody(original, diff));
        }
        if (hrLimitChanged(baseHrLimit(), state.hrLimitEdit)) {
          await ctx.api.setLimits(pid, { hrLimit: state.hrLimitEdit });
        }
      } catch {
        if (!alive) return;
        refreshDirty();
        ctx.toast({ kind: "error", text: "Couldn't save the plan. Try again." });
        return;
      }
      if (!alive) return;
      state.edits.clear();
      state.hrLimitEdit = null;
      const name = firstNameOf(state.patientJson && state.patientJson.patient && state.patientJson.patient.name);
      await reloadAll();
      if (!alive) return;
      ctx.toast({ kind: "success", text: `Saved. Changes reach Mira the next time ${name} starts a session.` });
    }

    function discard() {
      state.edits.clear();
      state.hrLimitEdit = null;
      buildEditorFlagsLimit();
    }

    // ---- loading / reloading ----
    async function reloadPrescriptions() {
      try {
        state.prescriptionsJson = await ctx.api.prescriptions(pid);
      } catch { /* keep the stale list rather than blanking the screen */ }
      if (!alive) return;
      buildEditorFlagsLimit();
    }

    async function reloadAll() {
      const [patientRes, rxRes, progressRes] = await Promise.allSettled([
        ctx.api.patient(pid), ctx.api.prescriptions(pid), ctx.api.progress(pid, {}),
      ]);
      if (!alive) return;
      if (patientRes.status === "fulfilled") state.patientJson = patientRes.value;
      if (rxRes.status === "fulfilled") state.prescriptionsJson = rxRes.value;
      if (progressRes.status === "fulfilled") state.progressJson = progressRes.value;
      head.titleEl.textContent = state.patientJson && state.patientJson.patient
        ? `Prescribe · ${state.patientJson.patient.name}` : "Prescribe";
      emergencyHost.replaceChildren(renderEmergencyCard(
        prescribeModel(state.patientJson, state.prescriptionsJson, state.progressJson,
          state.selectedExerciseId).emergency));
      buildEditorFlagsLimit();
    }

    // ---- Mira Assistant proposals rail ----
    function currentRxForProposal(p) {
      return originalRxFor(state.prescriptionsJson, p.change && p.change.exercise_id);
    }

    function cardFor(p) {
      const current = p.status === "pending" ? currentRxForProposal(p) : null;
      const key = JSON.stringify([p.status, p.change, p.decided_by, p.decided_at, current]);
      const existing = proposalCards.get(p.id);
      if (existing && existing.key === key) return existing.el;
      const el = proposalCard(p, {
        current, patientStatus: "watch",
        onApprove: (prop) => decideProposal(prop, () => ctx.api.approveProposal(prop.id), "Approved."),
        onEdit: (prop, change) => decideProposal(prop, () => ctx.api.editProposal(prop.id, change),
          "Approved with your changes."),
        onDismiss: (prop) => decideProposal(prop, () => ctx.api.dismissProposal(prop.id, null), "Dismissed."),
      });
      proposalCards.set(p.id, { el, key });
      return el;
    }

    async function decideProposal(p, call, successText) {
      try {
        await call();
      } catch (err) {
        if (!alive) return false;
        ctx.toast({ kind: "error", text: (err && err.status === 403)
          ? "Only this patient's therapist can decide this." : "Couldn't save the decision. Try again." });
        return false;
      }
      if (!alive) return true;
      ctx.toast({ kind: "success", text: successText });
      await reloadAll();
      if (!alive) return true;
      await loadProposals();
      return true;
    }

    async function loadProposals() {
      try {
        const res = await ctx.api.proposals({ status: "pending", patientId: pid });
        if (!alive) return;
        state.proposals = (res && res.proposals) || [];
      } catch {
        if (!alive) return;
        state.proposals = [];
      }
      const live = new Set(state.proposals.map((p) => p.id));
      for (const id of proposalCards.keys()) if (!live.has(id)) proposalCards.delete(id);
      if (state.proposals.length === 0) {
        rail.listHost.replaceChildren(h("p", { class: "meta" }, "No pending proposals for this patient."));
      } else {
        rail.listHost.replaceChildren(...state.proposals.map(cardFor));
      }
    }

    // ---- unsaved-changes guard (in-page bar, never window.confirm) ----
    function showUnsavedBar() {
      unsavedHost.replaceChildren(renderUnsavedBar({
        onStay: () => { state.pendingHref = null; clear(unsavedHost); },
        onDiscardAndLeave: () => {
          const href = state.pendingHref;
          state.edits.clear();
          state.hrLimitEdit = null;
          state.pendingHref = null;
          clear(unsavedHost);
          if (href) globalThis.location.hash = href.replace(/^#/, "");
        },
      }));
    }

    // Walked by hand (rather than Element.closest with an attribute-prefix selector) so this works the same
    // in a browser and under the test fakedom, which only supports simple selectors.
    function nearestHashAnchor(node) {
      let n = node;
      while (n) {
        if (n.tagName === "A") {
          const href = n.getAttribute && n.getAttribute("href");
          return href && href.startsWith("#") ? n : null;
        }
        n = n.parentNode;
      }
      return null;
    }

    function onDocClick(e) {
      if (!isDirty()) return;
      const a = nearestHashAnchor(e.target);
      if (!a) return;
      e.preventDefault();
      e.stopPropagation();
      state.pendingHref = a.getAttribute("href");
      showUnsavedBar();
    }
    document.addEventListener("click", onDocClick, true);

    function onBeforeUnload(e) {
      if (!isDirty()) return;
      e.preventDefault();
      e.returnValue = "";
    }
    // No `window` under node:test's fakedom; guarded so the screen still mounts there.
    const hasWindowEvents = typeof globalThis.addEventListener === "function";
    if (hasWindowEvents) globalThis.addEventListener("beforeunload", onBeforeUnload);

    // ---- boot ----
    async function boot() {
      ctx.store.set({ selectedPatientId: pid });
      await reloadAll();
      if (!alive) return;
      loadProposals();
    }
    boot();

    return () => {
      alive = false;
      document.removeEventListener("click", onDocClick, true);
      if (hasWindowEvents) globalThis.removeEventListener("beforeunload", onBeforeUnload);
    };
  },
};
