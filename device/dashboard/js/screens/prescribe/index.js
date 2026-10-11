// Prescribe screen mount (Figma 207:282): exercise plan, editor, "When should Mira flag a rep?", heart-rate
// limit, emergency contact (read-only) and the Mira Assistant proposals rail for this patient. Persistent
// regions are built once; the editor/flags/limit cards are rebuilt only on a deliberate exercise switch or a
// data reload, never while a stepper/slider is mid-interaction (js/ui/stepper.js and slider.js keep their own
// element alive across value changes).
import { h, clear } from "../../core/dom.js";
import { patientMeta, firstName } from "../../core/format.js";
import { exerciseName } from "../../core/exercises.js";
import { errorState } from "../../ui/states.js";
import { proposalCard } from "../../ui/proposalCard.js";
import { decideProposal } from "../../ui/proposalDecision.js";
import { prescribeModel, diffPrescription, buildPutBody, hrLimitChanged } from "./model.js";
import {
  renderHead, renderExerciseCard, renderSearchResults, renderEditorCard, renderReferenceRepCard, renderFlagsCard,
  renderHrLimitCard, renderEmergencyCard, renderProposalsRail, renderUnsavedBar,
} from "./view.js";

const EXERCISES_CAP = "/exercises";
const PID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const SEARCH_DEBOUNCE_MS = 250;
const PROPOSAL_EVENTS = new Set(["proposal_created", "proposal_decided"]);
// A library pick is staged with these defaults; nothing is written until "Save plan".
const STAGED_DEFAULTS = { sets: 3, reps: 10 };

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
  return list.find((r) => r.exercise_id === exerciseId && r.active !== false && !r.staged) || null;
}

function firstNameOf(name) {
  return firstName(name) || "your patient";
}

