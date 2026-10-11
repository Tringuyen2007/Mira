// Pure state for the LCD: reduce(state, {channel, msg}, now) → state; view(state, now) → what to draw.
// No DOM, no timers, so it runs under `node --test`.

export const CUE_WINDOW_MS = 4000;    // a correction cue keeps the Cue screen (worried Mira) this long
export const CHEER_MS = 1500;         // Mira cheers this long after a good rep
export const OFFLINE_AFTER_MS = 3000; // a dropped socket shows "Reconnecting…" only after this grace period
export const BALANCE_LIMIT = 15;      // |left% − 50| above this is "off balance" (VICTORIA.md)
export const SESSION_DONE_MS = 60000; // the whole-session summary (sessionDone) keeps showing this long

const NON_CORRECTION_CUES = new Set(["set_start", "check_in", "im_ok_thanks", "rest", "set_done", "fatigue_rest"]);
const CLOSED_ALERT_STEPS = new Set(["resolved", "closed", "resolved_by_therapist"]);
// Alert steps that mean "no call is going on" (the call status line is hidden).
const NO_CALL_STEPS = new Set(["detected", "checked_in", ...CLOSED_ALERT_STEPS]);
// Spec §4: which call phase each alert step narrates on the fall screen.
const CALL_PHASES = new Map(Object.entries({
  escalated: "calling", call_registered: "calling", call_retry: "calling",
  call_ongoing: "on_call", call_update: "on_call", patient_responded: "on_call",
  call_ended: "ended", acknowledged: "ended",
  call_not_connected: "failed", call_error: "failed", call_failed: "failed", call_watch_timeout: "failed",
}));
const NO_ALERT = { alertStep: null, alertMessage: "", show911: false };

export function initialState() {
  return {
    live: null,
    arrived: false,
    awaitingGesture: false,
    lastCorrectionAt: -Infinity,
    lastGoodRepAt: -Infinity,
    alertOpen: false,
    socket: "connecting",
    closedAt: null,
    summary: null,
    speaking: false,
    ...NO_ALERT,
    closedAlertId: null,
  };
}

// Stores the alert step that drives the fall screen's call status line. Steps that name no call state
// (therapist_call, webhook_*) keep the last one; steps for an alert that already closed, or that land when no
// incident is showing, are ignored so a late Retell webhook can't put a stale line on the next fall.
function alertCall(state, msg) {
  const id = typeof msg.alert_id === "string" ? msg.alert_id : null;
  if (CLOSED_ALERT_STEPS.has(msg.step)) return { ...state, ...NO_ALERT, closedAlertId: id };
  if (msg.step === "detected") return { ...state, ...NO_ALERT, alertStep: "detected" };
  if (!NO_CALL_STEPS.has(msg.step) && !CALL_PHASES.has(msg.step)) return state;
  if (id !== null && id === state.closedAlertId) return state;
  const live = state.live ?? {};
  if (!state.alertOpen && live.incident !== true && live.phase !== "incident") return state;
  return { ...state, alertStep: msg.step, alertMessage: typeof msg.message === "string" ? msg.message : "",
    show911: msg.show_call_911 === true };
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
  if (channel === "speaking") return { ...state, speaking: msg.on === true };
  if (channel === "live") {
    let next = { ...state, live: msg };
    if (msg.phase === "countdown" || msg.phase === "active") next = { ...next, awaitingGesture: false };
    // The live snapshot is authoritative: once it reports no incident, the alert the kiosk showed early
    // (from a fall/check_in event, ahead of the next 10 Hz snapshot) is stale and must not stick.
    // Its call status goes too, so a missed `closed` alert can't leave a stale line on the next fall.
    if (msg.incident !== true && msg.phase !== "incident") next = { ...next, alertOpen: false, ...NO_ALERT };
    return next;
  }
  switch (msg.type) {
    case "arrived":
      // A session boundary: nothing carries over to the new patient/session.
      return { ...state, arrived: true, awaitingGesture: false, alertOpen: false,
        lastCorrectionAt: -Infinity, lastGoodRepAt: -Infinity, summary: null, ...NO_ALERT };
    case "greeting":
      return { ...state, arrived: true };
    case "awaiting_gesture":
      return { ...state, arrived: true, awaitingGesture: true };
    case "gesture":
      return { ...state, awaitingGesture: false };
    case "cue": {
      const correction = !NON_CORRECTION_CUES.has(msg.id);
      return { ...state, lastCorrectionAt: correction ? now : state.lastCorrectionAt,
        alertOpen: msg.id === "check_in" ? true : state.alertOpen };
    }
    case "rep":
      return msg.quality === "good" ? { ...state, lastGoodRepAt: now } : state;
    case "fall":
      // Only a confirmed fall is an incident (as for the SafetyGuardian); a suspected one is not.
      return msg.stage === "confirmed" ? { ...state, alertOpen: true } : state;
    case "alert": {
      // `detected` opens and a closing step ends it; anything else (escalated, Retell call_*/webhook_*
      // steps that land after the close) leaves it as it is.
      const next = alertCall(state, msg);
      if (msg.step === "detected") return { ...next, alertOpen: true };
      if (CLOSED_ALERT_STEPS.has(msg.step)) return { ...next, alertOpen: false };
      return next;
    }
    case "stopped":
      return { ...state, awaitingGesture: false };
    case "session_summary":
      return { ...state, summary: { msg, at: now } };
    default:
      return state;
  }
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
  const caption = live.caption && typeof live.caption.text === "string" ? live.caption.text : "";
  const devicePrefs = live.device_prefs && typeof live.device_prefs === "object" ? live.device_prefs : {};
  const base = {
    captionsOn: devicePrefs.captions !== false,
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
    // live.caption is the server's own derived line (≤100 ms behind the greeting/cue/summary_ready event
    // that set it); taking it straight from the snapshot means a kiosk that (re)joins mid-session gets it
    // repainted on the very next live message, with no separate event-caption state to go stale.
    caption,
    summary: null,
    sessionSummary: null,
    speaking: state.speaking === true,
    callStatus: CALL_PHASES.has(state.alertStep)
      ? { phase: CALL_PHASES.get(state.alertStep), message: state.alertMessage } : null,
    show911: state.show911 === true,
  };

  if (state.socket === "rejected") return { ...base, screen: "unpaired", mood: null };
  if (live.incident || phase === "incident" || state.alertOpen) return { ...base, screen: "fall", mood: null };
  if (live.paused || phase === "paused") return { ...base, screen: "paused", mood: "sleeping" };
  if (phase === "countdown") return { ...base, screen: "countdown", mood: null };
  if (state.summary && now - state.summary.at <= SESSION_DONE_MS &&
      (phase === "done" || phase === "idle" || phase === "rest")) {
    const m = state.summary.msg;
    return { ...base, screen: "sessionDone", mood: "celebrating", sessionSummary: {
      formScore: num(m.form_score),
      goodReps: num(m.good_reps),
      repsTotal: num(m.reps_total),
      spoken: typeof m.spoken === "string" ? m.spoken : "",
    } };
  }
  if (phase === "rest" || phase === "done") {
    const summary = { reps: base.rep, repsTarget, balance: balance
      ? `${balance.leftPct} / ${100 - balance.leftPct}` : null, balanceOk: balance ? balance.ok : null };
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
