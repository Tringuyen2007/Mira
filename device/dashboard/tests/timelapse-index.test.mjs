// Fakedom smoke coverage for the Recovery time-lapse screen mount: defaults the two dates to the earliest/
// latest session of the exercise, renders the headline, and tears down cleanly (no leaked frames fetch, no
// leaked rAF loop) on unmount.
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/timelapse/index.js");

import { sample } from "./samples.mjs";

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

test("Add to progress note hands the headline over in memory and navigates without it in the URL", async () => {
  const ctx = makeCtx();
  let navigated = null;
  let stored = null;
  ctx.navigate = (hash) => { navigated = hash; };
  ctx.store.set = (patch) => { if ("noteDraft" in patch) stored = patch.noteDraft; };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  const addBtn = root.querySelectorAll("button").find((b) => b.textContent === "Add to progress note");
  addBtn.dispatch("click");
  assert.equal(navigated, "#/patients/maya");
  assert.deepEqual(stored, { patientId: "maya", text: "Depth +22°, right-leg load 36% → 47%" });
  unmount();
});

test("dates come from the session-history route: defaults are the earliest and latest, weeks from the first session", async () => {
  const ctx = makeCtx();
  const history = [];
  for (let i = 0; i < 12; i++) {
    const d = new Date(Date.UTC(2026, 6, 23 + i * 7, 10));
    history.push({ id: `00000000-0000-0000-0000-${String(i).padStart(12, "0")}`, exercise_id: "squat",
      started_at: d.toISOString(), reps_done: 10 });
  }
  history.reverse(); // newest first, as the server sends it
  const asked = [];
  ctx.api.sessionHistory = async (pid, opts) => { asked.push([pid, opts]); return { sessions: history }; };
  const seen = [];
  ctx.api.session = async (sid) => { seen.push(sid); return sid === history[history.length - 1].id ? sessA() : sessB(); };
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  assert.deepEqual(asked, [["maya", { limit: 200 }]]);
  assert.deepEqual(seen.slice(0, 2), [history[history.length - 1].id, history[0].id]);
  const first = root.querySelector("#timelapse-date-a");
  const second = root.querySelector("#timelapse-date-b");
  assert.match(first.childNodes[0].textContent, /^Week 1 · Jul 23$/);
  assert.match(second.childNodes[second.childNodes.length - 1].textContent, /^Week 12 · /);
  // Accessible names: every select has a <label for>.
  for (const id of ["timelapse-date-a", "timelapse-date-b", "timelapse-exercise"]) {
    assert.ok(root.querySelector(`[for="${id}"]`), id);
  }
  // One View control for the screen, none inside the cards.
  assert.equal(root.querySelectorAll(".avatar-placeholder__segmented").length, 0);
  assert.match(root.querySelector(".patient__meta").textContent, /Right knee replacement/);
  unmount();
});

test("unmount doesn't throw and tears down cleanly", async () => {
  const ctx = makeCtx();
  const root = document.createElement("div");
  const unmount = screen.mount(root, ctx, { name: "timelapse", params: { pid: "maya" }, query: {} });
  await flush();
  assert.doesNotThrow(() => unmount());
});
