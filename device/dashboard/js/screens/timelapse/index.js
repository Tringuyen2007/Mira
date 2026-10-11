// Recovery time-lapse screen mount (Figma 208:339): two dates of the same exercise, each one's best rep
// replayed in sync, on the avatar placeholder (createAvatarView). Side by side = two independent views; Overlay
// = one view with { ghost: true }, fed both frames each tick (avatarView's setFrame(frame, ghostFrame)).
// Cleanup on unmount: the shared rAF loop and the two frames AbortControllers (same leak class as the Patient
// review screen's self-review note).
import { h } from "../../core/dom.js";
import { href } from "../../core/router.js";
import { patientMeta } from "../../core/format.js";
import { exerciseName } from "../../core/exercises.js";
import { createAvatarView } from "../../avatar/avatarView.js";
import { loadSessionFrames } from "../patient/player.js";
import { bestRep, timelapseModel, syncClock, framesDepth, weekLabel } from "./model.js";
import {
  renderHead, renderControls, fillDateOptions, fillExerciseOptions, renderSideCard, renderOverlayCard,
  renderTransport, noExerciseSessions,
} from "./view.js";
import { loading, errorState, empty } from "../../ui/states.js";

const PID_RE = /^[A-Za-z0-9_-]{1,64}$/;

const fmtDayShort = (iso) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

// "Week 1 · Jul 23": weeks count from the patient's first session.
function sessionLabel(s, firstSessionAt, createdAt) {
  const week = weekLabel(s.started_at, firstSessionAt, createdAt);
  const date = s.started_at ? fmtDayShort(s.started_at) : "—";
  return week ? `${week} · ${date}` : date;
}

