import { test } from "node:test";
import assert from "node:assert/strict";
import { initialState, reduce, view, CUE_WINDOW_MS, CHEER_MS, OFFLINE_AFTER_MS, SESSION_DONE_MS }
  from "../js/state.js";

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
  s = reduce(s, ev({ type: "awaiting_gesture", gesture: "raised_hand" }), 1000);
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

const summaryMsg = { session_id: "s1", exercise_name: "Mini squats", form_score: 82, reps_total: 12,
  good_reps: 10, spoken: "Nice work today, Maya!", audio_url: "/audio/summary_s1.mp3" };

test("session_summary shows the sessionDone screen with the numbers while fresh and idle/rest/done", () => {
  let s = run([open, live({ phase: "idle" })]);
  s = reduce(s, ev({ type: "session_summary", ...summaryMsg }), 1000);
  let v = view(s, 1000);
  assert.equal(v.screen, "sessionDone");
  assert.equal(v.mood, "celebrating");
  assert.deepEqual(v.sessionSummary, { formScore: 82, goodReps: 10, repsTotal: 12, spoken: "Nice work today, Maya!" });

  s = reduce(s, live({ phase: "rest" }), 1000);
  assert.equal(view(s, 1000).screen, "sessionDone");

  s = reduce(s, live({ phase: "done" }), 1000);
  assert.equal(view(s, 1000).screen, "sessionDone");
});

test("session_summary expires after SESSION_DONE_MS", () => {
  let s = run([open, live({ phase: "idle" })]);
  s = reduce(s, ev({ type: "session_summary", ...summaryMsg }), 1000);
  assert.equal(view(s, 1000 + SESSION_DONE_MS).screen, "sessionDone");
  assert.equal(view(s, 1000 + SESSION_DONE_MS + 1).screen, "idle");
});

test("arrived clears the session summary", () => {
  let s = run([open, live({ phase: "idle" })]);
  s = reduce(s, ev({ type: "session_summary", ...summaryMsg }), 1000);
  s = reduce(s, ev({ type: "arrived" }), 1000);
  assert.equal(view(s, 1000).screen, "greeting");
});

test("countdown phase wins over a fresh session summary (next session started)", () => {
  let s = run([open, live({ phase: "idle" })]);
  s = reduce(s, ev({ type: "session_summary", ...summaryMsg }), 1000);
  s = reduce(s, live({ phase: "countdown", countdown: 3 }), 1000);
  assert.equal(view(s, 1000).screen, "countdown");
});

test("captionsOn defaults true when device_prefs is absent or captions is not false", () => {
  let s = run([open, live()]);
  assert.equal(view(s, 1000).captionsOn, true);
  s = run([open, live({ device_prefs: {} })]);
  assert.equal(view(s, 1000).captionsOn, true);
  s = run([open, live({ device_prefs: { captions: true } })]);
  assert.equal(view(s, 1000).captionsOn, true);
});

test("captionsOn is false only when device_prefs.captions === false", () => {
  const s = run([open, live({ device_prefs: { captions: false } })]);
  assert.equal(view(s, 1000).captionsOn, false);
});

test("speaking defaults to false and follows the speaking channel", () => {
  let s = run([open, live({ phase: "active" })]);
  assert.equal(view(s, 1000).speaking, false);
  s = reduce(s, { channel: "speaking", msg: { on: true } }, 1000);
  assert.equal(view(s, 1000).speaking, true);
  s = reduce(s, { channel: "speaking", msg: { on: false } }, 1000);
  assert.equal(view(s, 1000).speaking, false);
  s = reduce(s, { channel: "speaking", msg: { on: "yes" } }, 1000);
  assert.equal(view(s, 1000).speaking, false, "only a real true counts");
});

test("a say event changes neither screen nor mood; its caption arrives via live", () => {
  let s = run([open, live({ phase: "active", rep: 3 })]);
  const before = view(s, 1000);
  s = reduce(s, ev({ type: "say", session_id: "s_01", t: 1, id: "count_3", text: "Three!",
    audio_url: "/audio/count_3_1.mp3" }), 1000);
  const after = view(s, 1000);
  assert.equal(after.screen, before.screen);
  assert.equal(after.mood, before.mood);
  assert.equal(after.caption, "");
  s = reduce(s, live({ phase: "active", rep: 3, caption: { text: "Three!", source: "say", t: 1 } }), 1000);
  assert.equal(view(s, 1000).caption, "Three!");
});

const alert = (step, over = {}) => ev({ type: "alert", alert_id: "a1", kind: "fall", step, ...over });

function inCall(...steps) {
  let s = run([open, live({ phase: "active" }), alert("detected", { message: "Fall detected." })]);
  for (const st of steps) s = reduce(s, st, 1000);
  return s;
}

test("no call status before escalation", () => {
  const s = inCall();
  assert.equal(view(s, 1000).screen, "fall");
  assert.equal(view(s, 1000).callStatus, null);
  assert.equal(view(s, 1000).show911, false);
  assert.equal(view(run([open, live()]), 1000).callStatus, null);
  assert.equal(view(inCall(alert("checked_in", { message: "Maya answered." })), 1000).callStatus, null);
});

