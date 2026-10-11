// Fakedom coverage for Patient review's "Apple Watch" group: capability gating, the three cards from the sample,
// the empty state, the Tremor monitoring switch (PATCH body, aria-checked) and the watch_features_changed push.
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/patient/index.js");

import { sample } from "./samples.mjs";

const PATIENT = sample("rest_get_patient.json");
const SESSION = sample("rest_get_session.json");
const PROGRESS = sample("rest_get_patient_progress.json");
const MEMORY = sample("rest_get_patient_memory.json");
const WATCH = sample("rest_get_patient_watch.json");
const WATCH_CAPS = new Set(["/patients/{patient_id}/watch", "/patients/{patient_id}/watch-features"]);

function makeSockets() {
  const subs = new Map();
  return {
    subscribe(channel, fn) {
      if (!subs.has(channel)) subs.set(channel, new Set());
      subs.get(channel).add(fn);
      return () => subs.get(channel)?.delete(fn);
    },
    emit(channel, msg) { for (const fn of subs.get(channel) || []) fn(msg); },
    activeCount(channel) { return subs.get(channel)?.size || 0; },
  };
}

function makeCtx({ caps = WATCH_CAPS, watch = WATCH, patient = PATIENT, setWatchFeatures } = {}) {
  const sockets = makeSockets();
  const calls = [];
  return {
    calls,
    sockets,
    ctx: {
      api: {
        patient: async () => patient,
        session: async () => SESSION,
        progress: async () => PROGRESS,
        memory: async () => MEMORY,
        progressNote: async () => { throw { status: 404 }; },
        framesPage: async () => ({ frames: [], next_t0: null }),
        patientWatch: async (pid) => { calls.push(["watch", pid]); return watch; },
        setWatchFeatures: setWatchFeatures || (async (pid, f) => {
          calls.push(["features", pid, f]);
          return { patient_id: pid, tremor_monitoring: f.tremorMonitoring };
        }),
      },
      sockets,
      store: { set() {}, get() { return {}; }, subscribe() { return () => {}; } },
      caps: { has: (p) => caps.has(p) },
      toast: () => {},
      navigate: () => {},
    },
  };
}

async function flush() {
  for (let i = 0; i < 16; i++) await Promise.resolve();
}

const mount = (ctx) => {
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "patient", params: { pid: "maya" }, query: {} });
  return { root, unmount };
};
const toggleOf = (root) => root.querySelector(".watch__toggle");

test("without the watch capability there is no Apple Watch group and no watch request", async () => {
  const { ctx, calls } = makeCtx({ caps: new Set() });
  const { root, unmount } = mount(ctx);
  await flush();
  assert.doesNotMatch(root.textContent, /Apple Watch/);
  assert.equal(calls.length, 0);
  unmount();
});

test("renders HR recovery, rotation range and the Simulated tremor card from GET /patients/{id}/watch", async () => {
  const { ctx, calls } = makeCtx();
  const { root, unmount } = mount(ctx);
  await flush();
  assert.deepEqual(calls[0], ["watch", "maya"]);
  assert.match(root.textContent, /Apple Watch/);
  assert.match(root.textContent, /Avg drop 22 bpm in 1 min/);
  assert.match(root.textContent, /Forearm turns/);
  assert.match(root.textContent, /Goal reached/);
  assert.match(root.textContent, /Tremor \(14 days\)/);
  assert.ok(root.querySelector(".watch__simulated"), "Simulated label shown");
  unmount();
});

test("no watch data at all -> one empty state", async () => {
  const { ctx } = makeCtx({ watch: { patient_id: "maya", hr_recovery: [], rotation_sets: [], tremor: [] } });
  const { root, unmount } = mount(ctx);
  await flush();
  assert.match(root.textContent, /No Apple Watch data yet/);
  assert.doesNotMatch(root.textContent, /Avg drop/);
  unmount();
});

test("the Tremor monitoring switch PATCHes the flipped value and reflects the response", async () => {
  const { ctx, calls } = makeCtx();
  const { root, unmount } = mount(ctx);
  await flush();
  const toggle = toggleOf(root);
  assert.equal(toggle.getAttribute("role"), "switch");
  assert.equal(toggle.getAttribute("aria-checked"), "false"); // fixture patient has no tremor_monitoring
  toggle.dispatch("click");
  await flush();
  assert.deepEqual(calls.at(-1), ["features", "maya", { tremorMonitoring: true }]);
  assert.equal(toggle.getAttribute("aria-checked"), "true");
  unmount();
});

test("a failed PATCH leaves the switch as it was", async () => {
  const { ctx } = makeCtx({ setWatchFeatures: async () => { throw { status: 503 }; } });
  let toasted = null;
  ctx.toast = (t) => { toasted = t; };
  const { root, unmount } = mount(ctx);
  await flush();
  toggleOf(root).dispatch("click");
  await flush();
  assert.equal(toggleOf(root).getAttribute("aria-checked"), "false");
  assert.equal(toasted.kind, "error");
  unmount();
});

test("watch_features_changed for this patient flips the switch; another patient's is ignored; unmount unsubscribes", async () => {
  const { ctx, sockets } = makeCtx();
  const { root, unmount } = mount(ctx);
  await flush();
  sockets.emit("events", { type: "watch_features_changed", patient_id: "sam", tremor_monitoring: true });
  assert.equal(toggleOf(root).getAttribute("aria-checked"), "false");
  sockets.emit("events", { type: "watch_features_changed", patient_id: "maya", tremor_monitoring: true });
  assert.equal(toggleOf(root).getAttribute("aria-checked"), "true");
  assert.match(root.textContent, /Tremor monitoring turned on/);
  unmount();
  assert.equal(sockets.activeCount("events"), 0);
});

test("without the watch-features capability the cards show but the switch is hidden", async () => {
  const { ctx } = makeCtx({ caps: new Set(["/patients/{patient_id}/watch"]) });
  const { root, unmount } = mount(ctx);
  await flush();
  assert.match(root.textContent, /Avg drop 22 bpm/);
  assert.equal(toggleOf(root), null);
  unmount();
});
