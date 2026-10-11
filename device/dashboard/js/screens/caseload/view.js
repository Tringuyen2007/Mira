// Caseload view: pure render(model, ctx) -> HTMLElement. Figma 205:205 (table), 227:794/227:1023/227:1200
// (right rail). No state lives here; index.js owns filter/search/showAllOnTrack and re-renders on change.
import { h } from "../../core/dom.js";
import { href } from "../../core/router.js";
import { icon } from "../../ui/icons.js";
import { statusIcon } from "../../ui/statusIcon.js";
import { aiDraftChip } from "../../ui/aiDraftChip.js";
import { empty } from "../../ui/states.js";

function pageHead(model, { search, caps, onSearchInput, onAddPatient }) {
  const canAddPatient = caps && caps.has && caps.has("/patients/{patient_id}/pairing-code");
  return h("div", { class: "page-head caseload__head" },
    h("div", {},
      h("p", { class: "meta caseload__date" }, model.dateLabel),
      h("h1", { class: "page-title" }, "Caseload"),
      h("p", { class: "page-sub" }, model.subtitle)),
    h("div", { class: "caseload__head-actions" },
      h("label", { class: "search-field" },
        h("span", { class: "sr-only" }, "Search patients"),
        icon("search", { label: "" }),
        h("input", {
          type: "search", placeholder: "Search patients", value: search || "",
          oninput: (e) => onSearchInput(e.target.value),
        })),
      canAddPatient
        ? h("button", { class: "btn btn--primary", type: "button", onclick: onAddPatient }, "Add patient")
        : null));
}

function pillTabs(model, { onTabClick }) {
  return h("div", { class: "pill-tabs", role: "tablist", "aria-label": "Filter by status" },
    model.tabs.map((t) => h("button", {
      type: "button", role: "tab", "aria-selected": t.active ? "true" : "false",
      onclick: () => onTabClick(t.key),
    }, `${t.label} `, h("span", { class: "pill-tabs__count" }, String(t.count)))));
}

function row(r, { onRowClick }) {
  return h("a", { class: "row", href: r.href, "data-row-id": r.id, onclick: () => onRowClick?.(r.id) },
    h("span", { class: "row__cell row__cell--patient" },
      statusIcon(r.statusIcon),
      h("span", {},
        h("span", { class: "row__name" }, `${r.name}, ${r.age}`),
        h("span", { class: "row__sub" }, r.sub))),
    h("span", { class: "row__cell row__cell--why" },
      h("span", { class: "row__reason" }, r.reason),
      h("span", { class: "row__when meta" }, r.when)),
    h("span", { class: `row__cell row__cell--evidence row__cell--evidence-${r.evidenceTone}` }, r.evidence),
    icon("chevron", { label: "" }));
}

function groupRows(group, model, { onShowMoreOnTrack, onRowClick }) {
  const rows = [h("div", { class: "table__group-head", role: "row" },
    h("span", { role: "columnheader" }, group.label),
    h("span", { class: "meta" }, String(group.count)))];
  if (group.rows.length === 0) {
    rows.push(h("div", { class: "table__empty" }, empty("Nobody here right now", "")));
  } else {
    for (const r of group.rows) rows.push(row(r, { onRowClick }));
  }
  if (group.key === "on_track" && model.moreOnTrack > 0) {
    rows.push(h("button", { class: "table__more", type: "button", onclick: onShowMoreOnTrack },
      `Show ${model.moreOnTrack} more on track`));
  }
  return rows;
}

function tableCard(model, handlers) {
  if (model.groups.every((g) => g.rows.length === 0)) {
    return h("div", { class: "card table-card" },
      empty("Nobody here right now", "No patients match this filter."));
  }
  return h("div", { class: "card table-card" },
    h("div", { class: "table", role: "table", "aria-label": "Patients" },
      h("div", { class: "table__head", role: "row" },
        h("span", { role: "columnheader" }, "Patient"),
        h("span", { role: "columnheader" }, "Why now"),
        h("span", { role: "columnheader" }, "Evidence")),
      model.groups.flatMap((g) => groupRows(g, model, handlers))));
}

function todayCard(model) {
  return h("div", { class: "card rail-card" },
    h("h2", { class: "card__title" }, "Today"),
    h("div", { class: "rail-stat" }, h("span", {}, "Sessions done"), h("span", { class: "rail-stat__value" }, String(model.today.done))),
    h("div", { class: "rail-stat" }, h("span", {}, "Live right now"), h("span", { class: "rail-stat__value" }, String(model.today.live))));
}

function copilotCard(model) {
  if (!model.copilot) return null;
  return h("div", { class: "card rail-card" },
    h("div", { class: "rail-card__head" },
      h("h2", { class: "card__title" }, "Care Copilot"),
      aiDraftChip()),
    h("p", { class: "meta" }, `${model.copilot.count} proposals are waiting for your review.`),
    h("ul", { class: "copilot-list" },
      model.copilot.items.map((item) => h("li", { class: "copilot-list__item" }, item.text))),
    h("a", { class: "btn btn--secondary", href: href("copilot") }, "Review proposals"));
}

function liveCard(model) {
  if (!model.live) return null;
  return h("div", { class: "card rail-card" },
    h("h2", { class: "card__title" },
      h("span", { class: "live-dot" }), " Live now"),
    h("p", { class: "row__name" }, model.live.name),
    h("p", { class: "meta" }, model.live.line),
    h("a", { class: "btn btn--secondary", href: model.live.href }, "Watch live twin"));
}

export function render(model, handlers) {
  const { search, caps } = handlers;
  return h("div", { class: "caseload" },
    pageHead(model, { search, caps, onSearchInput: handlers.onSearchInput, onAddPatient: handlers.onAddPatient }),
    pillTabs(model, handlers),
    h("div", { class: "grid-main-rail caseload__body" },
      tableCard(model, handlers),
      h("div", { class: "caseload__rail" },
        todayCard(model),
        copilotCard(model),
        liveCard(model))));
}
