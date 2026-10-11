// DOM builders for the Prescribe screen (Figma 207:282). Each render* function takes a plain view-model and
// returns either a plain element or {el, ...refs} when the host needs to update it in place later (steppers
// and sliders keep their own element alive — see js/ui/stepper.js / slider.js — so dragging one never gets
// interrupted by a re-render).
import { h } from "../../core/dom.js";
import { icon } from "../../ui/icons.js";
import { stepper } from "../../ui/stepper.js";
import { slider } from "../../ui/slider.js";

// Head (Figma): meta line, title, "Changes reach Mira…" subtitle, and Discard / Save at the top right.
export function renderHead({ onSave, onDiscard }) {
  const metaEl = h("p", { class: "meta prescribe__meta" });
  const titleEl = h("h1", { class: "page-title" }, "Prescribe");
  const subtitleEl = h("p", { class: "prescribe__subtitle" });
  const saveBtn = h("button", { class: "btn btn--primary", type: "button", disabled: true, onclick: onSave },
    "Save plan");
  const discardBtn = h("button", { class: "btn btn--secondary", type: "button", disabled: true, onclick: onDiscard },
    "Discard changes");
  const el = h("div", { class: "page-head prescribe__head" },
    h("div", {}, metaEl, titleEl, subtitleEl),
    h("div", { class: "prescribe__save-bar" }, discardBtn, saveBtn));
  return { el, metaEl, titleEl, subtitleEl, saveBtn, discardBtn, setDirty: (dirty) => {
    saveBtn.toggleAttribute("disabled", !dirty);
    discardBtn.toggleAttribute("disabled", !dirty);
  } };
}

// Small "Watch" tag on watch exercises (counted by the Apple Watch, not the cameras); text, not colour alone.
function watchTag() {
  return h("span", { class: "chip prescribe__watch-tag" }, "Watch");
}

// ---- Exercise plan card -----------------------------------------------------------------------------------

// Each row is one <button> (aria-pressed = selected for editing). The tick is a drawn box, not an <input>, so
// there's no interactive control nested inside the button; every listed exercise is in the active plan.
export function renderExerciseList(exercises, { onSelect }) {
  if (exercises.length === 0) {
    return h("p", { class: "meta" }, "No active exercises yet.");
  }
  return h("ul", { class: "prescribe__exercise-list" }, exercises.map((ex) => h("li", {},
    h("button", {
      class: `prescribe__exercise-row${ex.selected ? " is-selected" : ""}`, type: "button",
      "aria-pressed": ex.selected ? "true" : "false", onclick: () => onSelect(ex.id),
    },
      h("span", { class: "prescribe__check", "aria-hidden": "true" }),
      h("span", { class: "prescribe__exercise-name" }, ex.name,
        ex.watch ? [" ", watchTag()] : null,
        ex.staged ? h("span", { class: "prescribe__staged meta" }, " · new, not saved yet") : null),
      h("span", { class: "prescribe__exercise-dose" }, ex.dose)))));
}

// `watchChoices` ([{id, name}], may be empty) + `onAddWatch(id)`: the "Add a watch exercise" quick picks, shown
// when the server has the Apple Watch routes; a pick only stages the exercise, like a library pick.
function renderWatchChoices(choices, onAddWatch) {
  if (!choices || choices.length === 0) return null;
  return h("div", { class: "prescribe__watch-add" },
    h("p", { class: "meta" }, "Add a watch exercise"),
    h("ul", { class: "prescribe__search-list" }, choices.map((x) => h("li", {},
      h("button", { class: "btn btn--ghost", type: "button", onclick: () => onAddWatch(x.id) },
        `Add ${x.name} to the plan`, " ", watchTag())))));
}

export function renderExerciseCard(exercises, { onSelect, onSearch, canAddFromLibrary, summary, watchChoices,
  onAddWatch }) {
  const listHost = h("div", {}, renderExerciseList(exercises, { onSelect }));
  const searchInput = canAddFromLibrary
    ? h("input", { type: "search", class: "prescribe__search", placeholder: "Add from exercise library",
        "aria-label": "Add from exercise library", oninput: (e) => onSearch(e.target.value) })
    : null;
  const resultsHost = h("div", { class: "prescribe__search-results" });
  const el = h("section", { class: "card prescribe__card", "aria-label": "Exercise plan" },
    h("h2", { class: "card__title" }, "Exercise plan"),
    summary ? h("p", { class: "meta" }, summary) : null,
    listHost,
    canAddFromLibrary ? h("div", { class: "prescribe__search-wrap" },
      h("span", { class: "prescribe__search-icon" }, icon("search")), searchInput, resultsHost) : null,
    renderWatchChoices(watchChoices, onAddWatch));
  return { el, listHost, searchInput, resultsHost,
    setExercises: (list) => listHost.replaceChildren(renderExerciseList(list, { onSelect })) };
}

