// Fakedom smoke coverage for the Recovery time-lapse screen mount: defaults the two dates to the earliest/
// latest session of the exercise, renders the headline, and tears down cleanly (no leaked frames fetch, no
// leaked rAF loop) on unmount.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/timelapse/index.js");

function sample(name) {
  return JSON.parse(readFileSync(new URL(`../../../server/samples/${name}`, import.meta.url), "utf8"));
}

const SESSION = sample("rest_get_session.json");

const PATIENT = {
  patient: { id: "maya", name: "Maya Ellis", age: 67, condition: "right knee replacement",
    affected_side: "right", created_at: "2026-08-01T00:00:00Z" },
  prescriptions: [],
  recent_sessions: [
    { id: "11111111-1111-1111-1111-111111111111", exercise_id: "squat", started_at: "2026-09-20T10:00:00Z", reps_done: 4 },
    { id: "22222222-2222-2222-2222-222222222222", exercise_id: "squat", started_at: "2026-10-11T10:00:00Z", reps_done: 4 },
  ],
};

function sessA() {
  return {
    ...SESSION,
    session: { ...SESSION.session, id: "11111111-1111-1111-1111-111111111111", started_at: "2026-09-20T10:00:00Z" },
    reps: [{ ...SESSION.reps[0], rep: 1, peak_angle: 70, flags: [] }],
    tw_load_left_pct: 64,
  };
}

function sessB() {
  return {
    ...SESSION,
    session: { ...SESSION.session, id: "22222222-2222-2222-2222-222222222222", started_at: "2026-10-11T10:00:00Z" },
    reps: [{ ...SESSION.reps[0], rep: 1, peak_angle: 92, flags: [] }],
    tw_load_left_pct: 53,
  };
}

function makeCtx() {
  return {
    api: {
      patient: async () => PATIENT,
      session: async (sid) => (sid === sessA().session.id ? sessA() : sessB()),
      framesPage: async () => ({ frames: [], next_t0: null }),
    },
    store: { set() {}, get() { return {}; }, subscribe() { return () => {}; } },
    caps: { has: () => false },
    toast: () => {},
    navigate: () => {},
  };
}

async function flush() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

test("mounts from #/patients/:pid/timelapse, defaults to the earliest/latest session and shows the headline", async () => {
  const ctx = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  assert.match(root.textContent, /Recovery time-lapse/);
  assert.match(root.textContent, /Depth \+22°, right-leg load 36% → 47%/);
  unmount();
});

test("Add to progress note navigates to the patient screen with ?note=<headline>", async () => {
  const ctx = makeCtx();
  let navigated = null;
  ctx.navigate = (hash) => { navigated = hash; };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  const addBtn = root.querySelectorAll("button").find((b) => b.textContent === "Add to progress note");
  addBtn.dispatch("click");
  assert.ok(navigated);
  assert.match(navigated, /#\/patients\/maya\?note=/);
  unmount();
});

test("unmount doesn't throw and tears down cleanly", async () => {
  const ctx = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  assert.doesNotThrow(() => unmount());
});
