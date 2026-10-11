import { test } from "node:test";
import assert from "node:assert/strict";
import { initialState, reduce, view, CUE_WINDOW_MS, CHEER_MS, OFFLINE_AFTER_MS } from "../js/state.js";

const live = (over = {}) => ({ channel: "live", msg: { phase: "idle", rep: 0, reps_target: 12, set: 2,
  sets_target: 3, countdown: null, load_l_pct: 50, hr: 96, paused: false, caption: null, first_name: "Maya",
  exercise_name: "Mini squats", incident: false, ...over } });
const ev = (msg) => ({ channel: "events", msg });
const open = { channel: "socket", msg: { status: "open" } };

function run(inputs, now = 1000) {
  let s = initialState();
  for (const i of inputs) s = reduce(s, i, now);
  return s;
}

test("idle before anything happens", () => {
  assert.equal(view(run([open, live()]), 1000).screen, "idle");
});

test("greeting after arrived, gesture after awaiting_gesture, countdown wins", () => {
  let s = run([open, live(), ev({ type: "arrived" })]);
  assert.equal(view(s, 1000).screen, "greeting");
  s = reduce(s, ev({ type: "awaiting_gesture", gesture: "thumbs_up" }), 1000);
  assert.equal(view(s, 1000).screen, "gesture");
  s = reduce(s, live({ phase: "countdown", countdown: 3 }), 1000);
  const v = view(s, 1000);
  assert.equal(v.screen, "countdown");
  assert.equal(v.countdown, 3);
  assert.equal(v.mood, null);
});

test("active shows rep screen; good rep cheers briefly", () => {
  let s = run([open, live({ phase: "active", rep: 7 })]);
  s = reduce(s, ev({ type: "rep", index: 7, quality: "good" }), 1000);
  let v = view(s, 1000);
  assert.equal(v.screen, "rep");
  assert.equal(v.mood, "cheering");
  assert.equal(v.rep, 7);
  assert.equal(v.repsTarget, 12);
  assert.equal(v.setLabel, "SET 2 OF 3");
  assert.equal(view(s, 1000 + CHEER_MS + 1).mood, "idle");
});

test("correction cue shows cue screen for a window; balance off also does", () => {
  let s = run([open, live({ phase: "active" })]);
  s = reduce(s, ev({ type: "cue", id: "knees_out", text: "Knees out." }), 1000);
  assert.equal(view(s, 1000).screen, "cue");
  assert.equal(view(s, 1000).mood, "worried");
  assert.equal(view(s, 1000 + CUE_WINDOW_MS + 1).screen, "rep");
  s = reduce(s, live({ phase: "active", load_l_pct: 70 }), 9000);
  const v = view(s, 9000);
  assert.equal(v.screen, "cue");
  assert.deepEqual(v.balance, { leftPct: 70, ok: false });
});

test("set_start cue is not a correction", () => {
  let s = run([open, live({ phase: "active" })]);
  s = reduce(s, ev({ type: "cue", id: "set_start", text: "Let's begin." }), 1000);
  assert.equal(view(s, 1000).screen, "rep");
});

test("paused, set done, fall priority", () => {
  let s = run([open, live({ phase: "paused", paused: true, rep: 7 })]);
  assert.equal(view(s, 1000).screen, "paused");
  assert.equal(view(s, 1000).mood, "sleeping");
  s = reduce(s, live({ phase: "rest", rep: 12, load_l_pct: 52 }), 1000);
  const v = view(s, 1000);
  assert.equal(v.screen, "setDone");
  assert.equal(v.mood, "celebrating");
  assert.deepEqual(v.summary, { reps: 12, repsTarget: 12, balance: "52 / 48", balanceOk: true });
  assert.equal(view(reduce(s, live({ phase: "rest", rep: 12, load_l_pct: 70 }), 1000), 1000).summary.balanceOk,
    false);
  s = reduce(s, live({ phase: "incident", incident: true }), 1000);
  assert.equal(view(s, 1000).screen, "fall");
});

test("open alert forces fall screen until resolved", () => {
  let s = run([open, live({ phase: "active" })]);
  s = reduce(s, ev({ type: "alert", step: "detected" }), 1000);
  s = reduce(s, ev({ type: "alert", step: "escalated", show_call_911: true }), 1000);
  assert.equal(view(s, 1000).screen, "fall");
  s = reduce(s, ev({ type: "alert", step: "resolved" }), 1000);
  assert.equal(view(s, 1000).screen, "rep");
});

test("alert-triggered fall screen clears once a live snapshot reports no incident", () => {
  let s = run([open, live({ phase: "active" })]);
  s = reduce(s, ev({ type: "alert", step: "detected" }), 1000);
  assert.equal(view(s, 1000).screen, "fall");
  s = reduce(s, live({ phase: "active", incident: false }), 1000);
  assert.equal(view(s, 1000).screen, "rep");
});

test("alert steps after the close do not reopen the fall screen", () => {
  let s = run([open]);
  s = reduce(s, ev({ type: "alert", step: "detected" }), 1000);
  s = reduce(s, ev({ type: "alert", step: "closed" }), 1000);
  s = reduce(s, ev({ type: "alert", step: "call_update" }), 1000);
  s = reduce(s, live({ phase: "active", incident: false }), 1000);
  s = reduce(s, ev({ type: "alert", step: "webhook_call_analyzed" }), 1000);
  assert.notEqual(view(s, 1000).screen, "fall");
});

test("only a confirmed fall shows the fall screen", () => {
  let s = run([open, live({ phase: "active" })]);
  s = reduce(s, ev({ type: "fall", stage: "suspected" }), 1000);
  assert.equal(view(s, 1000).screen, "rep");
  s = reduce(s, ev({ type: "fall", stage: "confirmed" }), 1000);
  assert.equal(view(s, 1000).screen, "fall");
});

test("caption comes from live", () => {
  let s = run([open, live({ caption: { text: "Hi Maya!", source: "greeting", t: 1 } })]);
  assert.equal(view(s, 1000).caption, "Hi Maya!");
  s = reduce(s, ev({ type: "cue", id: "knees_out", text: "Knees out." }), 1000);
  assert.equal(view(s, 1000).caption, "Hi Maya!", "a cue event alone does not change the caption");
  s = reduce(s, live({ caption: { text: "Knees out.", source: "cue", t: 2 } }), 1000);
  assert.equal(view(s, 1000).caption, "Knees out.");
  s = reduce(s, live({ caption: null }), 1000);
  assert.equal(view(s, 1000).caption, "");
});

test("offline after the socket is closed for a while; unpaired on rejection", () => {
  let s = run([open, live()]);
  s = reduce(s, { channel: "socket", msg: { status: "closed" } }, 1000);
  assert.equal(view(s, 1000).offline, false);
  assert.equal(view(s, 1000 + OFFLINE_AFTER_MS + 1).offline, true);
  s = reduce(s, { channel: "socket", msg: { status: "rejected" } }, 1000);
  assert.equal(view(s, 1000).screen, "unpaired");
});

test("balance hidden when unknown, ok within 15 points", () => {
  assert.equal(view(run([open, live({ phase: "active", load_l_pct: null })]), 1000).balance, null);
  assert.deepEqual(view(run([open, live({ phase: "active", load_l_pct: 64 })]), 1000).balance,
    { leftPct: 64, ok: true });
});

test("dose line and pings are ignored", () => {
  let s = run([open, live({ phase: "idle" }), ev({ type: "arrived" }), ev({ type: "ping" })]);
  assert.equal(view(s, 1000).dose, "3 sets · 12 reps");
});