test("escalated → calling with the alert's message; show_call_911 is exposed", () => {
  const s = inCall(alert("escalated", { message: "No response from Maya. Calling the emergency contact.",
    show_call_911: true }));
  const v = view(s, 1000);
  assert.equal(v.screen, "fall");
  assert.deepEqual(v.callStatus, { phase: "calling",
    message: "No response from Maya. Calling the emergency contact." });
  assert.equal(v.show911, true);
});

test("alert steps map to call phases", () => {
  const phase = (step) => view(inCall(alert(step, { message: step })), 1000).callStatus?.phase ?? null;
  for (const step of ["escalated", "call_registered", "call_retry"]) assert.equal(phase(step), "calling", step);
  for (const step of ["call_ongoing", "call_update", "patient_responded"]) assert.equal(phase(step), "on_call", step);
  for (const step of ["call_ended", "acknowledged"]) assert.equal(phase(step), "ended", step);
  for (const step of ["call_not_connected", "call_error", "call_failed", "call_watch_timeout"]) {
    assert.equal(phase(step), "failed", step);
  }
});

test("call_ongoing → on_call, call_failed → failed, and a non-string message is empty", () => {
  let s = inCall(alert("escalated", { message: "Calling." }), alert("call_ongoing", { message: "On the call." }));
  assert.deepEqual(view(s, 1000).callStatus, { phase: "on_call", message: "On the call." });
  s = reduce(s, alert("call_failed", { message: 42 }), 1000);
  assert.deepEqual(view(s, 1000).callStatus, { phase: "failed", message: "" });
  assert.equal(view(s, 1000).show911, false, "show911 follows the latest step's show_call_911");
});

test("a step that names no call state keeps the last call status", () => {
  let s = inCall(alert("call_ongoing", { message: "On the call." }));
  s = reduce(s, alert("therapist_call", { message: "Calling the therapist too." }), 1000);
  assert.deepEqual(view(s, 1000).callStatus, { phase: "on_call", message: "On the call." });
});

test("resolved clears the call status (and the fall screen)", () => {
  let s = inCall(alert("escalated", { message: "Calling.", show_call_911: true }));
  s = reduce(s, alert("resolved", { message: "All clear." }), 1000);
  const v = view(s, 1000);
  assert.equal(v.callStatus, null);
  assert.equal(v.show911, false);
  assert.equal(v.screen, "rep");
});

test("call steps that land after the close do not bring back a call status", () => {
  let s = inCall(alert("escalated", { message: "Calling." }));
  s = reduce(s, alert("closed"), 1000);
  s = reduce(s, live({ phase: "active", incident: false }), 1000);
  s = reduce(s, alert("call_update", { message: "late" }), 1000);
  s = reduce(s, ev({ type: "fall", stage: "confirmed" }), 1000);
  assert.equal(view(s, 1000).screen, "fall");
  assert.equal(view(s, 1000).callStatus, null);
});

test("arrived resets the call status", () => {
  let s = inCall(alert("escalated", { message: "Calling.", show_call_911: true }));
  s = reduce(s, ev({ type: "arrived" }), 1000);
  s = reduce(s, live({ phase: "incident", incident: true }), 1000);
  const v = view(s, 1000);
  assert.equal(v.screen, "fall");
  assert.equal(v.callStatus, null);
  assert.equal(v.show911, false);
});

test("the fall screen (with a call) wins over every screen but unpaired", () => {
  const calling = [alert("detected"), alert("escalated", { message: "Calling." })];
  const cases = [
    [live({ phase: "paused", paused: true })],
    [live({ phase: "countdown", countdown: 2 })],
    [live({ phase: "rest", rep: 12 })],
    [live({ phase: "idle" }), ev({ type: "session_summary", ...summaryMsg })],
    [live({ phase: "idle" }), ev({ type: "awaiting_gesture" })],
    [live({ phase: "active" }), ev({ type: "cue", id: "knees_out" })],
  ];
  for (const inputs of cases) {
    const v = view(run([open, ...inputs, ...calling]), 1000);
    assert.equal(v.screen, "fall", JSON.stringify(inputs[0].msg));
    assert.equal(v.callStatus.phase, "calling");
  }
  const s = run([open, live({ phase: "active" }), ...calling, { channel: "socket", msg: { status: "rejected" } }]);
  assert.equal(view(s, 1000).screen, "unpaired");
});

test("a live snapshot with no incident clears a stale call status (missed closed alert)", () => {
  let s = inCall(alert("escalated", { message: "Calling.", show_call_911: true }),
    alert("call_ongoing", { message: "Emergency contact is on the call.", show_call_911: true }));
  s = reduce(s, live({ phase: "active", incident: false }), 1000);
  assert.equal(s.alertStep, null);
  assert.equal(s.alertMessage, "");
  assert.equal(s.show911, false);
  s = reduce(s, ev({ type: "fall", stage: "confirmed" }), 1000);
  const v = view(s, 1000);
  assert.equal(v.screen, "fall");
  assert.equal(v.callStatus, null);
  assert.equal(v.show911, false);
});

test("a live snapshot during the incident keeps the call status", () => {
  let s = inCall(alert("escalated", { message: "Calling.", show_call_911: true }));
  s = reduce(s, live({ phase: "incident", incident: true }), 1000);
  assert.deepEqual(view(s, 1000).callStatus, { phase: "calling", message: "Calling." });
  assert.equal(view(s, 1000).show911, true);
});
