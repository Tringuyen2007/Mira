import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { caseloadModel } = await import("../js/screens/caseload/model.js");
const { render } = await import("../js/screens/caseload/view.js");
const { readFileSync } = await import("node:fs");

const figma = JSON.parse(
  readFileSync(new URL("./fixtures/caseload-figma.json", import.meta.url), "utf8"));
const now = new Date("2026-10-11T15:00:00");

function noCaps() {
  return { has: () => false };
}

function handlers(extra = {}) {
  return {
    search: "", caps: noCaps(),
    onTabClick: () => {}, onSearchInput: () => {}, onShowMoreOnTrack: () => {},
    onRowClick: () => {}, onAddPatient: () => {},
    ...extra,
  };
}

test("rows render as real links with text content, not markup", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  const root = render(model, handlers());
  const rows = root.querySelectorAll(".row");
  assert.ok(rows.length > 0);
  for (const r of rows) {
    assert.equal(r.tagName, "A");
    assert.match(r.getAttribute("href"), /^#\/patients\//);
    assert.ok(r.textContent.trim().length > 0);
  }
  const maya = rows.find((r) => r.getAttribute("data-row-id") === "maya");
  assert.match(maya.textContent, /Maya Ruiz, 67/);
  assert.match(maya.textContent, /Right knee replacement/);
});

test("tabs carry role=tab and aria-selected, exactly one active", () => {
  const model = caseloadModel(figma, { proposals: [] }, { filter: "watch", now });
  const root = render(model, handlers());
  const tabs = root.querySelectorAll('[role="tab"]');
  assert.equal(tabs.length, 4);
  for (const t of tabs) assert.ok(["true", "false"].includes(t.getAttribute("aria-selected")));
  const selected = tabs.filter((t) => t.getAttribute("aria-selected") === "true");
  assert.equal(selected.length, 1);
  assert.match(selected[0].textContent, /Watch/);
});

test("the table has role=table and column headers", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  const root = render(model, handlers());
  const table = root.querySelector('[role="table"]');
  assert.ok(table);
  const headers = table.querySelectorAll('[role="columnheader"]').map((e) => e.textContent);
  assert.deepEqual(headers.slice(0, 3), ["Patient", "Why now", "Evidence"]);
});

test("server text (names) lands verbatim as element text, not as markup", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  const root = render(model, handlers());
  const mayaRow = root.querySelectorAll(".row").find((r) => r.getAttribute("data-row-id") === "maya");
  assert.equal(mayaRow.querySelector(".row__name").textContent, "Maya Ruiz, 67");
  assert.equal(mayaRow.querySelector(".row__sub").textContent, "Right knee replacement · wk 3");
});

test("status icon pairs an icon with a visually-hidden text label, never colour alone", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  const root = render(model, handlers());
  const mayaRow = root.querySelectorAll(".row").find((r) => r.getAttribute("data-row-id") === "maya");
  const label = mayaRow.querySelector(".sr-only");
  assert.equal(label.textContent, "Needs you");
});

test("Add patient button is hidden without the pairing-code capability, shown with it", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  const withoutCap = render(model, handlers());
  assert.equal([...withoutCap.querySelectorAll("button")].some((b) => b.textContent === "Add patient"), false);

  const caps = { has: (p) => p === "/patients/{patient_id}/pairing-code" };
  const withCap = render(model, handlers({ caps }));
  assert.equal([...withCap.querySelectorAll("button")].some((b) => b.textContent === "Add patient"), true);
});

test("Show N more on track triggers the handler", () => {
  const model = caseloadModel(figma, { proposals: [] }, { now });
  let called = false;
  const root = render(model, handlers({ onShowMoreOnTrack: () => { called = true; } }));
  const more = root.querySelectorAll(".table__more").find((b) => /Show 3 more on track/.test(b.textContent));
  assert.ok(more);
  more.dispatch("click");
  assert.equal(called, true);
});

test("copilot card is absent when proposals failed to load", () => {
  const model = caseloadModel(figma, null, { now });
  const root = render(model, handlers());
  assert.doesNotMatch(root.textContent, /Care Copilot/);
});
