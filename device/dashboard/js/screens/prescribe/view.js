// DOM builders for the Prescribe screen (Figma 207:282). Each render* function takes a plain view-model and
// returns either a plain element or {el, ...refs} when the host needs to update it in place later (steppers
// and sliders keep their own element alive — see js/ui/stepper.js / slider.js — so dragging one never gets
// interrupted by a re-render).
import { h } from "../../core/dom.js";
import { stepper } from "../../ui/stepper.js";
import { slider } from "../../ui/slider.js";

export function renderHead(patientName) {
  const titleEl = h("h1", { class: "page-title" }, patientName ? `Prescribe · ${patientName}` : "Prescribe");
  return { el: h("div", { class: "prescribe__head" }, titleEl), titleEl };
}

// ---- Exercise plan card -----------------------------------------------------------------------------------

export function renderExerciseList(exercises, { onSelect }) {
  if (exercises.length === 0) {
    return h("p", { class: "meta" }, "No active exercises yet.");
  }
  return h("ul", { class: "prescribe__exercise-list" }, exercises.map((ex) => h("li", {},
    h("button", {
      class: `prescribe__exercise-row${ex.selected ? " is-selected" : ""}`, type: "button",
      "aria-pressed": ex.selected ? "true" : "false", onclick: () => onSelect(ex.id),
    },
      h("input", { type: "checkbox", checked: true, disabled: true, tabindex: "-1", "aria-hidden": "true" }),
      h("span", { class: "prescribe__exercise-name" }, ex.name),
      h("span", { class: "prescribe__exercise-dose" }, ex.dose)))));
}

export function renderExerciseCard(exercises, { onSelect, onSearch, canAddFromLibrary }) {
  const listHost = h("div", {}, renderExerciseList(exercises, { onSelect }));
  const searchInput = canAddFromLibrary
    ? h("input", { type: "search", class: "prescribe__search", placeholder: "Add from exercise library…",
        "aria-label": "Add from exercise library", oninput: (e) => onSearch(e.target.value) })
    : null;
  const resultsHost = h("div", { class: "prescribe__search-results" });
  const el = h("section", { class: "card prescribe__card", "aria-label": "Exercise plan" },
    h("h2", { class: "card__title" }, "Exercise plan"),
    listHost,
    canAddFromLibrary ? h("div", { class: "prescribe__search-wrap" }, searchInput, resultsHost) : null);
  return { el, listHost, searchInput, resultsHost,
    setExercises: (list) => listHost.replaceChildren(renderExerciseList(list, { onSelect })) };
}

// ---- Editor card ------------------------------------------------------------------------------------------

const PCT_TEXT = (v) => ({ text: `${v}%`, valueText: `${v} percent` });
const DEG_TEXT = (v) => ({ text: `${v}°`, valueText: `${v} degrees` });

export function renderEditorCard(editor, limits, { onChange }) {
  if (!editor.exerciseId) {
    return { el: h("section", { class: "card prescribe__card" },
      h("h2", { class: "card__title" }, "Editor"),
      h("p", { class: "meta" }, "Select an exercise to edit it.")) };
  }
  const minReps = limits.min_reps ?? 1, maxReps = limits.max_reps ?? 100;
  const minRest = limits.min_rest_s ?? 0, maxRest = limits.max_rest_s ?? 600;

  const setsStepper = stepper({ label: "sets", min: 1, max: 10, value: editor.sets ?? 1,
    onChange: (v) => onChange({ sets: v }) });
  const repsStepper = stepper({ label: "reps", min: minReps, max: maxReps, value: editor.reps ?? minReps,
    onChange: (v) => onChange({ reps: v }) });
  const restStepper = stepper({ label: "rest between sets", min: minRest, max: maxRest, step: 15,
    value: editor.restS, format: (v) => `${v}s`, onChange: (v) => onChange({ restS: v }) });
  const tempoStepper = stepper({ label: "tempo", min: 1, max: 10, value: editor.tempoS,
    format: (v) => `${v}s`, onChange: (v) => onChange({ tempoS: v }) });
  const hasKnee = editor.kneeTarget != null;
  const kneeStepper = hasKnee ? stepper({ label: "knee bend target", min: 0, max: 180, value: editor.kneeTarget,
    format: (v) => `${v}°`, onChange: (v) => onChange({ kneeTarget: v }) }) : null;

  function field(label, control) {
    return h("div", { class: "prescribe__field" }, h("span", { class: "prescribe__field-label" }, label), control.el);
  }

  const el = h("section", { class: "card prescribe__card", "aria-label": "Editor" },
    h("h2", { class: "card__title" }, "Editor"),
    editor.lastChanged ? h("p", { class: "meta" }, `Last changed ${editor.lastChanged}`) : null,
    editor.todayAvg ? h("p", { class: "meta" }, editor.todayAvg) : null,
    h("div", { class: "prescribe__fields" },
      field("Sets", setsStepper), field("Reps", repsStepper), field("Rest between sets", restStepper),
      field("Tempo", tempoStepper),
      hasKnee ? field("Knee bend target", kneeStepper) : null),
    h("div", { class: "prescribe__reference-rep" },
      h("span", { class: "chip chip--stretch" }, "Stretch"),
      h("button", { class: "btn btn--secondary", type: "button", disabled: true,
        title: "Coming later" }, "Record reference rep")));

  return { el, setsStepper, repsStepper, restStepper, tempoStepper, kneeStepper };
}