// Library search results: clicking one only stages it in the plan (Save plan writes it).
export function renderSearchResults(items, { onAdd }) {
  if (items.length === 0) return h("p", { class: "meta" }, "No matching exercises.");
  return h("ul", { class: "prescribe__search-list" }, items.map((x) => h("li", {},
    h("button", { class: "btn btn--ghost", type: "button", onclick: () => onAdd(x.id) },
      `Add ${x.name} to the plan`, x.watch ? [" ", watchTag()] : null))));
}

// ---- Editor card ------------------------------------------------------------------------------------------

const PCT_TEXT = (v) => ({ text: `${v}%`, valueText: `${v} percent` });
const DEG_TEXT = (v) => ({ text: `${v}°`, valueText: `${v} degrees` });

export function renderEditorCard(editor, limits, { onChange, title }) {
  if (!editor.exerciseId) {
    return { el: h("section", { class: "card prescribe__card" },
      h("h2", { class: "card__title" }, "Exercise"),
      h("p", { class: "meta" }, "Select an exercise to edit it.")) };
  }
  const minReps = limits.min_reps ?? 1, maxReps = limits.max_reps ?? 100;
  const minRest = limits.min_rest_s ?? 0, maxRest = limits.max_rest_s ?? 600;

  const setsStepper = stepper({ label: "sets", min: 1, max: 10, value: editor.sets ?? 1,
    onChange: (v) => onChange({ sets: v }) });
  const repsStepper = stepper({ label: "reps", min: minReps, max: maxReps, value: editor.reps ?? minReps,
    onChange: (v) => onChange({ reps: v }) });
  const restStepper = stepper({ label: "rest between sets", min: minRest, max: maxRest, step: 15,
    value: editor.restS, format: (v) => `${v} s`, onChange: (v) => onChange({ restS: v }) });
  const hasKnee = editor.kneeTarget != null;
  const kneeSlider = hasKnee ? slider({ label: "Knee bend target", min: 0, max: 150, value: editor.kneeTarget,
    format: DEG_TEXT, onChange: (v) => onChange({ kneeTarget: v }) }) : null;
  const rotationSlider = editor.watch ? slider({ label: "Turn goal (from the watch)", min: 10, max: 180,
    value: editor.rotationGoal, format: DEG_TEXT, onChange: (v) => onChange({ rotationGoal: v }) }) : null;
  const tempoSlider = slider({ label: "Speed: seconds down and up", min: 1, max: 10, value: editor.tempoS,
    format: (v) => ({ text: `${v} s each`, valueText: `${v} seconds each way` }),
    onChange: (v) => onChange({ tempoS: v }) });

  function field(label, control) {
    return h("div", { class: "prescribe__field" }, h("span", { class: "prescribe__field-label" }, label), control.el);
  }

  const el = h("section", { class: "card prescribe__card", "aria-label": `${title} settings` },
    h("div", { class: "prescribe__card-head" },
      h("h2", { class: "card__title" }, title, editor.watch ? [" ", watchTag()] : null),
      editor.lastChanged ? h("p", { class: "meta" }, `Last changed ${editor.lastChanged}`) : null),
    h("div", { class: "prescribe__fields" },
      field("Sets", setsStepper), field("Reps", repsStepper), field("Rest", restStepper)),
    kneeSlider ? kneeSlider.el : null,
    rotationSlider ? rotationSlider.el : null,
    editor.todayAvg ? h("p", { class: "meta prescribe__today" }, editor.todayAvg) : null,
    tempoSlider.el);

  return { el, setsStepper, repsStepper, restStepper, kneeSlider, rotationSlider, tempoSlider };
}

// ---- Reference rep card (right column) ------------------------------------------------------------------------

