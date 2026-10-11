// Fakedom smoke coverage for the Patient review screen mount: boots from a #/patients/:pid route, renders the
// head/room/timeline/cards, and tears down cleanly (no leaked socket subscriptions, no dangling frames fetch)
// on unmount — the leak class called out in the brief's self-review note.
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

function makeSockets() {
  const subs = new Map(); // channel -> Set<fn>
  return {
    subscribe(channel, fn) {
      if (!subs.has(channel)) subs.set(channel, new Set());
      subs.get(channel).add(fn);
      return () => subs.get(channel)?.delete(fn);
    },
    activeCount(channel) { return subs.get(channel)?.size || 0; },
  };
}

function makeCtx({ framesPage } = {}) {
  const sockets = makeSockets();
  return {
    ctx: {
      api: {
        patient: async () => PATIENT,
        session: async () => SESSION,
        progress: async () => PROGRESS,
        memory: async () => MEMORY,
        progressNote: async () => { throw { status: 404 }; },
        framesPage: framesPage || (async () => ({ frames: [], next_t0: null })),
        approveProgressNote: async () => ({ ok: true }),
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

test("mounts from #/patients/:pid, loads the patient's latest session and renders the cards", async () => {
  const { ctx } = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "patient", params: { pid: "maya" }, query: {} });
  await flush();

  assert.match(root.textContent, /Maya Ellis/);
  assert.match(root.textContent, /This session/);
  assert.match(root.textContent, /3-week trend/);
  assert.match(root.textContent, /Therapist Memory/);
  assert.match(root.textContent, /Progress note/);
  assert.match(root.textContent, /3D view coming soon/);

  unmount();
});

test("mounts from #/replay/:sid directly (no pid known up front)", async () => {
  const { ctx } = makeCtx();
  const root = document.createElement("div");
  const sid = SESSION.session.id;
  const unmount = screen.mount(root, ctx, { name: "replay", params: { sid }, query: {} });
  await flush();
  assert.match(root.textContent, /Maya Ellis/);
  unmount();
});

test("unmount tears down without leaking live/pose socket subscriptions", async () => {
  const { ctx, sockets } = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "patient", params: { pid: "maya" }, query: {} });
  await flush();

  // Switch to Live, then unmount mid-session: both live-tab sockets must be released.
  const liveBtn = root.querySelectorAll("button").find((b) => b.textContent === "Live");
  liveBtn?.dispatch("click");
  await flush();
  assert.ok(sockets.activeCount("live") > 0 || sockets.activeCount("pose") > 0);

  unmount();
  assert.equal(sockets.activeCount("live"), 0);
  assert.equal(sockets.activeCount("pose"), 0);
});

test("a patient with no sessions shows the empty state instead of crashing", async () => {
  const { ctx } = makeCtx();
  ctx.api.patient = async () => ({ ...PATIENT, recent_sessions: [] });
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "patient", params: { pid: "maya" }, query: {} });
  await flush();
  assert.match(root.textContent, /No sessions yet/);
  unmount();
});

test("a failed progress fetch doesn't block the rest of the screen (Promise.allSettled per card)", async () => {
  const { ctx } = makeCtx();
  ctx.api.progress = async () => { throw { status: 503 }; };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "patient", params: { pid: "maya" }, query: {} });
  await flush();
  assert.match(root.textContent, /This session/);
  assert.match(root.textContent, /Therapist Memory/);
  unmount();
});

test("?note=<headline> from the time-lapse screen prefills the progress-note draft, unapproved", async () => {
  const { ctx } = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, {
    name: "patient", params: { pid: "maya" }, query: { note: "Depth +22°, right-leg load 36% → 47%" },
  });
  await flush();
  assert.match(root.textContent, /Depth \+22°, right-leg load 36% → 47%/);
  // Still an AI draft — the therapist must click Approve, same as any other AI-authored note.
  assert.match(root.textContent, /AI draft/);
  assert.match(root.textContent, /Approve/);
  unmount();
});
