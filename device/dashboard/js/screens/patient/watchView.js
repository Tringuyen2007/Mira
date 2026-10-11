// DOM builders for Patient review's "Apple Watch" card group (watchModel.js view-models). Same rules as view.js:
// h()/svg() only, no inline styles (bar sizes are SVG presentation attributes, colours are CSS classes), status is
// never colour alone.
import { h, svg } from "../../core/dom.js";
import { icon } from "../../ui/icons.js";
import { lineChart } from "../../ui/lineChart.js";
import { empty } from "../../ui/states.js";
import { TREMOR_LEVELS } from "./watchModel.js";

const LEVEL_LABEL = { none: "None", slight: "Slight", mild: "Mild", strong: "Strong" };

// The group's frame: heading, the "Tremor monitoring" switch (role="switch", like the sidebar's projector switch)
// and a body host index.js fills. `canToggle` false (no PATCH route on this server) hides the switch.
export function renderWatchGroup({ canToggle, onToggle }) {
  const toggleBtn = canToggle ? h("button", {
    class: "watch__toggle sidebar__theme-toggle", type: "button", role: "switch", "aria-checked": "false",
    onclick: () => onToggle?.(),
  },
    h("span", { class: "watch__toggle-label" }, "Tremor monitoring"),
    h("span", { class: "sidebar__theme-toggle-track" }, h("span", { class: "sidebar__theme-toggle-thumb" })))
    : null;
  const toggleStatusEl = h("p", { class: "meta watch__toggle-status", role: "status" });
  const bodyHost = h("div", { class: "watch__body" });
  const el = h("section", { class: "watch", "aria-labelledby": "watch-group-title" },
    h("div", { class: "watch__head" },
      h("h2", { class: "watch__title", id: "watch-group-title" }, "Apple Watch"),
      h("div", { class: "watch__head-actions" }, toggleBtn, toggleStatusEl)),
    bodyHost);
  return {
    el, bodyHost, toggleBtn, toggleStatusEl,
    setMonitoring(on) { toggleBtn?.setAttribute("aria-checked", on ? "true" : "false"); },
    setBusy(busy) { toggleBtn?.toggleAttribute("disabled", Boolean(busy)); },
  };
}

export function renderWatchEmpty() {
  return h("div", { class: "card watch__empty" },
    empty("No Apple Watch data yet",
      "Heart-rate recovery, turn range and tremor show here once the patient trains with their watch."));
}

// ---- Heart-rate recovery ------------------------------------------------------------------------------------

export function renderHrRecoveryCard(m) {
  const body = m.empty
    ? [h("p", { class: "meta" }, "No heart-rate recovery yet. It's measured 60 s after each set.")]
    : [
      h("p", { class: "watch__big" }, m.summary),
      m.latest ? h("p", { class: "meta" }, m.latest) : null,
      h("div", { class: "watch__chart" }, lineChart({
        title: `Heart-rate drop 1 min after each set, last ${m.count} ${m.count === 1 ? "set" : "sets"} (bpm)`,
        series: [{ name: "Drop (bpm)", points: m.points }],
      }, { width: 360, height: 200 })),
    ];
  return h("section", { class: "card watch__card", "aria-label": "Heart-rate recovery" },
    h("h3", { class: "card__title watch__card-title" }, icon("heart"), "Heart-rate recovery"), body);
}

// ---- Rotation range -------------------------------------------------------------------------------------------

export function renderRotationCard(m) {
  const body = m.empty
    ? h("p", { class: "meta" }, "No watch exercises done yet.")
    : h("ul", { class: "watch__rotation-list" }, m.exercises.map((ex) => h("li", { class: "watch__rotation-row" },
      h("div", {},
        h("p", { class: "watch__rotation-name" }, ex.name),
        h("p", { class: "meta" }, `${ex.line} · ${ex.sets} ${ex.sets === 1 ? "set" : "sets"}`)),
      h("p", { class: `watch__goal ${ex.reached ? "watch__goal--reached" : "watch__goal--short"}` },
        icon(ex.reached ? "check-circle" : "diamond"), h("span", {}, ex.status)))));
  return h("section", { class: "card watch__card", "aria-label": "Rotation range" },
    h("h3", { class: "card__title" }, "Rotation range"),
    h("p", { class: "meta" }, "Recent sets, measured by the watch"),
    body);
}

// ---- Tremor (14 days) ------------------------------------------------------------------------------------------

const BAR_W = 18;
const BAR_GAP = 8;
const CHART_H = 120;

// Stacked bars, one per day, none -> strong bottom to top. The SVG is decorative to assistive tech (aria-hidden);
// the same numbers are in the visually-hidden list below it, and the legend names each level in text.
function tremorBars(m) {
  const width = m.days.length * (BAR_W + BAR_GAP);
  const bars = m.days.map((d, i) => {
    let y = CHART_H;
    const x = i * (BAR_W + BAR_GAP);
    const rects = TREMOR_LEVELS.map((k) => {
      const hgt = Math.round((d.minutes[k] / m.maxTotal) * CHART_H * 10) / 10;
      y -= hgt;
      return hgt > 0 ? svg("rect", { class: `watch__tremor-seg watch__tremor-seg--${k}`, x, y: Math.round(y * 10) / 10,
        width: BAR_W, height: hgt }) : null;
    });
    return svg("g", {}, rects);
  });
  return svg("svg", { class: "watch__tremor-chart", viewBox: `0 0 ${Math.max(width, 1)} ${CHART_H}`,
    preserveAspectRatio: "xMinYMax meet", "aria-hidden": "true" }, bars);
}

export function renderTremorCard(m) {
  let body;
  if (m.empty) {
    body = h("p", { class: "meta" }, m.emptyText);
  } else {
    body = h("div", {},
      h("p", { class: "watch__big" }, m.latest),
      h("div", { class: "watch__tremor-wrap" }, tremorBars(m),
        h("div", { class: "watch__tremor-days meta", "aria-hidden": "true" },
          h("span", {}, m.days[0].label), m.days.length > 1 ? h("span", {}, m.days[m.days.length - 1].label) : null)),
      h("ul", { class: "watch__legend meta" }, TREMOR_LEVELS.map((k) => h("li", {},
        h("span", { class: `watch__swatch watch__tremor-seg--${k}`, "aria-hidden": "true" }), LEVEL_LABEL[k]))),
      h("ul", { class: "sr-only" }, m.days.map((d) => h("li", {}, d.text))));
  }
  return h("section", { class: "card watch__card", "aria-label": "Tremor, last 14 days" },
    h("div", { class: "watch__card-head" },
      h("h3", { class: "card__title" }, "Tremor (14 days)"),
      m.simulated ? h("span", { class: "chip chip--source watch__simulated" }, "Simulated") : null),
    body);
}