export function renderReferenceRepCard({ exerciseLabel, firstName }) {
  return h("section", { class: "card prescribe__card", "aria-label": "Reference rep" },
    h("div", { class: "prescribe__card-head" },
      h("h2", { class: "card__title" }, "Reference rep"),
      h("span", { class: "chip chip--stretch" }, "Stretch")),
    h("p", { class: "meta" },
      `Do one ${exerciseLabel} in front of Mira. It plays as a see-through ghost that ${firstName} follows.`),
    h("button", { class: "btn btn--secondary prescribe__reference-btn", type: "button", disabled: true,
      title: "Coming later" }, "Record reference rep"));
}

// ---- "When should Mira flag a rep?" card -----------------------------------------------------------------

export function renderFlagsCard(flags, { onChange, firstName }) {
  const weightShift = slider({ label: "Flag weight shift above", min: 0, max: 50, value: flags.weightShiftPct,
    format: PCT_TEXT, onChange: (v) => onChange({ weightShiftPct: v }) });
  const kneeCave = slider({ label: "Flag knee drifting inward above", min: 0, max: 45, value: flags.kneeCaveDeg,
    format: DEG_TEXT, onChange: (v) => onChange({ kneeCaveDeg: v }) });
  const trunkLean = slider({ label: "Flag trunk leaning forward above", min: 0, max: 45, value: flags.trunkLeanDeg,
    format: DEG_TEXT, onChange: (v) => onChange({ trunkLeanDeg: v }) });
  const el = h("section", { class: "card prescribe__card", "aria-label": "When should Mira flag a rep?" },
    h("h2", { class: "card__title" }, "When should Mira flag a rep?"),
    h("p", { class: "meta" },
      `Just for ${firstName}. Flags show red on your timeline and Mira coaches ${firstName} in the moment.`),
    weightShift.el, kneeCave.el, trunkLean.el);
  return { el, weightShift, kneeCave, trunkLean };
}

// ---- Heart rate limit card --------------------------------------------------------------------------------

// `sentText`: "Sent to Maya's watch" once the server's limits_changed push for this patient arrives after a save
// (index.js also sets statusEl directly when the event lands while the card is up).
export function renderHrLimitCard(hrLimit, { onChange, firstName, sentText = null }) {
  const hr = slider({ label: "Pause and check in above", min: 40, max: 220, value: hrLimit,
    format: (v) => ({ text: `${v} bpm`, valueText: `${v} beats per minute` }), onChange: (v) => onChange(v) });
  hr.el.querySelector("input")?.setAttribute("aria-label", "Heart rate limit: pause and check in above");
  const statusEl = h("p", { class: "prescribe__limit-sent", role: "status" });
  const el = h("section", { class: "card prescribe__card", "aria-label": "Heart rate limit" },
    h("h2", { class: "card__title prescribe__title-icon" }, icon("heart"), "Heart rate limit"),
    hr.el,
    h("p", { class: "meta" }, `From ${firstName}'s Apple Watch. Mira asks ${firstName} to rest and tells you.`),
    statusEl);
  setLimitSent(statusEl, sentText);
  return { el, hr, statusEl };
}

export function setLimitSent(statusEl, text) {
  if (!text) { statusEl.replaceChildren(); return; }
  statusEl.replaceChildren(icon("check-circle"), h("span", {}, text));
}

// ---- Emergency contact card (read-only) --------------------------------------------------------------------

export function renderEmergencyCard(emergency, { firstName }) {
  const contactFirst = String(emergency.name || "").trim().split(/\s+/)[0];
  return h("section", { class: "card prescribe__card", "aria-label": "Emergency contact" },
    h("h2", { class: "card__title" }, "Emergency contact"),
    emergency.name
      ? h("div", { class: "prescribe__emergency" },
        h("p", { class: "prescribe__emergency-name" }, emergency.name),
        emergency.relation ? h("p", { class: "meta" }, emergency.relation) : null,
        emergency.phone ? h("p", { class: "prescribe__emergency-phone" }, emergency.phone) : null,
        h("p", { class: "meta" },
          `Mira calls ${contactFirst} if she sees a fall and ${firstName} does not answer within 30 seconds.`))
      : h("p", { class: "meta" }, "No emergency contact on file."));
}

// ---- Mira Assistant proposals rail --------------------------------------------------------------------------

export function renderProposalsRail() {
  const listHost = h("div", { class: "prescribe__proposals-list" });
  const el = h("section", { class: "card prescribe__rail", "aria-labelledby": "prescribe-proposals-title" },
    h("h2", { class: "card__title", id: "prescribe-proposals-title" }, "Mira Assistant proposals"),
    listHost);
  return { el, listHost };
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
