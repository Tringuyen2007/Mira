// Patient review screen mount (Figma 206:202): head, 3D replay/live room, rep timeline, This session, 3-week
// trend, Therapist Memory and Progress note cards. Persistent regions are built once; each card loads
// independently (Promise.allSettled) so a slow/failed progress fetch never blocks the room or timeline.
// Cleanup on unmount/route change: the player's rAF loop, the frames AbortController, and the live/pose
// socket subscriptions are the three things that leak if this isn't careful (self-review note in the brief).
import { h, clear } from "../../core/dom.js";
import { MESSAGE_MAX_CHARS } from "../../net/endpoints.js";
import { href } from "../../core/router.js";
import { firstName, fmtWhen, weeksSince } from "../../core/format.js";
import { createAvatarView } from "../../avatar/avatarView.js";
import { lerpJoints, isValidJoints } from "../../three/joints.js";
import { createPlayer, loadSessionFrames } from "./player.js";
import { sessionCardModel, repTimelineModel, trendModel, memoryModel, noteModel } from "./model.js";
import {
  renderHead, renderSessionPicker, renderRoomCard, setFlagCallout, renderSessionCard, renderTimelineCard,
  renderReps, renderTrendCard, renderMemoryCard, renderNoteCard, cardLoading, cardError, noSessions,
} from "./view.js";

const MESSAGES_CAP = "/patients/{patient_id}/messages";
const POSE_LERP_T = 0.4; // ~60ms-behind smoothing per frame (decisions: "lerp ~60 ms behind")
const SID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const PID_RE = /^[A-Za-z0-9_-]{1,64}$/;

// GET /patients/{id} doesn't expose first_session_at (only /caseload's server-computed `week` does, and that's
// a different route), so "week N" here is approximated from the patient's registration date (created_at)
// rather than their first session — close enough for the head's meta line, not used for any clinical logic.
function patientMeta(patient) {
  if (!patient) return "";
  const parts = [patient.name, patient.age != null ? String(patient.age) : null].filter(Boolean).join(", ");
  const week = patient.created_at != null ? `week ${Math.max(1, weeksSince(patient.created_at, new Date()) + 1)}` : null;
  return [parts, patient.condition, week].filter(Boolean).join(" · ");
}