// ---- "When should Mira flag a rep?" card -----------------------------------------------------------------

export function renderFlagsCard(flags, { onChange }) {
  const weightShift = slider({ label: "Weight shift", min: 0, max: 50, value: flags.weightShiftPct,
    format: PCT_TEXT, onChange: (v) => onChange({ weightShiftPct: v }) });
  const kneeCave = slider({ label: "Knee cave", min: 0, max: 45, value: flags.kneeCaveDeg,
    format: DEG_TEXT, onChange: (v) => onChange({ kneeCaveDeg: v }) });
  const trunkLean = slider({ label: "Trunk lean", min: 0, max: 45, value: flags.trunkLeanDeg,
    format: DEG_TEXT, onChange: (v) => onChange({ trunkLeanDeg: v }) });
  const el = h("section", { class: "card prescribe__card", "aria-label": "When should Mira flag a rep?" },
    h("h2", { class: "card__title" }, "When should Mira flag a rep?"),
    weightShift.el, kneeCave.el, trunkLean.el);
  return { el, weightShift, kneeCave, trunkLean };
}

// ---- Heart rate limit card --------------------------------------------------------------------------------

export function renderHrLimitCard(hrLimit, { onChange }) {
  const hr = slider({ label: "Heart rate limit", min: 40, max: 220, value: hrLimit,
    format: (v) => ({ text: `${v} bpm`, valueText: `${v} beats per minute` }), onChange: (v) => onChange(v) });
  const el = h("section", { class: "card prescribe__card", "aria-label": "Heart rate limit" },
    h("h2", { class: "card__title" }, "Heart rate limit"),
    hr.el);
  return { el, hr };
}

// ---- Emergency contact card (read-only) --------------------------------------------------------------------

export function renderEmergencyCard(emergency) {
  return h("section", { class: "card prescribe__card", "aria-label": "Emergency contact" },
    h("h2", { class: "card__title" }, "Emergency contact"),
    h("dl", { class: "prescribe__emergency" },
      h("dt", {}, "Name"), h("dd", {}, emergency.name || "—"),
      h("dt", {}, "Relationship"), h("dd", {}, emergency.relation || "—"),
      h("dt", {}, "Phone"), h("dd", {}, emergency.phone || "—")));
}

// ---- Mira Assistant proposals rail --------------------------------------------------------------------------

export function renderProposalsRail() {
  const listHost = h("div", { class: "prescribe__proposals-list" });
  const el = h("section", { class: "card prescribe__rail", "aria-labelledby": "prescribe-proposals-title" },
    h("h2", { class: "card__title", id: "prescribe-proposals-title" }, "Mira Assistant proposals"),
    listHost);
  return { el, listHost };
}

// ---- Save / discard bar -------------------------------------------------------------------------------------

export function renderSaveBar({ onSave, onDiscard }) {
  const saveBtn = h("button", { class: "btn btn--primary", type: "button", disabled: true, onclick: onSave }, "Save plan");
  const discardBtn = h("button", { class: "btn btn--secondary", type: "button", disabled: true, onclick: onDiscard }, "Discard changes");
  const el = h("div", { class: "prescribe__save-bar" }, discardBtn, saveBtn);
  return { el, saveBtn, discardBtn, setDirty: (dirty) => {
    saveBtn.toggleAttribute("disabled", !dirty);
    discardBtn.toggleAttribute("disabled", !dirty);
  } };
}

// Unsaved-changes guard: an in-page bar (never window.confirm), shown over the main column when the
// therapist tries to navigate away with unsaved changes.
export function renderUnsavedBar({ onDiscardAndLeave, onStay }) {
  const el = h("div", { class: "prescribe__unsaved-bar", role: "alertdialog", "aria-label": "Unsaved changes" },
    h("p", {}, "You have unsaved changes to this plan."),
    h("div", { class: "prescribe__unsaved-actions" },
      h("button", { class: "btn btn--ghost", type: "button", onclick: onStay }, "Stay on this page"),
      h("button", { class: "btn btn--secondary", type: "button", onclick: onDiscardAndLeave }, "Discard and leave")));
  return el;
}