// "Squats" -> "squat" for "Do one squat in front of Mira" (good enough for the known exercise names).
function singular(name) {
  const lower = String(name || "rep").toLowerCase();
  return lower.endsWith("s") && !lower.endsWith("ss") ? lower.slice(0, -1) : lower;
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
      staged: new Map(), // exerciseId -> staged library pick (not written until Save plan)
      loadError: null, // set when the patient or prescriptions can't be loaded: no editing, no Save
    };
    const proposalCards = new Map();
    let searchTimer = null;
    let searchSeq = 0;
    let proposalsTimer = null;

    // ---- persistent regions ----
    const head = renderHead({ onSave: () => save(), onDiscard: () => discard() });
    const saveBar = head; // Save / Discard live in the page head (Figma: top right)
    const exerciseHost = h("div", {});
    const editorHost = h("div", {});
    const referenceHost = h("div", {});
    const flagsHost = h("div", {});
    const limitHost = h("div", {});
    const emergencyHost = h("div", {});
    const rail = renderProposalsRail();
    const unsavedHost = h("div", {});
    // Three columns (Figma): plan + emergency | editor + flags + heart rate | reference rep + proposals.
    const gridEl = h("div", { class: "prescribe__grid" },
      h("div", { class: "prescribe__col" }, exerciseHost, emergencyHost),
      h("div", { class: "prescribe__col" }, editorHost, flagsHost, limitHost),
      h("div", { class: "prescribe__col" }, referenceHost, rail.el));
    const bodyHost = h("div", {}, gridEl);
    root.append(head.el, unsavedHost, bodyHost);

    // GET /prescriptions plus any staged library picks, as one list for the model.
    function effectiveRxJson() {
      const base = (state.prescriptionsJson && state.prescriptionsJson.prescriptions) || [];
      if (state.staged.size === 0) return state.prescriptionsJson;
      return { ...(state.prescriptionsJson || {}), prescriptions: [...base, ...state.staged.values()] };
    }

    function firstNameHere() {
      return firstNameOf(state.patientJson && state.patientJson.patient && state.patientJson.patient.name);
    }

    function isDirty() {
      if (state.loadError) return false;
      if (state.staged.size > 0) return true;
      if (hrLimitChanged(baseHrLimit(), state.hrLimitEdit)) return true;
      for (const [exerciseId] of state.edits) {
        const original = originalRxFor(state.prescriptionsJson, exerciseId);
        if (!original) continue;
        const vals = editorValuesFor(state.patientJson, effectiveRxJson(), state.progressJson,
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
      const m = prescribeModel(state.patientJson, effectiveRxJson(), state.progressJson,
        state.selectedExerciseId);
      const canAdd = Boolean(ctx.caps && ctx.caps.has && ctx.caps.has(EXERCISES_CAP));
      const n = m.exercises.length;
      const card = renderExerciseCard(m.exercises, {
        onSelect: (id) => { state.selectedExerciseId = id; buildEditorFlagsLimit(); },
        onSearch: (q) => onSearchInput(q),
        canAddFromLibrary: canAdd,
        summary: n ? `${n} ${n === 1 ? "exercise" : "exercises"}` : null,
      });
      exerciseHost.replaceChildren(card.el);
    }

    // Debounced, and sequenced so a slow older response never replaces newer results (final review B, I7).
    function onSearchInput(q) {
      clearTimeout(searchTimer);
      const mySeq = ++searchSeq;
      searchTimer = setTimeout(() => searchLibrary(q, mySeq), SEARCH_DEBOUNCE_MS);
    }

    async function searchLibrary(q, mySeq) {
      const resultsHost = exerciseHost.querySelector?.(".prescribe__search-results");
      if (!q || !q.trim()) { resultsHost?.replaceChildren(); return; }
      let items = [];
      try {
        const res = await ctx.api.exercises(q.trim());
        items = (res && Array.isArray(res.exercises)) ? res.exercises : [];
      } catch { /* search is a convenience; a failed lookup just shows nothing */ }
      if (!alive || mySeq !== searchSeq) return;
      const list = items.slice(0, 8)
        .map((x) => (typeof x === "string" ? { id: x } : x))
        .filter((x) => x && PID_RE.test(String(x.id || "")))
        .map((x) => ({ id: String(x.id), name: x.name ? String(x.name) : exerciseName(x.id) }));
      exerciseHost.querySelector?.(".prescribe__search-results")
        ?.replaceChildren(renderSearchResults(list, { onAdd: (id) => stageFromLibrary(id, list) }));
    }

    // A library pick only stages the exercise (dirty state); "Save plan" writes it, "Discard changes" drops it.
    function stageFromLibrary(exerciseId, list) {
      const already = originalRxFor(state.prescriptionsJson, exerciseId) || state.staged.has(exerciseId);
      if (!already) {
        const hit = (list || []).find((x) => x.id === exerciseId);
        state.staged.set(exerciseId, { exercise_id: exerciseId, ...STAGED_DEFAULTS, targets: {}, limits: {},
          rule_overrides: {}, active: true, staged: true, display_name: hit ? hit.name : null });
      }
      state.selectedExerciseId = exerciseId;
      buildEditorFlagsLimit();
    }

    // ---- editor / flags / limit cards ----
    function buildEditorFlagsLimit() {
      const rxJson = effectiveRxJson();
      const vals = editorValuesFor(state.patientJson, rxJson, state.progressJson,
        state.selectedExerciseId, state.edits);
      const name = firstNameHere();
      const selected = vals.editor.exerciseId;
      const stagedName = selected && state.staged.get(selected) && state.staged.get(selected).display_name;
      const title = selected ? (stagedName || exerciseName(selected)) : "Exercise";
      const editorCard = renderEditorCard(vals.editor, vals.limits, {
        title,
        onChange: (partial) => {
          const id = vals.editor.exerciseId;
          const cur = state.edits.get(id) || {};
          state.edits.set(id, { ...cur, ...partial });
          refreshDirty();
        },
      });
      editorHost.replaceChildren(editorCard.el);
      referenceHost.replaceChildren(renderReferenceRepCard({ exerciseLabel: singular(title), firstName: name }));

      const flagsCard = renderFlagsCard(vals.flags, {
        firstName: name,
        onChange: (partial) => {
          const id = vals.editor.exerciseId;
          const cur = state.edits.get(id) || {};
          state.edits.set(id, { ...cur, ...partial });
          refreshDirty();
        },
      });
      flagsHost.replaceChildren(flagsCard.el);

      const m = prescribeModel(state.patientJson, rxJson, state.progressJson, state.selectedExerciseId);
      const hrValue = state.hrLimitEdit != null ? state.hrLimitEdit : m.hrLimit;
      const limitCard = renderHrLimitCard(hrValue, {
        firstName: name,
        onChange: (v) => { state.hrLimitEdit = v; refreshDirty(); },
      });
      limitHost.replaceChildren(limitCard.el);

      buildExerciseCard(); // re-highlight the selected row
      refreshDirty();
    }

    // ---- save / discard ----
    // The PUT body for a staged library pick: sets/reps as shown in the editor, plus any edited targets/flags.
    function stagedPutBody(exerciseId) {
      const row = state.staged.get(exerciseId);
      const vals = editorValuesFor(state.patientJson, effectiveRxJson(), state.progressJson, exerciseId, state.edits);
      const diff = diffPrescription(row, { ...vals.editor, ...vals.flags }) || { exercise_id: exerciseId };
      return { ...buildPutBody(row, diff), sets: diff.sets ?? row.sets, reps: diff.reps ?? row.reps };
    }

    async function save() {
      if (!isDirty()) return;
      saveBar.saveBtn.setAttribute("disabled", "");
      saveBar.discardBtn.setAttribute("disabled", "");
      try {
        for (const exerciseId of [...state.staged.keys()]) {
          await ctx.api.putPrescription(pid, stagedPutBody(exerciseId));
          state.staged.delete(exerciseId);
          state.edits.delete(exerciseId);
        }
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
      state.staged.clear();
      state.hrLimitEdit = null;
      const name = firstNameHere();
      await reloadAll();
      if (!alive) return;
      ctx.toast({ kind: "success", text: `Saved. Changes reach Mira the next time ${name} starts a session.` });
    }

    function discard() {
      state.edits.clear();
      state.staged.clear();
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

    // The patient and the prescriptions are required: if either fails (401/403/404/503/offline) the screen
    // shows an error with Retry instead of editable defaults, and Save stays disabled (final review B, I8).
    // Progress only feeds "Today's average", so its failure is tolerated.
    async function reloadAll() {
      const [patientRes, rxRes, progressRes] = await Promise.allSettled([
        ctx.api.patient(pid), ctx.api.prescriptions(pid), ctx.api.progress(pid, {}),
      ]);
      if (!alive) return;
      const failed = [patientRes, rxRes].find((r) => r.status !== "fulfilled");
      if (failed) {
        state.loadError = failed.reason || new Error("load failed");
        refreshDirty();
        bodyHost.replaceChildren(h("section", { class: "card" }, errorState(state.loadError, () => retryLoad())));
        return;
      }
      state.loadError = null;
      if (gridEl.parentNode !== bodyHost) bodyHost.replaceChildren(gridEl);
      state.patientJson = patientRes.value;
      state.prescriptionsJson = rxRes.value;
      if (progressRes.status === "fulfilled") state.progressJson = progressRes.value;
      const patient = state.patientJson && state.patientJson.patient;
      head.metaEl.textContent = patientMeta(patient);
      head.subtitleEl.textContent = `Changes reach Mira the next time ${firstNameHere()} starts a session.`;
      emergencyHost.replaceChildren(renderEmergencyCard(
        prescribeModel(state.patientJson, state.prescriptionsJson, state.progressJson,
          state.selectedExerciseId).emergency, { firstName: firstNameHere() }));
      buildEditorFlagsLimit();
    }

    async function retryLoad() {
      await reloadAll();
      if (alive && !state.loadError) loadProposals();
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
        onApprove: (prop) => decide(() => ctx.api.approveProposal(prop.id), "Approved."),
        onEdit: (prop, change) => decide(() => ctx.api.editProposal(prop.id, change), "Approved with your changes."),
        onDismiss: (prop) => decide(() => ctx.api.dismissProposal(prop.id, null), "Dismissed."),
      });
      proposalCards.set(p.id, { el, key });
      return el;
    }

    // Same error mapping as the Assistant (409 reloads instead of letting the therapist retry forever).
    async function decide(call, successText) {
      const ok = await decideProposal(ctx, call, { successText, isAlive: () => alive,
        onConflict: () => loadProposals() });
      if (!ok || !alive) return ok;
      await reloadAll();
      if (!alive) return true;
      await loadProposals();
      return true;
    }

    async function loadProposals() {
      if (state.loadError) return;
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
          state.staged.clear();
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

    // Proposals for this patient can be created or decided elsewhere (the Assistant, another tab): refresh.
    const unsubEvents = ctx.sockets.subscribe("events", (msg) => {
      if (!msg || !PROPOSAL_EVENTS.has(msg.type)) return;
      clearTimeout(proposalsTimer);
      proposalsTimer = setTimeout(() => { if (alive) loadProposals(); }, 800);
    });

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
      clearTimeout(searchTimer);
      clearTimeout(proposalsTimer);
      unsubEvents?.();
      document.removeEventListener("click", onDocClick, true);
      if (hasWindowEvents) globalThis.removeEventListener("beforeunload", onBeforeUnload);
    };
  },
};
