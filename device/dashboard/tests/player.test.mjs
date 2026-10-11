import { test } from "node:test";
import assert from "node:assert/strict";
import { createPlayer, loadSessionFrames } from "../js/screens/patient/player.js";

function fakeClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms) => { t += ms; } };
}

function fakeRaf() {
  let queued = null;
  return {
    raf: (cb) => { queued = cb; return 1; },
    fire: () => { const cb = queued; queued = null; if (cb) cb(); },
    pending: () => queued !== null,
  };
}

const frames = [
  { time: 0, joints: null, load_l_pct: 50 },
  { time: 500, joints: null, load_l_pct: 52 },
  { time: 1000, joints: null, load_l_pct: 54 },
];
const reps = [
  { rep: 1, set: 1, flagged: false, flags: [], tStart: 0, tEnd: 500, label: "Rep 1" },
  { rep: 2, set: 1, flagged: false, flags: [], tStart: 500, tEnd: 1000, label: "Rep 2" },
];

test("play() advances t with wall-clock dt and calls onFrame as new frames become current", () => {
  const clock = fakeClock(0);
  const r = fakeRaf();
  const seen = [];
  const player = createPlayer({ frames, reps, onFrame: (f) => seen.push(f.time), raf: r.raf, now: clock.now });

  player.play();
  r.fire(); // primes lastTickAt, emits frame 0
  assert.deepEqual(seen, [0]);

  clock.advance(300);
  r.fire(); // t=300ms: still frame 0 (300 < 500)
  assert.deepEqual(seen, [0]);
  assert.ok(Math.abs(player.state().t - 300) < 1);

  clock.advance(300);
  r.fire(); // t=600ms: frame 1 (time 500) becomes current
  assert.deepEqual(seen, [0, 500]);

  clock.advance(500);
  r.fire(); // t clamps to duration (1000ms): frame 2 becomes current, then playback stops
  assert.deepEqual(seen, [0, 500, 1000]);
  assert.equal(player.state().playing, false);
  assert.equal(player.state().t, 1000);
});

test("seekRep(2) jumps to that rep's tStart and reports its rep number", () => {
  const clock = fakeClock(0);
  const r = fakeRaf();
  const seen = [];
  const player = createPlayer({ frames, reps, onFrame: (f) => seen.push(f.time), raf: r.raf, now: clock.now });

  player.seekRep(2);
  assert.equal(player.state().t, 500);
  assert.equal(player.state().rep, 2);
  assert.deepEqual(seen, [500]);
});

test("speed(0.5) halves how far t advances for the same wall-clock dt", () => {
  const clock = fakeClock(0);
  const r = fakeRaf();
  const player = createPlayer({ frames, reps, onFrame: () => {}, raf: r.raf, now: clock.now });

  player.speed(0.5);
  player.play();
  r.fire(); // primes lastTickAt
  clock.advance(400);
  r.fire();
  assert.ok(Math.abs(player.state().t - 200) < 1, `expected ~200, got ${player.state().t}`);
});

test("pause() stops playback: further clock advances don't move t", () => {
  const clock = fakeClock(0);
  const r = fakeRaf();
  const player = createPlayer({ frames, reps, onFrame: () => {}, raf: r.raf, now: clock.now });

  player.play();
  r.fire();
  clock.advance(200);
  r.fire();
  const tBeforePause = player.state().t;
  player.pause();
  assert.equal(player.state().playing, false);

  clock.advance(1000);
  assert.equal(player.state().t, tBeforePause); // no rAF scheduled after pause, so nothing to fire/advance it
});

test("loadSessionFrames: follows next_t0 until null, converts time to ms since session start", async () => {
  const sessionStart = "2026-01-01T00:00:00.000Z";
  const pages = [
    { frames: [{ time: "2026-01-01T00:00:00.500Z", joints: null, load_l_pct: 1, knee_l: 1, knee_r: 1, set_no: 1 }], next_t0: "p2" },
    { frames: [{ time: "2026-01-01T00:00:01.000Z", joints: null, load_l_pct: 2, knee_l: 2, knee_r: 2, set_no: 1 }], next_t0: null },
  ];
  let call = 0;
  const framesPage = async (sid, t0) => {
    assert.equal(sid, "sess-1");
    if (call === 0) assert.equal(t0, undefined);
    else assert.equal(t0, "p2");
    return pages[call++];
  };
  const out = await loadSessionFrames(framesPage, "sess-1", sessionStart);
  assert.equal(out.length, 2);
  assert.equal(out[0].time, 500);
  assert.equal(out[1].time, 1000);
});

test("loadSessionFrames: stops after MAX_PAGES (20) even if next_t0 never ends", async () => {
  let calls = 0;
  const framesPage = async () => {
    calls++;
    return { frames: [{ time: "2026-01-01T00:00:00.000Z", joints: null }], next_t0: "more" };
  };
  const out = await loadSessionFrames(framesPage, "sess-1", "2026-01-01T00:00:00.000Z");
  assert.equal(calls, 20);
  assert.equal(out.length, 20);
});

test("loadSessionFrames: stops when the signal is already aborted", async () => {
  let calls = 0;
  const framesPage = async () => { calls++; return { frames: [], next_t0: "more" }; };
  const controller = { aborted: true };
  const out = await loadSessionFrames(framesPage, "sess-1", "2026-01-01T00:00:00.000Z", { signal: controller });
  assert.equal(calls, 0);
  assert.equal(out.length, 0);
});
