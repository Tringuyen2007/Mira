// Caseload view: split so index.js can re-render the body (table + rail) and the tabs on every state change
// while leaving the page head — and its search `<input>` — untouched (I5/I6). Figma 205:205 (table),
// 227:794/227:1023/227:1200 (right rail).
//
// The table used to claim role="table" with data rows and group headers that weren't valid ARIA table
// children (review I7). Instead of patching the table semantics, each status group is a plain
// <section aria-labelledby> with a <ul> of <li><a>; the anchor carries one accessible name built from the
// row's visible text (I7 option 2), and the three-column layout is kept as a CSS grid with no table roles.
import { h } from "../../core/dom.js";
import { href } from "../../core/router.js";
import { icon } from "../../ui/icons.js";
import { statusIcon } from "../../ui/statusIcon.js";
import { aiDraftChip } from "../../ui/aiDraftChip.js";
import { empty } from "../../ui/states.js";

const STATUS_LABEL = { needs: "Needs you", watch: "Watch", ok: "On track" };

export function renderHead(model, { search, caps, onSearchInput, onAddPatient }) {
  const canAddPatient = caps && caps.has && caps.has("/patients/{patient_id}/pairing-code");
  const subtitleEl = h("p", { class: "page-sub" }, model.subtitle);
  const searchInput = h("input", {
    type: "search", placeholder: "Search patients", value: search || "",
    oninput: (e) => onSearchInput(e.target.value),
  });
  const el = h("div", { class: "page-head caseload__head" },
    h("div", {},
      h("p", { class: "meta caseload__date" }, model.dateLabel),
      h("h1", { class: "page-title" }, "Caseload"),
      subtitleEl),
    h("div", { class: "caseload__head-actions" },
      h("label", { class: "search-field" },
        h("span", { class: "sr-only" }, "Search patients"),
        icon("search", { label: "" }),
        searchInput),
      canAddPatient
        ? h("button", { class: "btn btn--primary", type: "button", onclick: onAddPatient }, "Add patient")
        : null));
  return { el, subtitleEl, searchInput };
}

export function renderTabs(model, { onTabClick }) {
  return h("div", { class: "pill-tabs", role: "tablist", "aria-label": "Filter by status" },
    model.tabs.map((t) => h("button", {
      type: "button", role: "tab", "aria-selected": t.active ? "true" : "false",
      onclick: () => onTabClick(t.key),
    }, `${t.label} `, h("span", { class: "pill-tabs__count" }, String(t.count)))));
}

function rowAccessibleName(r) {
  const who = r.age != null ? `${r.name}, ${r.age}` : r.name;
  const parts = [who, STATUS_LABEL[r.statusIcon] || "", r.reason, r.when];
  return parts.filter(Boolean).join(". ");
}

function row(r, { onRowClick }) {
  return h("li", {},
    h("a", {
      class: "row", href: r.href, "data-row-id": r.id, "aria-label": rowAccessibleName(r),
      onclick: () => onRowClick?.(r.id),
    },
      h("span", { class: "row__cell row__cell--patient", "aria-hidden": "true" },
        statusIcon(r.statusIcon),
        h("span", {},
          h("span", { class: "row__name" }, r.age != null ? `${r.name}, ${r.age}` : r.name),
          h("span", { class: "row__sub" }, r.sub))),
      h("span", { class: "row__cell row__cell--why", "aria-hidden": "true" },
        h("span", { class: "row__reason" }, r.reason),
        h("span", { class: "row__when meta" }, r.when)),
      h("span", {
        class: `row__cell row__cell--evidence row__cell--evidence-${r.evidenceTone}`, "aria-hidden": "true",
      }, r.evidence || "—"),
      icon("chevron", { label: "" })));
}

function groupSection(group, model, handlers) {
  const headingId = `caseload-group-${group.key}`;
  const items = group.rows.length === 0
    ? [h("li", { class: "table__empty-compact meta" }, "Nobody here right now")]
    : group.rows.map((r) => row(r, handlers));
  if (group.key === "on_track" && model.moreOnTrack > 0) {
    items.push(h("li", {},
      h("button", { class: "table__more", type: "button", onclick: handlers.onShowMoreOnTrack },
        `Show ${model.moreOnTrack} more on track`)));
  }
  return h("section", { class: "table__group", "aria-labelledby": headingId },
    h("div", { class: "table__group-head" },
      h("h2", { id: headingId, class: "table__group-title" }, group.label),
      h("span", { class: "meta" }, String(group.count))),
    h("ul", { class: "table-list" }, items));
}

export function renderBody(model, handlers) {
  return h("div", { class: "grid-main-rail caseload__body" },
    tableCard(model, handlers),
    h("div", { class: "caseload__rail" },
      todayCard(model),
      copilotCard(model),
      liveCard(model)));
}

function tableCard(model, handlers) {
  if (model.groups.every((g) => g.rows.length === 0)) {
    return h("div", { class: "card table-card" },
      empty("Nobody here right now", "No patients match this filter."));
  }
  return h("div", { class: "card table-card" },
    h("div", { class: "table__head", "aria-hidden": "true" },
      h("span", {}, "Patient"), h("span", {}, "Why now"), h("span", {}, "Evidence")),
    model.groups.map((g) => groupSection(g, model, handlers)));
}

function todayCard(model) {
  return h("div", { class: "card rail-card" },
    h("h2", { class: "card__title" }, "Today"),
    h("div", { class: "rail-stat" }, h("span", {}, "Sessions done"), h("span", { class: "rail-stat__value" }, String(model.today.done))),
    h("div", { class: "rail-stat" }, h("span", {}, "Live right now"), h("span", { class: "rail-stat__value" }, String(model.today.live))));
}

function copilotSummary(count) {
  if (count === 0) return "No proposals waiting.";
  if (count === 1) return "1 proposal is waiting for your review.";
  return `${count} proposals are waiting for your review.`;
}

function copilotCard(model) {
  if (!model.copilot) return null;
  const count = model.copilot.count;
  return h("div", { class: "card rail-card" },
    h("div", { class: "rail-card__head" },
      h("h2", { class: "card__title" }, "Care Copilot"),
      aiDraftChip()),
    h("p", { class: "meta" }, copilotSummary(count)),
    count > 0
      ? h("ul", { class: "copilot-list" }, model.copilot.items.map((item) => h("li", { class: "copilot-list__item" }, item.text)))
      : null,
    count > 0
      ? h("a", { class: "btn btn--secondary", href: href("copilot") }, "Review proposals")
      : h("a", { class: "link-quiet", href: href("copilot") }, "Open Care Copilot"));
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

// Whole-screen render, used by tests and as the first paint before index.js splits head/tabs/body apart.
export function render(model, handlers) {
  const { search, caps } = handlers;
  const { el: head } = renderHead(model, { search, caps, onSearchInput: handlers.onSearchInput, onAddPatient: handlers.onAddPatient });
  return h("div", { class: "caseload" },
    head,
    renderTabs(model, handlers),
    renderBody(model, handlers));
}
