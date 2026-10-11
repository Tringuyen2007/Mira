import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { createNotificationsBell } = await import("../js/ui/notificationsBell.js");

const SAMPLE = [
  { id: 1, time: 3, kind: "alert", severity: "urgent", title: "No response from Maya.",
    link: "#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b", read_at: null, acked_at: null },
  { id: 2, time: 2, kind: "milestone", severity: "low", title: "Maya hit her squat goal.",
    link: "#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b", read_at: null, acked_at: null },
];

function fakeApi(overrides = {}) {
  return {
    notifications: async () => ({ notifications: SAMPLE.slice() }),
    notificationCount: async () => ({ unread: 2, urgent_unacked: 1 }),
    markRead: async (id) => ({ id, read: true }),
    markAllRead: async () => ({ marked_read: 2 }),
    ack: async (id) => ({ id, acked_at: 999, acked_by: "dr_okafor" }),
    ...overrides,
  };
}

test("toggle opens the popover and Esc closes it and returns focus to the trigger", async () => {
  const panelEl = document.createElement("div");
  const trigger = document.createElement("button");
  const bell = createNotificationsBell({ panelEl, api: fakeApi(), navigate: () => {}, toast: () => {} });
  await bell.init();
  bell.toggle(trigger);
  assert.equal(panelEl.hidden, false);
  assert.ok(panelEl.querySelector('[role="dialog"]'));
  document.dispatch("keydown", { key: "Escape" });
  assert.equal(panelEl.hidden, true);
  assert.equal(trigger._focused, true);
  bell.destroy();
});

test("clicking the trigger again toggles the popover closed", async () => {
  const panelEl = document.createElement("div");
  const trigger = document.createElement("button");
  const bell = createNotificationsBell({ panelEl, api: fakeApi(), navigate: () => {}, toast: () => {} });
  await bell.init();
  bell.toggle(trigger);
  assert.equal(panelEl.hidden, false);
  bell.toggle(trigger);
  assert.equal(panelEl.hidden, true);
  bell.destroy();
});

test("the panel lists the latest notifications and marks an item read on open", async () => {
  const panelEl = document.createElement("div");
  let readCalls = [];
  const bell = createNotificationsBell({
    panelEl, api: fakeApi({ markRead: async (id) => { readCalls.push(id); return { id, read: true }; } }),
    navigate: () => {}, toast: () => {},
  });
  await bell.init();
  bell.toggle(document.createElement("button"));
  const items = panelEl.querySelectorAll(".notif-panel__item");
  assert.equal(items.length, 2);
  const row = items[0].querySelector(".notif-item__row");
  row.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(readCalls, [1]);
  bell.destroy();
});

test("ack is wired to the urgent item's Acknowledge button", async () => {
  const panelEl = document.createElement("div");
  let acked = [];
  const bell = createNotificationsBell({
    panelEl, api: fakeApi({ ack: async (id) => { acked.push(id); return { id, acked_at: 1, acked_by: "x" }; } }),
    navigate: () => {}, toast: () => {},
  });
  await bell.init();
  bell.toggle(document.createElement("button"));
  const ackBtn = panelEl.querySelector(".notif-item__ack");
  assert.ok(ackBtn);
  ackBtn.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(acked, [1]);
  bell.destroy();
});

test("a pushed urgent unacked notification toasts once, never duplicated for the same id", async () => {
  const panelEl = document.createElement("div");
  const toasts = [];
  const bell = createNotificationsBell({
    panelEl, api: fakeApi(), navigate: () => {}, toast: (t) => toasts.push(t),
  });
  await bell.init();
  const push = { id: 9, time: 10, kind: "alert", severity: "urgent", title: "Fall escalated for Maya.",
    link: "#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b", read_at: null, acked_at: null };
  bell.handlePush({ type: "notification", ...push });
  bell.handlePush({ type: "notification", ...push }); // resend: must not toast twice
  assert.equal(toasts.length, 1);
  bell.destroy();
});

test("markAllRead clears the unread badge", async () => {
  const panelEl = document.createElement("div");
  const bell = createNotificationsBell({ panelEl, api: fakeApi(), navigate: () => {}, toast: () => {} });
  await bell.init();
  assert.equal(bell.getState().unread, 2);
  bell.toggle(document.createElement("button"));
  panelEl.querySelector(".notif-panel__mark-all").dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(bell.getState().unread, 0);
  bell.destroy();
});

test("rows are real buttons, and closing returns focus to the live sidebar bell even after sidebar updates", async () => {
  const { createSidebarView } = await import("../js/ui/sidebar.js");
  const panelEl = document.createElement("div");
  let view = null;
  let bell = null;
  const state = () => ({ route: { name: "caseload" }, proposalsWaiting: 0, selectedPatientId: null,
    therapist: { name: "Dr. Sam Lee" }, live: null, unreadCount: bell.getState().unread,
    bellOpen: bell.getState().open, onBellToggle: (btn) => bell.toggle(btn) });
  bell = createNotificationsBell({ panelEl, api: fakeApi(), navigate: () => {}, toast: () => {},
    onChange: () => view?.update(state()) });
  view = createSidebarView(state());
  document.body.append(view.el);
  await bell.init();
  view.bellEl.dispatch("click");
  assert.equal(view.bellEl.getAttribute("aria-expanded"), "true");
  assert.equal(panelEl.querySelector(".notif-item__row").tagName, "BUTTON");
  bell.close();
  assert.equal(document.activeElement, view.bellEl);
  assert.equal(view.bellEl.getAttribute("aria-expanded"), "false");
  view.el.remove();
  bell.destroy();
});
