// Replay playback: drives a sorted list of frames against wall-clock time, calling `onFrame(frame)` as
// playback passes each one. `raf`/`now` are injected (default to the browser's) so tests can drive playback
// with a fake clock instead of real timers.
//
// createPlayer({ frames, reps, onFrame, raf, now }) -> {
//   play(), pause(), seekRep(n), seekTime(t), speed(x), state() -> { playing, t, duration, rep }
// }
//
// `frames` are objects with a numeric `.time` (ms elapsed since session start, ascending) — see
// loadSessionFrames below for how the paged `/sessions/{id}/frames` responses become this array. `reps` are
// repTimelineModel() rows ({ rep, tStart, tEnd, ... }).
const DEFAULT_SPEED = 1;
const MAX_PAGES = 20;

function currentRep(reps, t) {
  if (!reps || reps.length === 0) return null;
  for (const r of reps) {
    if (t >= r.tStart && t < r.tEnd) return r.rep;
  }
  const last = reps[reps.length - 1];
  return t >= last.tEnd ? last.rep : reps[0].rep;
}

function frameAt(frames, t) {
  if (!frames || frames.length === 0) return null;
  if (t <= frames[0].time) return frames[0];
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].time <= t) lo = mid; else hi = mid - 1;
  }
  return frames[lo];
}

export function createPlayer({ frames = [], reps = [], onFrame, raf, now }) {
  const sortedFrames = frames.slice().sort((a, b) => a.time - b.time);
  const rafImpl = raf || globalThis.requestAnimationFrame?.bind(globalThis);
  const nowImpl = now || (() => Date.now());
  const duration = sortedFrames.length ? sortedFrames[sortedFrames.length - 1].time : 0;

  let playing = false;
  let t = 0;
  let speedX = DEFAULT_SPEED;
  let lastTickAt = null;
  let rafHandle = null;
  let lastFrameRef = null;

  function emit() {
    const f = frameAt(sortedFrames, t);
    if (f && f !== lastFrameRef) {
      lastFrameRef = f;
      onFrame?.(f);
    }
  }

  function tick() {
    if (!playing) return;
    const n = nowImpl();
    const dt = lastTickAt === null ? 0 : n - lastTickAt;
    lastTickAt = n;
    t = Math.min(duration, t + dt * speedX);
    emit();
    if (t >= duration) {
      playing = false;
      lastTickAt = null;
      return;
    }
    rafHandle = rafImpl ? rafImpl(tick) : null;
  }

  return {
    play() {
      if (playing || sortedFrames.length === 0) return;
      if (t >= duration) t = 0;
      playing = true;
      lastTickAt = null;
      rafHandle = rafImpl ? rafImpl(tick) : null;
    },
    pause() {
      playing = false;
      lastTickAt = null;
    },
    seekRep(n) {
      const rep = reps.find((r) => r.rep === n);
      if (!rep) return;
      t = Math.max(0, Math.min(duration, rep.tStart));
      lastTickAt = null;
      emit();
    },
    seekTime(time) {
      t = Math.max(0, Math.min(duration, time));
      lastTickAt = null;
      emit();
    },
    speed(x) {
      speedX = Number(x) > 0 ? Number(x) : DEFAULT_SPEED;
    },
    state() {
      return { playing, t, duration, rep: currentRep(reps, t) };
    },
    dispose() {
      playing = false;
      if (rafHandle != null && typeof globalThis.cancelAnimationFrame === "function") {
        globalThis.cancelAnimationFrame(rafHandle);
      }
    },
  };
}

// Pages full frames (GET /sessions/{sid}/frames?downsample=0&t0=...), following `next_t0` until it's null, up
// to MAX_PAGES pages, keeping only the fields the room/player need and converting each frame's ISO `time`
// into milliseconds elapsed since `sessionStartIso` (what player.js and repTimelineModel's tStart/tEnd use).
// Aborted via `signal` (callers pass an AbortController's signal and abort() it on unmount, since this can
// run for a while on a long session).
export async function loadSessionFrames(framesPage, sid, sessionStartIso, { signal } = {}) {
  const startMs = Date.parse(sessionStartIso);
  const out = [];
  let t0;
  for (let page = 0; page < MAX_PAGES; page++) {
    if (signal && signal.aborted) break;
    const res = await framesPage(sid, t0);
    const frames = (res && Array.isArray(res.frames)) ? res.frames : [];
    for (const f of frames) {
      const ms = Date.parse(f.time);
      out.push({
        time: Number.isFinite(startMs) && Number.isFinite(ms) ? ms - startMs : 0,
        joints: f.joints || null, load_l_pct: f.load_l_pct,
        knee_l: f.knee_l, knee_r: f.knee_r, set_no: f.set_no,
      });
    }
    if (signal && signal.aborted) break;
    if (!res || !res.next_t0) break;
    t0 = res.next_t0;
  }
  return out;
}