// Nearest frame at or before `t` (ms), linear-scan: a bestRep window is a handful of seconds of frames, so
// this is plenty fast and keeps the windowed arrays simple (player.js's binary-search frameAt isn't exported).
function frameAt(frames, t) {
  if (!frames || frames.length === 0) return null;
  let f = frames[0];
  for (const cand of frames) {
    if (cand.time > t) break;
    f = cand;
  }
  return f;
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    const pid = PID_RE.test(route.params.pid || "") ? route.params.pid : null;

    const bodyHost = h("div", {});
    root.append(bodyHost);

    const state = {
      patientJson: null,
      exerciseId: null,
      sidA: null,
      sidB: null,
      sessA: null,
      sessB: null,
      mode: "side", // "side" | "overlay"
      view: "front", // "front" | "side"
      speed: 1,
      playing: false,
      history: [], // finished sessions (GET /patients/{id}/session-history), all exercises
      firstSessionAt: null,
      depthA: null, // frames-based depth fallbacks for the headline
      depthB: null,
    };
    let sessionsSeq = 0;

    let framesAbortA = null;
    let framesAbortB = null;
    let framesA = []; // windowed to bestRep(sessA), time rebased to 0
    let framesB = [];
    let durA = 0;
    let durB = 0;
    let rafHandle = null;
    let lastTickAt = null;
    let clockT = 0; // 0..maxDuration ms

    let headRefs = null;
    let controlsRefs = null;
    let transportRefs = null;
    let cardA = null;
    let cardB = null;
    let overlayHost = null;
    let overlayRefs = null;
    let avatarA = null;
    let avatarB = null;
    let avatarOverlay = null;

    function exercisesFor() {
      const seen = new Map();
      for (const s of state.history) {
        if (!seen.has(s.exercise_id)) {
          seen.set(s.exercise_id, { id: s.exercise_id, label: `${exerciseName(s.exercise_id)} · best rep` });
        }
      }
      return [...seen.values()];
    }

    function sessionsForExercise(exerciseId) {
      const createdAt = state.patientJson.patient && state.patientJson.patient.created_at;
      return state.history
        .filter((s) => s.exercise_id === exerciseId)
        .slice()
        .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))
        .map((s) => ({ id: s.id, label: sessionLabel(s, state.firstSessionAt, createdAt), startedAt: s.started_at }));
    }

    // The full finished-session history (bug 12: the 10 recent sessions made both defaults the same week).
    // Falls back to GET /patients/{id}'s recent_sessions if the history route isn't there.
    async function loadHistory() {
      let list = null;
      try {
        const res = await ctx.api.sessionHistory(pid, { limit: 200 });
        list = (res && Array.isArray(res.sessions)) ? res.sessions : null;
      } catch { /* older server: use recent_sessions below */ }
      if (!list) list = (state.patientJson.recent_sessions || []).filter((s) => s.ended_at !== null);
      state.history = list.filter((s) => s && s.id && s.exercise_id && s.started_at);
      const times = state.history.map((s) => Date.parse(s.started_at)).filter(Number.isFinite);
      state.firstSessionAt = times.length ? new Date(Math.min(...times)).toISOString() : null;
    }

    function disposeAvatars() {
      avatarA?.dispose(); avatarA = null;
      avatarB?.dispose(); avatarB = null;
      avatarOverlay?.dispose(); avatarOverlay = null;
    }

    function stopClock() {
      state.playing = false;
      lastTickAt = null;
      if (rafHandle != null && typeof globalThis.cancelAnimationFrame === "function") {
        globalThis.cancelAnimationFrame(rafHandle);
      }
      rafHandle = null;
    }

    function tick() {
      if (!state.playing) return;
      const now = Date.now();
      const dt = lastTickAt === null ? 0 : now - lastTickAt;
      lastTickAt = now;
      const maxDur = Math.max(durA, durB, 1);
      clockT = Math.min(maxDur, clockT + dt * state.speed);
      applyClock();
      if (clockT >= maxDur) { state.playing = false; lastTickAt = null; updateTransportUi(); return; }
      rafHandle = globalThis.requestAnimationFrame ? globalThis.requestAnimationFrame(tick) : null;
    }

    function applyClock() {
      const maxDur = Math.max(durA, durB, 1);
      const p = clockT / maxDur;
      const { tA, tB } = syncClock(durA, durB, p);
      const frameA = frameAt(framesA, tA);
      const frameB = frameAt(framesB, tB);
      if (state.mode === "side") {
        if (frameA) avatarA?.setFrame({ joints: frameA.joints, load_l_pct: frameA.load_l_pct, flags: [], t: frameA.time });
        if (frameB) avatarB?.setFrame({ joints: frameB.joints, load_l_pct: frameB.load_l_pct, flags: [], t: frameB.time });
      } else if (avatarOverlay && frameB) {
        avatarOverlay.setFrame(
          { joints: frameB.joints, load_l_pct: frameB.load_l_pct, flags: [], t: frameB.time },
          frameA ? { joints: frameA.joints, load_l_pct: frameA.load_l_pct, flags: [], t: frameA.time } : null,
        );
      }
      updateTransportUi();
    }

    function updateTransportUi() {
      if (!transportRefs) return;
      transportRefs.playBtn.setAttribute("aria-label", state.playing ? "Pause" : "Play");
      const maxDur = Math.max(durA, durB, 1);
      const repLabel = `rep ${fmtClock(clockT)} of ${fmtClock(maxDur)}`;
      transportRefs.captionEl.textContent = `Both replays stay in sync · ${repLabel}`;
    }

    function fmtClock(ms) {
      const s = Math.max(0, Math.round((Number(ms) || 0) / 1000));
      const m = Math.floor(s / 60);
      const r = s % 60;
      return `${m}:${String(r).padStart(2, "0")}`;
    }

    function mountAvatarsForMode() {
      disposeAvatars();
      const affectedSide = (state.patientJson.patient && state.patientJson.patient.affected_side) || "right";
      if (state.mode === "side") {
        cardA.avatarHost.replaceChildren();
        cardB.avatarHost.replaceChildren();
        avatarA = createAvatarView(cardA.avatarHost, { mode: "replay", affectedSide, ghost: false, viewControls: false });
        avatarB = createAvatarView(cardB.avatarHost, { mode: "replay", affectedSide, ghost: false, viewControls: false });
      } else {
        avatarOverlay = createAvatarView(overlayRefs.avatarHost, { mode: "replay", affectedSide, ghost: true,
          viewControls: false });
      }
      avatarA?.setView(state.view);
      avatarB?.setView(state.view);
      avatarOverlay?.setView(state.view);
      applyClock();
    }

    function renderLayout() {
      const sideRow = h("div", { class: "timelapse__side-grid" }, cardA.el, cardB.el);
      const body = state.mode === "side" ? sideRow : overlayRefs.el;
      bodyHost.replaceChildren(headRefs.el, controlsRefs.el, body, transportRefs.el);
    }

    function updateCardHeads() {
      const m = timelapseModel(state.sessA, state.sessB, state.patientJson,
        { firstSessionAt: state.firstSessionAt, depthA: state.depthA, depthB: state.depthB });
      cardA.labelEl.textContent = m.left.label;
      cardA.dateEl.textContent = m.left.date;
      cardB.labelEl.textContent = m.right.label;
      cardB.dateEl.textContent = m.right.date;
      overlayRefs.earlierLabelEl.textContent = m.left.label;
      overlayRefs.laterLabelEl.textContent = m.right.label;
      transportRefs.headlineEl.textContent = m.headline || "Not enough data yet.";
      return m;
    }

    async function loadFrames(mySeq) {
      framesAbortA?.abort(); framesAbortB?.abort();
      framesAbortA = new AbortController();
      framesAbortB = new AbortController();
      const repA = bestRep(state.sessA);
      const repB = bestRep(state.sessB);
      const [allA, allB] = await Promise.all([
        loadSessionFrames(ctx.api.framesPage, state.sidA, state.sessA.session.started_at, { signal: framesAbortA.signal }),
        loadSessionFrames(ctx.api.framesPage, state.sidB, state.sessB.session.started_at, { signal: framesAbortB.signal }),
      ]);
      if (!alive || mySeq !== sessionsSeq) return;
      framesA = repA ? allA.filter((f) => f.time >= repA.tStart && f.time <= repA.tEnd)
        .map((f) => ({ ...f, time: f.time - repA.tStart })) : [];
      framesB = repB ? allB.filter((f) => f.time >= repB.tStart && f.time <= repB.tEnd)
        .map((f) => ({ ...f, time: f.time - repB.tStart })) : [];
      durA = framesA.length ? framesA[framesA.length - 1].time : 0;
      durB = framesB.length ? framesB[framesB.length - 1].time : 0;
      state.depthA = framesDepth(framesA);
      state.depthB = framesDepth(framesB);
      updateCardHeads(); // the headline's depth can come from the frames when reps carry no peak angle
      clockT = 0;
      applyClock();
    }

    function buildScreen() {
      headRefs = renderHead({
        onModeChange: (mode) => { state.mode = mode; headRefs.sideBtn.setAttribute("aria-selected", mode === "side" ? "true" : "false"); headRefs.overlayBtn.setAttribute("aria-selected", mode === "overlay" ? "true" : "false"); mountAvatarsForMode(); renderLayout(); },
        // Hands the headline to Patient review in memory (never in the URL: clinical text would land in
        // history and a link could inject it); Patient review consumes it once and still needs Approve.
        onAddToNote: () => {
          const m = updateCardHeads();
          if (!m.headline) return;
          const patientId = state.patientJson.patient.id;
          ctx.store.set({ noteDraft: { patientId, text: m.headline } });
          ctx.navigate(href("patient", { pid: patientId }));
        },
      });
      controlsRefs = renderControls({
        onDateA: (sid) => { state.sidA = sid; stopClock(); reloadBothSessions(); },
        onDateB: (sid) => { state.sidB = sid; stopClock(); reloadBothSessions(); },
        onExercise: (exId) => { state.exerciseId = exId; reloadExercise(); },
        onView: (v) => {
          state.view = v;
          controlsRefs.frontBtn.setAttribute("aria-selected", v === "front" ? "true" : "false");
          controlsRefs.sideViewBtn.setAttribute("aria-selected", v === "side" ? "true" : "false");
          avatarA?.setView(v); avatarB?.setView(v); avatarOverlay?.setView(v);
        },
      });
      transportRefs = renderTransport({
        onPlayPause: () => {
          if (state.playing) { stopClock(); } else {
            if (clockT >= Math.max(durA, durB, 1)) clockT = 0;
            state.playing = true; lastTickAt = null;
            rafHandle = globalThis.requestAnimationFrame ? globalThis.requestAnimationFrame(tick) : null;
          }
          updateTransportUi();
        },
        onSpeed: (x) => {
          state.speed = x;
          transportRefs.halfBtn.setAttribute("aria-selected", x === 0.5 ? "true" : "false");
          transportRefs.oneBtn.setAttribute("aria-selected", x === 1 ? "true" : "false");
        },
      });
      cardA = renderSideCard();
      cardB = renderSideCard();
      overlayRefs = renderOverlayCard();
    }

    async function reloadExercise() {
      stopClock();
      const sessions = sessionsForExercise(state.exerciseId);
      if (sessions.length < 2) {
        bodyHost.replaceChildren(headRefs.el, controlsRefs.el, noExerciseSessions());
        return;
      }
      // Defaults: first = the earliest session, second = the latest (bug 12).
      state.sidA = sessions[0].id;
      state.sidB = sessions[sessions.length - 1].id;
      fillDateOptions(controlsRefs.dateASelect, sessions, state.sidA);
      fillDateOptions(controlsRefs.dateBSelect, sessions, state.sidB);
      controlsRefs.dateASelect.value = state.sidA;
      controlsRefs.dateBSelect.value = state.sidB;
      await reloadBothSessions();
    }

    // Sequenced: a slower, older date pick never overwrites a newer one (final review B, Minor 5).
    async function reloadBothSessions() {
      const mySeq = ++sessionsSeq;
      bodyHost.replaceChildren(headRefs.el, controlsRefs.el, loading("Loading sessions…"));
      try {
        const [sessA, sessB] = await Promise.all([
          ctx.api.session(state.sidA), ctx.api.session(state.sidB),
        ]);
        if (!alive || mySeq !== sessionsSeq) return;
        state.sessA = sessA;
        state.sessB = sessB;
        state.depthA = null;
        state.depthB = null;
        updateCardHeads();
        mountAvatarsForMode();
        renderLayout();
        await loadFrames(mySeq);
      } catch (err) {
        if (!alive || mySeq !== sessionsSeq) return;
        bodyHost.replaceChildren(headRefs.el, controlsRefs.el, errorState(err, reloadBothSessions));
      }
    }

    async function boot() {
      if (!pid) { bodyHost.replaceChildren(h("div", { class: "card" }, empty("No patient selected", ""))); return; }
      try {
        const patientRes = await ctx.api.patient(pid);
        if (!alive) return;
        state.patientJson = patientRes;
        ctx.store.set({ selectedPatientId: pid });
        await loadHistory();
        if (!alive) return;
        buildScreen();
        headRefs.metaEl.textContent = patientMeta(patientRes.patient);
        const exercises = exercisesFor();
        if (exercises.length === 0) {
          bodyHost.replaceChildren(headRefs.el, noExerciseSessions());
          return;
        }
        state.exerciseId = (state.history[0] && state.history[0].exercise_id) || exercises[0].id;
        fillExerciseOptions(controlsRefs.exerciseSelect, exercises, state.exerciseId);
        await reloadExercise();
      } catch (err) {
        if (!alive) return;
        bodyHost.replaceChildren(errorState(err, boot));
      }
    }

    boot();

    return () => {
      alive = false;
      stopClock();
      framesAbortA?.abort();
      framesAbortB?.abort();
      disposeAvatars();
    };
  },
};
