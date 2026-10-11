// Recovery time-lapse view builders (Figma frame 208:339). Persistent regions, same pattern as the other
// screens: index.js updates text/attributes on the refs it gets back rather than rebuilding on every tick.
import { h } from "../../core/dom.js";
import { icon } from "../../ui/icons.js";
import { empty } from "../../ui/states.js";

export function renderHead({ onModeChange, onAddToNote }) {
  const metaEl = h("p", { class: "meta patient__meta" });
  const titleEl = h("h1", { class: "page-title" }, "Recovery time-lapse");
  const subtitleEl = h("p", { class: "meta" }, "Two dates, the same exercise, replayed in sync from stored 3D frames.");
  const sideBtn = h("button", { type: "button", role: "tab", "aria-selected": "true",
    onclick: () => onModeChange("side") }, "Side by side");
  const overlayBtn = h("button", { type: "button", role: "tab", "aria-selected": "false",
    onclick: () => onModeChange("overlay") }, "Overlay");
  const modeTabs = h("div", { class: "pill-tabs timelapse__mode", role: "tablist", "aria-label": "Side by side or overlay" },
    sideBtn, overlayBtn);
  const addNoteBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: onAddToNote },
    "Add to progress note");
  const el = h("div", { class: "page-head timelapse__head" },
    h("div", {}, metaEl, titleEl, subtitleEl),
    h("div", { class: "timelapse__head-actions" }, modeTabs, addNoteBtn));
  return { el, metaEl, titleEl, sideBtn, overlayBtn, addNoteBtn };
}

function pickerField(labelText, selectEl) {
  return h("div", { class: "timelapse__field" },
    h("label", { class: "meta" }, labelText), selectEl);
}

export function renderControls({ onDateA, onDateB, onExercise, onView }) {
  const dateASelect = h("select", { class: "timelapse__select", onchange: (e) => onDateA(e.target.value) });
  const dateBSelect = h("select", { class: "timelapse__select", onchange: (e) => onDateB(e.target.value) });
  const exerciseSelect = h("select", { class: "timelapse__select", onchange: (e) => onExercise(e.target.value) });
  const frontBtn = h("button", { type: "button", role: "tab", "aria-selected": "true",
    onclick: () => onView("front") }, "Front");
  const sideViewBtn = h("button", { type: "button", role: "tab", "aria-selected": "false",
    onclick: () => onView("side") }, "Side");
  const viewTabs = h("div", { class: "pill-tabs timelapse__view", role: "tablist", "aria-label": "Front or side view" },
    frontBtn, sideViewBtn);
  const el = h("div", { class: "timelapse__controls" },
    pickerField("First date", dateASelect),
    pickerField("Second date", dateBSelect),
    pickerField("Exercise", exerciseSelect),
    h("div", { class: "timelapse__field" }, h("label", { class: "meta" }, "View"), viewTabs));
  return { el, dateASelect, dateBSelect, exerciseSelect, frontBtn, sideViewBtn };
}

export function fillDateOptions(selectEl, sessions, selectedId) {
  selectEl.replaceChildren(...sessions.map((s) => h("option", {
    value: s.id, selected: s.id === selectedId ? true : null,
  }, s.label)));
}

export function fillExerciseOptions(selectEl, exercises, selectedId) {
  selectEl.replaceChildren(...exercises.map((ex) => h("option", {
    value: ex.id, selected: ex.id === selectedId ? true : null,
  }, ex.label)));
}

export function renderSideCard() {
  const labelEl = h("span", { class: "timelapse__card-label" });
  const dateEl = h("span", { class: "meta" });
  const avatarHost = h("div", { class: "timelapse__avatar-host" });
  const el = h("section", { class: "card timelapse__card" },
    h("div", { class: "timelapse__card-head" }, labelEl, dateEl), avatarHost);
  return { el, labelEl, dateEl, avatarHost };
}

export function renderOverlayCard() {
  const avatarHost = h("div", { class: "timelapse__avatar-host" });
  const earlierLabelEl = h("span", {});
  const laterLabelEl = h("span", {});
  const el = h("section", { class: "card timelapse__overlay-card" },
    h("h2", { class: "card__title" }, "Overlay"),
    h("p", { class: "meta" }, "Both dates on one avatar, earlier date see-through."),
    avatarHost,
    h("div", { class: "timelapse__legend" },
      h("p", {}, h("span", { class: "timelapse__swatch timelapse__swatch--ghost" }),
        earlierLabelEl, " (see-through)"),
      h("p", {}, h("span", { class: "timelapse__swatch timelapse__swatch--solid" }),
        laterLabelEl, " (solid)")));
  return { el, avatarHost, earlierLabelEl, laterLabelEl };
}

export function renderTransport({ onPlayPause, onSpeed }) {
  const playBtn = h("button", { class: "timelapse__play", type: "button", "aria-label": "Play", onclick: onPlayPause },
    icon("play", { label: "" }));
  const headlineEl = h("p", { class: "timelapse__headline" });
  const captionEl = h("p", { class: "meta" });
  const halfBtn = h("button", { type: "button", role: "tab", "aria-selected": "false",
    onclick: () => onSpeed(0.5) }, "0.5×");
  const oneBtn = h("button", { type: "button", role: "tab", "aria-selected": "true",
    onclick: () => onSpeed(1) }, "1×");
  const speedTabs = h("div", { class: "pill-tabs timelapse__speed", role: "tablist", "aria-label": "Playback speed" },
    halfBtn, oneBtn);
  const el = h("div", { class: "card timelapse__transport" },
    playBtn,
    h("div", { class: "timelapse__transport-text" }, headlineEl, captionEl),
    speedTabs);
  return { el, playBtn, headlineEl, captionEl, halfBtn, oneBtn };
}

export function noExerciseSessions() {
  return h("div", { class: "card" },
    empty("Not enough sessions yet", "This patient needs at least two sessions of the same exercise to compare."));
}
