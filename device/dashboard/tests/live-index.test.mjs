// Fakedom smoke coverage for the Live session screen mount: route resolution (no pid -> whoever's live; a
// pid that isn't live -> empty state) and socket cleanup on unmount (the leak class called out in the brief).
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/live/index.js");

import { sample } from "./samples.mjs";

const PATIENT = sample("rest_get_patient.json");
const CASELOAD = sample("rest_get_caseload.json");

const CASELOAD_WITH_LIVE = {
  ...CASELOAD,
  today: { ...CASELOAD.today, live_now: {
    patient_id: "maya", name: "Maya Ellis", exercise_name: "Mini squats", set: 1, sets_target: 3, rep: 7,
  } },
};

function makeSockets() {
  const subs = new Map();
  return {
    subscribe(channel, fn) {
      if (!subs.has(channel)) subs.set(channel, new Set());
      subs.get(channel).add(fn);
      return () => subs.get(channel)?.delete(fn);
    },
    activeCount(channel) { return subs.get(channel)?.size || 0; },
  };
}

function makeCtx({ caseload } = {}) {
  const sockets = makeSockets();
  return {
    ctx: {
      api: {
        caseload: async () => caseload || CASELOAD,
        patient: async () => PATIENT,
      },
      sockets,
      store: { set() {}, get() { return {}; }, subscribe() { return () => {}; } },
      caps: { has: () => false },
      toast: () => {},
      navigate: () => {},
    },
    sockets,
  };
}

async function flush() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

test("#/live with no pid and nobody live shows the no-one-live empty state", async () => {
  const { ctx } = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.match(root.textContent, /No patient is in a live session right now/);
  unmount();
});

test("#/live with no pid resolves to whoever GET /caseload says is live", async () => {
  const { ctx, sockets } = makeCtx({ caseload: CASELOAD_WITH_LIVE });
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.match(root.textContent, /Maya Ellis/);
  assert.match(root.textContent, /Live session/);
  assert.ok(sockets.activeCount("live") > 0);
  assert.ok(sockets.activeCount("pose") > 0);
  unmount();
});

test("#/live/:pid for a patient who isn't live shows their empty state with a link to the replay", async () => {
  const { ctx, sockets } = makeCtx({ caseload: CASELOAD }); // no one live
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: { pid: "maya" }, query: {} });
  await flush();
  assert.match(root.textContent, /Maya isn't in a session right now/);
  // Only a watcher on /ws/live (to notice a session starting); no pose/events streams.
  assert.equal(sockets.activeCount("live"), 1);
  assert.equal(sockets.activeCount("pose"), 0);
  unmount();
  assert.equal(sockets.activeCount("live"), 0);
});

test("#/live becomes live without a reload when a session starts after the empty state", async () => {
  let caseload = CASELOAD;
  const { ctx, sockets } = makeCtx();
  ctx.api.caseload = async () => caseload;
  const subs = [];
  const realSub = sockets.subscribe;
  ctx.sockets.subscribe = (channel, fn) => { subs.push([channel, fn]); return realSub(channel, fn); };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.match(root.textContent, /No patient is in a live session right now/);
  caseload = CASELOAD_WITH_LIVE;
  const watcher = subs.find(([c]) => c === "live")[1];
  watcher({ phase: "active", first_name: "Maya", t: 1 });
  await flush();
  assert.match(root.textContent, /Live session/);
  assert.ok(sockets.activeCount("pose") > 0);
  unmount();
});

test("a boot failure shows an error with Retry, not 'no one is live'", async () => {
  const { ctx } = makeCtx();
  ctx.api.caseload = async () => { throw Object.assign(new Error("503"), { status: 503, detail: "database unavailable" }); };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.doesNotMatch(root.textContent, /No patient is in a live session/);
  assert.ok(root.querySelectorAll("button").length > 0);
  unmount();
});

test("#/live/:pid for the patient who is live mounts the room and subscribes live/pose/events", async () => {
  const { ctx, sockets } = makeCtx({ caseload: CASELOAD_WITH_LIVE });
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: { pid: "maya" }, query: {} });
  await flush();
  assert.match(root.textContent, /Live session/);
  assert.ok(sockets.activeCount("live") > 0);
  assert.ok(sockets.activeCount("pose") > 0);
  assert.ok(sockets.activeCount("events") > 0);
  unmount();
  assert.equal(sockets.activeCount("live"), 0);
  assert.equal(sockets.activeCount("pose"), 0);
  assert.equal(sockets.activeCount("events"), 0);
});

test("without caps.has('/live/say'), the Send-a-cue panel shows the device-bridge placeholder copy", async () => {
  const { ctx } = makeCtx({ caseload: CASELOAD_WITH_LIVE });
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.match(root.textContent, /Cues from the dashboard arrive with the device bridge\./);
  unmount();
});

test("with caps.has('/live/say'), the Send-a-cue panel renders the textarea and quick cues", async () => {
  const { ctx } = makeCtx({ caseload: CASELOAD_WITH_LIVE });
  ctx.caps = { has: (t) => t === "/live/say" };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "live", params: {}, query: {} });
  await flush();
  assert.match(root.textContent, /Say it through Mira/);
  assert.match(root.textContent, /Knees over your toes/);
  unmount();
});