function sessionTitle(session) {
  if (!session) return "—";
  const name = session.exercise_id ? session.exercise_id.replace(/_/g, " ") : "Session";
  const cap = name.charAt(0).toUpperCase() + name.slice(1);
  return `${cap}, ${fmtWhen(session.started_at, new Date())}`;
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    const state = {
      pid: PID_RE.test(route.params.pid || "") ? route.params.pid : null,
      sid: SID_RE.test(route.params.sid || "") ? route.params.sid : null,
      patientJson: null,
      sessionJson: null,
      mode: "replay",
      currentRep: null,
      noSessions: false,
      noteOverride: typeof route.query.note === "string" && route.query.note.trim() ? route.query.note.trim() : null,
    };
    let frames = [];
    let framesAbort = null;
    let player = null;
    let avatar = null;
    let pickerEl = null;
    let messagePopoverEl = null;
    let unsubLive = null;
    let unsubPose = null;

    const headRefs = renderHead({
      onModeChange: (mode) => setMode(mode),
      onPickSession: (btn) => togglePicker(btn),
      onMessage: (e) => toggleMessagePopover(e.currentTarget),
    });
    const roomRefs = renderRoomCard();
    const sessionHost = h("div", {});
    const timelineHost = h("div", {});
    const trendHost = h("div", {});
    const memoryHost = h("div", {});
    const noteHost = h("div", {});
    const mainEl = h("div", { class: "patient__main" }, roomRefs.el, timelineHost,
      h("div", { class: "patient__bottom-grid" }, trendHost, memoryHost, noteHost));
    const gridEl = h("div", { class: "patient__grid" }, mainEl, sessionHost);
    const bodyHost = h("div", {});
    root.append(headRefs.el, bodyHost);

    function showBody() {
      bodyHost.replaceChildren(gridEl);
    }

    function closePopovers() {
      pickerEl?.remove(); pickerEl = null;
      messagePopoverEl?.remove(); messagePopoverEl = null;
    }

    function onDocClick(e) {
      if (pickerEl && !pickerEl.contains(e.target) && e.target !== headRefs.sessionBtn
        && !headRefs.sessionBtn.contains(e.target)) { pickerEl.remove(); pickerEl = null; }
      if (messagePopoverEl && !messagePopoverEl.contains(e.target) && e.target !== headRefs.messageBtn
        && !headRefs.messageBtn.contains(e.target)) { messagePopoverEl.remove(); messagePopoverEl = null; }
    }
    document.addEventListener("click", onDocClick, true);

    function togglePicker(btn) {
      if (pickerEl) { pickerEl.remove(); pickerEl = null; return; }
      const sessions = ((state.patientJson && state.patientJson.recent_sessions) || []).slice(0, 10).map((s) => ({
        id: s.id, label: fmtWhen(s.started_at, new Date()),
        sub: `${s.reps_done != null ? s.reps_done : "—"} reps`,
      }));
      pickerEl = renderSessionPicker(sessions, {
        selectedId: state.sid,
        onPick: (sid) => { pickerEl.remove(); pickerEl = null; navigateToSession(sid); },
      });
      pickerEl.classList.add("patient__popover");
      btn.parentElement.append(pickerEl);
    }

    function toggleMessagePopover(btn) {
      if (messagePopoverEl) { messagePopoverEl.remove(); messagePopoverEl = null; return; }
      const name = firstName((state.patientJson && state.patientJson.patient && state.patientJson.patient.name) || "");
      const textarea = h("textarea", { class: "patient__message-textarea", rows: 3, maxlength: MESSAGE_MAX_CHARS,
        placeholder: `Message ${name}…`, "aria-label": `Message ${name}` });
      const errorEl = h("p", { class: "patient__note-error", role: "alert" });
      const sendBtn = h("button", { class: "btn btn--primary", type: "button", onclick: send }, "Send");
      messagePopoverEl = h("div", { class: "card patient__popover patient__message-popover" },
        textarea, errorEl, h("div", { class: "patient__popover-actions" }, sendBtn));
      btn.parentElement.append(messagePopoverEl);
      textarea.focus?.();
      async function send() {
        const text = textarea.value.trim();
        if (!text) { errorEl.textContent = "Write something first."; return; }
        if (text.length > MESSAGE_MAX_CHARS) {
          errorEl.textContent = `Keep it under ${MESSAGE_MAX_CHARS} characters.`; return;
        }
        sendBtn.setAttribute("disabled", "");
        try {
          await ctx.api.sendMessage(state.pid, text);
        } catch {
          if (!alive) return;
          sendBtn.removeAttribute("disabled");
          errorEl.textContent = "Couldn't send the message. Try again.";
          return;
        }
        if (!alive) return;
        messagePopoverEl?.remove(); messagePopoverEl = null;
        ctx.toast({ kind: "success", text: `Message sent to ${name}.` });
      }
    }

    function navigateToSession(sid) {
      ctx.navigate(href("replay", { sid }));
    }

    // ---- head/meta ----
    function renderHeadUi() {
      const patient = state.patientJson && state.patientJson.patient;
      headRefs.metaEl.textContent = patientMeta(patient);
      headRefs.titleEl.textContent = sessionTitle(state.sessionJson && state.sessionJson.session);
      headRefs.liveBtn.setAttribute("aria-selected", state.mode === "live" ? "true" : "false");
      headRefs.replayBtn.setAttribute("aria-selected", state.mode === "replay" ? "true" : "false");
      headRefs.sessionLabelEl.textContent = sessionTitle(state.sessionJson && state.sessionJson.session)
        .replace(/^[A-Z][a-z ]*, /, "");
      const canMessage = Boolean(ctx.caps && ctx.caps.has && ctx.caps.has(MESSAGES_CAP));
      headRefs.messageBtn.hidden = !canMessage;
      if (canMessage && patient) headRefs.messageBtn.textContent = `Message ${firstName(patient.name)}`;
    }

    // ---- mode (live/replay) ----
    function setMode(mode) {
      if (mode === state.mode) return;
      state.mode = mode;
      renderHeadUi();
      if (mode === "live") startLive(); else stopLive();
      if (mode === "replay") mountReplay();
    }

    function stopLive() {
      unsubLive?.(); unsubLive = null;
      unsubPose?.(); unsubPose = null;
    }

    function startLive() {
      player?.pause();
      let lastJoints = null; // for the lerp-toward-latest smoothing below
      unsubLive = ctx.sockets.subscribe("live", (msg) => {
        if (!alive || state.mode !== "live" || !msg) return;
        const patientName = state.patientJson && state.patientJson.patient && state.patientJson.patient.name;
        if (firstName(patientName) && msg.first_name && firstName(patientName) !== msg.first_name) return;
        roomRefs.repPillEl.hidden = msg.rep == null;
        if (msg.rep != null) roomRefs.repPillEl.textContent = `Live · set ${msg.set} · rep ${msg.rep}`;
        setFlagCallout(roomRefs.flagCalloutEl,
          Array.isArray(msg.flags) && msg.flags.length ? "Flagged this rep" : null);
      });
      // Smoothed a little behind the raw stream (decisions: "lerp ~60 ms behind") so a dropped or noisy frame
      // doesn't make the pills jump; the placeholder itself only repaints at ~10 Hz on top of this.
      unsubPose = ctx.sockets.subscribe("pose", (msg) => {
        if (!alive || state.mode !== "live" || !msg) return;
        const eased = lerpJoints(lastJoints, msg.joints || null, POSE_LERP_T);
        lastJoints = isValidJoints(eased) ? eased : (msg.joints || null);
        avatar?.setFrame({ joints: lastJoints, load_l_pct: msg.load_l_pct, flags: msg.flags || [], t: msg.t });
      });
    }

    // ---- replay ----
    async function mountReplay() {
      if (!state.sid || framesAbort) return;
      framesAbort = new AbortController();
      const sessionIso = state.sessionJson && state.sessionJson.session && state.sessionJson.session.started_at;
      try {
        frames = await loadSessionFrames(ctx.api.framesPage, state.sid, sessionIso, { signal: framesAbort.signal });
      } catch {
        frames = [];
      }
      if (!alive || state.mode !== "replay") return;
      const reps = repTimelineModel(state.sessionJson);
      player?.dispose();
      player = createPlayer({
        frames, reps,
        onFrame: (frame) => {
          avatar?.setFrame({ joints: frame.joints, load_l_pct: frame.load_l_pct, flags: currentFlags(reps), t: frame.time });
          updateTimelineUi();
        },
      });
      player.seekTime(0); // shows the first frame's pills/balance bar immediately, before any Play click
      updateTimelineUi();
    }

    function currentFlags(reps) {
      const st = player ? player.state() : { rep: null };
      const rep = reps.find((r) => r.rep === st.rep);
      return (rep && rep.flags) || [];
    }

    // ---- timeline card ----
    let timelineRefs = null;
    function buildTimeline() {
      timelineRefs = renderTimelineCard({
        onPlayPause: () => { if (!player) return; if (player.state().playing) player.pause(); else player.play(); updateTimelineUi(); },
        onRepClick: (n) => { player?.seekRep(n); updateTimelineUi(); },
      });
      timelineHost.replaceChildren(timelineRefs.el);
      timelineRefs.el.setAttribute("tabindex", "0");
      timelineRefs.el.addEventListener("keydown", onTimelineKey);
    }

    function onTimelineKey(e) {
      if (!player) return;
      const reps = repTimelineModel(state.sessionJson);
      const st = player.state();
      const idx = reps.findIndex((r) => r.rep === st.rep);
      if (e.key === " ") { e.preventDefault(); if (st.playing) player.pause(); else player.play(); updateTimelineUi(); }
      else if (e.key === "ArrowRight" && idx >= 0 && idx < reps.length - 1) { player.seekRep(reps[idx + 1].rep); updateTimelineUi(); }
      else if (e.key === "ArrowLeft" && idx > 0) { player.seekRep(reps[idx - 1].rep); updateTimelineUi(); }
    }

    function updateTimelineUi() {
      if (!timelineRefs || !player) return;
      const st = player.state();
      timelineRefs.playBtn.setAttribute("aria-label", st.playing ? "Pause" : "Play");
      timelineRefs.timeEl.textContent = `${fmtClockLocal(st.t)} / ${fmtClockLocal(st.duration)}`;
      const reps = repTimelineModel(state.sessionJson);
      renderReps(timelineRefs.repsHost, timelineRefs.repBlock, reps, st.rep);
      if (reps.length) {
        roomRefs.repPillEl.hidden = false;
        roomRefs.repPillEl.textContent = `Replaying rep ${st.rep} of ${reps.length}`;
      }
      const rep = reps.find((r) => r.rep === st.rep);
      setFlagCallout(roomRefs.flagCalloutEl, rep && rep.flagged ? flagCalloutText(rep) : null);
    }

    function flagCalloutText(rep) {
      if (rep.flags.includes("weight_shift")) return "Weight shift — more weight shifted off the affected leg this rep.";
      if (rep.flags.some((f) => f.startsWith("knee_cave"))) return "Knee drifting inward this rep.";
      if (rep.flags.includes("trunk_lean")) return "Trunk leaning more than usual this rep.";
      return "Flagged rep.";
    }

    function fmtClockLocal(ms) {
      const s = Math.max(0, Math.round((Number(ms) || 0) / 1000));
      const m = Math.floor(s / 60);
      const r = s % 60;
      return `${m}:${String(r).padStart(2, "0")}`;
    }

    // ---- independent cards ----
    async function loadSessionCard() {
      sessionHost.replaceChildren(cardLoading("This session"));
      try {
        const m = sessionCardModel(state.sessionJson, state.patientJson);
        if (!alive) return;
        sessionHost.replaceChildren(renderSessionCard(m));
      } catch (err) {
        if (alive) sessionHost.replaceChildren(cardError("This session", err, loadSessionCard));
      }
    }

    async function loadTrendCard() {
      trendHost.replaceChildren(cardLoading("3-week trend"));
      try {
        const patient = state.patientJson.patient;
        const res = await ctx.api.progress(state.pid, { exerciseId: state.sessionJson.session.exercise_id, days: 21 });
        if (!alive) return;
        const m = trendModel(res, patient.affected_side, new Date(), 21);
        trendHost.replaceChildren(renderTrendCard(m));
      } catch (err) {
        if (alive) trendHost.replaceChildren(cardError("3-week trend", err, loadTrendCard));
      }
    }

    async function loadMemoryCard() {
      memoryHost.replaceChildren(cardLoading("Therapist Memory"));
      try {
        const res = await ctx.api.memory(state.pid, 21);
        if (!alive) return;
        const patient = state.patientJson.patient;
        const lastVisit = patient.next_visit_at || null;
        const m = memoryModel(res, lastVisit);
        memoryHost.replaceChildren(renderMemoryCard(m));
      } catch (err) {
        if (alive) memoryHost.replaceChildren(cardError("Therapist Memory", err, loadMemoryCard));
      }
    }

    async function loadNoteCard() {
      noteHost.replaceChildren(cardLoading("Progress note"));
      let approval = null;
      try {
        approval = await ctx.api.progressNote(state.sid);
      } catch { /* no approval yet, or the route 404s before one exists */ }
      if (!alive) return;
      buildNoteCard(approval);
    }

    function buildNoteCard(approval) {
      let m = noteModel(state.sessionJson, approval);
      // Recovery time-lapse's "Add to progress note" navigates here with ?note=<headline>; prefilled into the
      // AI draft (still unapproved — the therapist still has to click Approve, decisions: "the therapist still
      // approves it"). Never applied on top of an already-approved note.
      if (state.noteOverride && !(approval && approval.approved)) {
        const text = [m.text, state.noteOverride].filter((s) => s && s.trim()).join("\n\n");
        m = { ...m, text, canApprove: text.trim().length > 0 };
      }
      const refs = renderNoteCard(m, {
        onEdit: () => {},
        onApprove: async (text) => {
          await ctx.api.approveProgressNote(state.sid, text);
          if (!alive) return;
          const when = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(new Date());
          refs.showApproved(text, when);
          ctx.toast({ kind: "success", text: "Progress note approved." });
        },
      });
      noteHost.replaceChildren(refs.el);
    }

    function loadCards() {
      loadSessionCard();
      loadTrendCard();
      loadMemoryCard();
      loadNoteCard();
    }

    // ---- boot ----
    async function boot() {
      try {
        let pid = state.pid;
        let sid = state.sid;
        if (sid) {
          const sessionRes = await ctx.api.session(sid);
          if (!alive) return;
          state.sessionJson = sessionRes;
          pid = sessionRes.session.patient_id;
        }
        const patientRes = await ctx.api.patient(pid);
        if (!alive) return;
        state.pid = pid;
        state.patientJson = patientRes;
        ctx.store.set({ selectedPatientId: pid });
        if (!sid) {
          const recent = (patientRes.recent_sessions || []);
          if (recent.length === 0) {
            state.noSessions = true;
            bodyHost.replaceChildren(noSessions());
            renderHeadUi();
            return;
          }
          sid = recent[0].id;
          const sessionRes = await ctx.api.session(sid);
          if (!alive) return;
          state.sessionJson = sessionRes;
        }
        state.sid = sid;
        renderHeadUi();
        showBody();
        buildTimeline();
        avatar = createAvatarView(roomRefs.avatarHost, {
          mode: state.mode, affectedSide: (state.patientJson.patient && state.patientJson.patient.affected_side) || "right",
          ghost: false,
        });
        loadCards();
        await mountReplay();
      } catch (err) {
        if (!alive) return;
        bodyHost.replaceChildren(cardError("Patient review", err, boot));
      }
    }

    boot();

    return () => {
      alive = false;
      document.removeEventListener("click", onDocClick, true);
      stopLive();
      framesAbort?.abort();
      player?.dispose();
      avatar?.dispose();
      closePopovers();
    };
  },
};
