// Pure state for the LCD: reduce(state, {channel, msg}, now) → state; view(state, now) → what to draw.
// No DOM, no timers, so it runs under `node --test`.

export const CUE_WINDOW_MS = 4000;    // a correction cue keeps the Cue screen (worried Mira) this long
export const CHEER_MS = 1500;         // Mira cheers this long after a good rep
export const OFFLINE_AFTER_MS = 3000; // a dropped socket shows "Reconnecting…" only after this grace period
export const BALANCE_LIMIT = 15;      // |left% − 50| above this is "off balance" (VICTORIA.md)

const NON_CORRECTION_CUES = new Set(["set_start", "check_in", "im_ok_thanks", "rest", "set_done", "fatigue_rest"]);
const CLOSED_ALERT_STEPS = new Set(["resolved", "closed", "resolved_by_therapist"]);

export function initialState() {
  return {
    live: null,
    arrived: false,
    awaitingGesture: false,
    lastCorrectionAt: -Infinity,
    lastGoodRepAt: -Infinity,
    alertOpen: false,
    eventCaption: "",
    socket: "connecting",
    closedAt: null,
  };
}

export function reduce(state, input, now) {
  const { channel, msg } = input;
  if (!msg || typeof msg !== "object") return state;
  if (channel === "socket") {
    if (msg.status === "open") return { ...state, socket: "open", closedAt: null };
    if (msg.status === "rejected") return { ...state, socket: "rejected" };
    if (msg.status === "closed") return { ...state, socket: "closed", closedAt: state.closedAt ?? now };
    return state;
  }
  if (channel === "live") {
    let next = { ...state, live: msg };
    if (msg.phase === "countdown" || msg.phase === "active") next = { ...next, awaitingGesture: false };
    return next;
  }
  switch (msg.type) {
    case "arrived":
      return { ...state, arrived: true, awaitingGesture: false, alertOpen: false, eventCaption: "" };
    case "greeting":
      return { ...state, arrived: true, eventCaption: textOf(msg.text, state.eventCaption) };
    case "awaiting_gesture":
      return { ...state, arrived: true, awaitingGesture: true };
    case "gesture":
      return { ...state, awaitingGesture: false };
    case "cue": {
      const correction = !NON_CORRECTION_CUES.has(msg.id);
      return { ...state, eventCaption: textOf(msg.text, state.eventCaption),
        lastCorrectionAt: correction ? now : state.lastCorrectionAt,
        alertOpen: msg.id === "check_in" ? true : state.alertOpen };
    }
    case "rep":
      return msg.quality === "good" ? { ...state, lastGoodRepAt: now } : state;
    case "summary_ready":
      return { ...state, eventCaption: textOf(msg.spoken, state.eventCaption) };
    case "fall":
      return { ...state, alertOpen: true };
    case "alert":
      return { ...state, alertOpen: !CLOSED_ALERT_STEPS.has(msg.step) };
    case "stopped":
      return { ...state, awaitingGesture: false };
    default:
      return state;
  }
}

function textOf(value, fallback) {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function view(state, now) {
  const live = state.live ?? {};
  const phase = live.phase ?? "idle";
  const repsTarget = num(live.reps_target);
  const leftPct = num(live.load_l_pct);
  const balance = leftPct === null ? null
    : { leftPct: Math.round(leftPct), ok: Math.abs(leftPct - 50) <= BALANCE_LIMIT };
  const set = num(live.set);
  const setsTarget = num(live.sets_target);
  const liveCaption = live.caption && typeof live.caption.text === "string" ? live.caption.text : "";
  const base = {
    offline: state.socket === "closed" && state.closedAt !== null && now - state.closedAt > OFFLINE_AFTER_MS,
    setLabel: set !== null && setsTarget !== null ? `SET ${set} OF ${setsTarget}` : null,
    hr: num(live.hr),
    firstName: typeof live.first_name === "string" ? live.first_name : "",
    exerciseName: typeof live.exercise_name === "string" ? live.exercise_name : "",
    dose: setsTarget !== null && repsTarget !== null ? `${setsTarget} sets · ${repsTarget} reps` : "",
    rep: num(live.rep) ?? 0,
    repsTarget,
    countdown: num(live.countdown),
    balance,
    // The newest line wins: an event caption is at least as new as the 10 Hz snapshot; live.caption repaints
    // a kiosk that (re)joined mid-session before any event arrived.
    caption: state.eventCaption || liveCaption,
    summary: null,
  };

  if (state.socket === "rejected") return { ...base, screen: "unpaired", mood: null };
  if (live.incident || phase === "incident" || state.alertOpen) return { ...base, screen: "fall", mood: null };
  if (live.paused || phase === "paused") return { ...base, screen: "paused", mood: "sleeping" };
  if (phase === "countdown") return { ...base, screen: "countdown", mood: null };
  if (phase === "rest" || phase === "done") {
    const summary = { reps: base.rep, repsTarget, balance: balance
      ? `${balance.leftPct} / ${100 - balance.leftPct}` : null };
    return { ...base, screen: "setDone", mood: "celebrating", summary };
  }
  if (state.awaitingGesture) return { ...base, screen: "gesture", mood: "idle" };
  if (phase === "active") {
    const correcting = now - state.lastCorrectionAt <= CUE_WINDOW_MS || (balance !== null && !balance.ok);
    if (correcting) return { ...base, screen: "cue", mood: "worried" };
    const cheering = now - state.lastGoodRepAt <= CHEER_MS;
    return { ...base, screen: "rep", mood: cheering ? "cheering" : "idle" };
  }
  if (state.arrived) return { ...base, screen: "greeting", mood: "idle" };
  return { ...base, screen: "idle", mood: "idle" };
}
