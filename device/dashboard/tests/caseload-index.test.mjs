// Fakedom coverage for the caseload screen's mount behaviour (review I5/I6/I10): typing into search filters
// rows without replacing the input element (focus survives a redraw), and a tab click updates aria-selected
// without a second /caseload fetch.
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/caseload/index.js");

function makeSockets() {
  const subs = new Map();
  return {
    subscribe(channel, fn) {
      subs.set(channel, fn);
      return () => subs.delete(channel);
    },
    emit(channel, msg) { subs.get(channel)?.(msg); },
  };
}

function makeCtx(caseloadJson) {
  let callCount = 0;
  const store = { set() {}, get() { return {}; }, subscribe() { return () => {}; } };
  return {
    ctx: {
      api: {
        caseload: async () => { callCount++; return caseloadJson; },
        proposals: async () => ({ proposals: [] }),
      },
      sockets: makeSockets(),
      store,
      caps: { has: () => false },
      toast: () => {},
      navigate: () => {},
    },
    callCount: () => callCount,
  };
}

const CASELOAD_JSON = {
  therapist_id: "okafor",
  generated_at: "2026-10-11T12:00:00Z",
  today: { sessions_done: 1, live_now: null },
  counts: { all: 2, needs_you: 1, watch: 0, on_track: 1 },
  patients: [
    { id: "maya", name: "Maya Ellis", age: 67, condition: "right knee replacement", affected_side: "right",
      week: 3, simulated: true, bucket: "needs_you", severity: 3, kind: "compensation",
      reason: "Bending deeper, but taking weight off the right leg", evidence: "Right leg 47% → 36%",
      last_session_at: "2026-10-10T18:00:00Z", last_session_id: null, live_now: false },
    { id: "george", name: "George Alvarez", age: 71, condition: "left hip replacement", affected_side: "left",
      week: 1, simulated: true, bucket: "on_track", severity: 1, kind: "steady", reason: "Last session on Oct 10",
      evidence: "", last_session_at: "2026-10-10T06:00:00Z", last_session_id: null, live_now: false },
  ],
};

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function fakeRoot() {
  const el = document.createElement("div");
  el.focus = () => {};
  return el;
}

test("typing into search filters rows without replacing the input element", async () => {
  const root = fakeRoot();
  const { ctx } = makeCtx(CASELOAD_JSON);
  const unmount = screen.mount(root, ctx, { query: {} });
  await flush();

  const inputBefore = root.querySelector("input");
  assert.ok(inputBefore);
  assert.equal(root.querySelectorAll(".row").length, 2);

  inputBefore.value = "george";
  inputBefore.dispatch("input", { target: inputBefore });
  await new Promise((r) => setTimeout(r, 150));

  const inputAfter = root.querySelector("input");
  assert.equal(inputAfter, inputBefore); // same node: the head was never rebuilt (I5)
  assert.equal(root.querySelectorAll(".row").length, 1);
  assert.match(root.querySelector(".row").getAttribute("aria-label"), /George Alvarez/);

  unmount();
});

test("tab click updates aria-selected without a second /caseload fetch", async () => {
  const root = fakeRoot();
  const { ctx, callCount } = makeCtx(CASELOAD_JSON);
  const unmount = screen.mount(root, ctx, { query: {} });
  await flush();

  assert.equal(callCount(), 1);
  const tabs = root.querySelectorAll('[role="tab"]');
  const onTrackTab = tabs.find((t) => /On track/.test(t.textContent));
  assert.equal(onTrackTab.getAttribute("aria-selected"), "false");

  onTrackTab.dispatch("click");
  await flush();

  assert.equal(callCount(), 1); // no refetch just for a filter change
  const tabsAfter = root.querySelectorAll('[role="tab"]');
  const onTrackTabAfter = tabsAfter.find((t) => /On track/.test(t.textContent));
  assert.equal(onTrackTabAfter.getAttribute("aria-selected"), "true");
  assert.equal(root.querySelectorAll(".row").length, 1);
  assert.match(root.querySelector(".row").getAttribute("aria-label"), /George Alvarez/);

  unmount();
});

test("a background refresh keeps rows on screen instead of flashing a loading state", async () => {
  const root = fakeRoot();
  const { ctx } = makeCtx(CASELOAD_JSON);
  const unmount = screen.mount(root, ctx, { query: {} });
  await flush();
  assert.equal(root.querySelectorAll(".row").length, 2);

  ctx.sockets.emit("events", { type: "set_done" });
  // Debounced refresh is scheduled, but must not blank the table before it resolves.
  assert.equal(root.querySelectorAll(".row").length, 2);
  assert.doesNotMatch(root.textContent, /Loading caseload/);

  unmount();
});

test("unmount closes the add-patient dialog so nothing lingers after sign-out", async () => {
  const root = fakeRoot();
  const { ctx } = makeCtx(CASELOAD_JSON);
  ctx.caps = { has: () => true };
  // showModal()/close() aren't implemented by the fake <dialog>; stub them so the dialog path is exercised.
  const realCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    const el = realCreateElement(tag);
    if (tag === "dialog") {
      el.open = false;
      el.showModal = () => { el.open = true; };
      el.close = () => { if (!el.open) return; el.open = false; el.dispatch("close"); };
    }
    return el;
  };
  document.body = document.createElement("body");
  try {
    const unmount = screen.mount(root, ctx, { query: {} });
    await flush();
    const addBtn = [...root.querySelectorAll("button")].find((b) => b.textContent === "Add patient");
    assert.ok(addBtn);
    addBtn.dispatch("click");
    await flush();
    assert.equal(document.body.querySelectorAll("dialog").length, 1);

    unmount(); // must close the dialog, not leave it on screen (Minor 11 / main.js signOut comment)
    assert.equal(document.body.querySelectorAll("dialog").length, 0);
  } finally {
    document.createElement = realCreateElement;
  }
});

test("tab clicks and background refreshes keep the focused tab and row (no rebuilt controls)", async () => {
  const root = fakeRoot();
  document.body.append(root);
  const { ctx, callCount } = makeCtx(CASELOAD_JSON);
  const unmount = screen.mount(root, ctx, { name: "caseload", params: {}, query: {} });
  await flush();
  const tabsBefore = root.querySelectorAll('[role="tab"]');
  const watchTab = tabsBefore.find((t) => t.getAttribute("data-tab") === "on_track");
  watchTab.focus();
  watchTab.dispatch("click");
  const tabsAfter = root.querySelectorAll('[role="tab"]');
  assert.deepEqual(tabsAfter, tabsBefore);
  assert.equal(watchTab.getAttribute("aria-selected"), "true");
  assert.equal(document.activeElement, watchTab);

  // Back to "All", focus Maya's row, then a set_done refresh with changed data rebuilds the body.
  tabsBefore[0].dispatch("click");
  const mayaRow = root.querySelector('[data-row-id="maya"]');
  mayaRow.focus();
  CASELOAD_JSON.today.sessions_done = 2;
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn) => { fn(); return 0; };
  try {
    ctx.sockets.emit("events", { type: "set_done" });
    await flush();
  } finally {
    globalThis.setTimeout = realSetTimeout;
    CASELOAD_JSON.today.sessions_done = 1;
  }
  assert.ok(callCount() >= 2);
  const mayaAfter = root.querySelector('[data-row-id="maya"]');
  assert.equal(document.activeElement, mayaAfter);
  unmount();
  root.remove();
});
