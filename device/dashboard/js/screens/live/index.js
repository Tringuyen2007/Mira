// Live session screen mount (Figma 210:820): watch a patient's session in real time from anywhere, on the
// avatar placeholder (createAvatarView, live mode). `#/live` with no pid shows whoever GET /caseload's
// `today.live_now` says is live right now; `#/live/:pid` shows that patient, or an empty state if they aren't
// in a session. Sockets (live/pose/events) are opened only once we know someone's actually live, and are
// always closed on unmount (self-review note in the brief: this is the leak class to watch for).
import { h, clear } from "../../core/dom.js";
import { href } from "../../core/router.js";
import { firstName, patientMeta } from "../../core/format.js";
import { errorState } from "../../ui/states.js";
import { createAvatarView } from "../../avatar/avatarView.js";
import { attachWhep } from "../../media/whep.js";
import { lerpJoints, isValidJoints } from "../../three/joints.js";
import { liveModel, cueHistoryModel } from "./model.js";
import {
  renderHead, renderRoomCard, renderCuePanel, renderCuePanelDisabled, renderCueHistory,
  renderCueHistoryList, emptyNoLiveSession, emptyNoOneLive,
} from "./view.js";

const SAY_CAP = "/live/say";
const POSE_LERP_T = 0.4; // same smoothing budget as the Patient review screen's live mode

const IDLE_PHASES = new Set(["idle", "done"]);
const RECHECK_MIN_MS = 5000; // /ws/live pushes ~10 Hz: re-check the caseload at most this often while waiting

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    let unsubLive = null;
    let unsubPose = null;
    let unsubEvents = null;
    let cueEvents = [];
    let lastSnapshot = null;
    let lastReceivedAt = null;
    let lastJoints = null;
    let unsubWatch = null; // while nobody is live: re-check on the first live snapshot
    let stopVideo = null;
    let lastRecheckAt = 0;

    const requestedPid = /^[A-Za-z0-9_-]{1,64}$/.test(route.params.pid || "") ? route.params.pid : null;
    const bodyHost = h("div", {});
    root.append(bodyHost);

    function showEmptyNoOneLive() {
      bodyHost.replaceChildren(emptyNoOneLive(href("caseload")));
      watchForSession(null);
    }

    function showEmptyForPatient(patient) {
      bodyHost.replaceChildren(emptyNoLiveSession(firstName(patient && patient.name), href("patient", { pid: patient.id })));
      watchForSession(patient);
    }

    // The empty states promise "they'll show up here the moment a session starts" (final review B, I11): listen
    // to /ws/live and re-run boot() on the first active snapshot (for the requested patient, if there is one).
    function watchForSession(waitingFor) {
      if (unsubWatch) return;
      const wantFirst = waitingFor ? firstName(waitingFor.name) : null;
      unsubWatch = ctx.sockets.subscribe("live", (msg) => {
        if (!alive || !msg || IDLE_PHASES.has(msg.phase)) return;
        if (wantFirst && msg.first_name && msg.first_name !== wantFirst) return;
        if (Date.now() - lastRecheckAt < RECHECK_MIN_MS) return;
        lastRecheckAt = Date.now();
        unsubWatch?.(); unsubWatch = null;
        boot();
      });
    }

    let headRefs = null;
    let roomRefs = null;
    let cueHistoryRefs = null;
    let avatar = null;
    let pid = null;
    let patient = null;

    function mountLiveUi() {
      headRefs = renderHead({
        onReplay: () => ctx.navigate(href("patient", { pid })),
        onLeave: () => ctx.navigate(href("patient", { pid })),
      });
      roomRefs = renderRoomCard();
      const canSay = Boolean(ctx.caps && ctx.caps.has && ctx.caps.has(SAY_CAP));
      const cuePanelEl = canSay
        ? renderCuePanel({ onSend: (text) => ctx.api.sayLive(pid, text) }).el
        : renderCuePanelDisabled();
      cueHistoryRefs = renderCueHistory();
      const sideHost = h("div", { class: "live__side" }, cuePanelEl, cueHistoryRefs.el);
      const gridEl = h("div", { class: "live__grid" }, roomRefs.el, sideHost);
      bodyHost.replaceChildren(headRefs.el, gridEl);

      headRefs.metaEl.textContent = patientMeta(patient);
      headRefs.subtitleEl.textContent = patient
        ? `You see ${firstName(patient.name)}'s live 3D twin from anywhere. Type a cue and Mira says it in her voice.`
        : "";

      avatar = createAvatarView(roomRefs.avatarHost, {
        mode: "live", affectedSide: (patient && patient.affected_side) || "right", ghost: false,
      });
      renderCueHistoryList(cueHistoryRefs.listHost, cueHistoryModel(cueEvents));
      applySnapshot(lastSnapshot, lastReceivedAt);
      startVideo();
    }

    async function startVideo() {
      try {
        const media = await ctx.api.liveMedia();
        if (!alive || !roomRefs || !media?.whep_url) return;
        stopVideo = await attachWhep(roomRefs.videoEl, media.whep_url, ctx.auth.token);
        if (roomRefs) roomRefs.videoStatusEl.textContent = "Live camera · synchronized with the 3D body view";
      } catch {
        if (roomRefs) roomRefs.videoStatusEl.textContent = "Camera video is not connected yet.";
      }
    }

    // `receivedAt`: when this snapshot arrived, so the latency is server time vs receive time.
    function applySnapshot(snapshot, receivedAt) {
      if (!roomRefs) return;
      const m = liveModel(snapshot, patient, receivedAt || new Date());
      roomRefs.latencyEl.textContent = m.latency || "";
      roomRefs.repCardEl.hidden = !m.rep;
      if (m.rep) {
        roomRefs.repBigEl.textContent = m.rep;
        roomRefs.repLineEl.textContent = m.line || "";
      }
      roomRefs.hrChipEl.hidden = !m.hr;
      if (m.hr) roomRefs.hrChipEl.textContent = m.hr;
      roomRefs.pausedEl.hidden = !m.paused;
    }

    function subscribeSockets() {
      unsubLive = ctx.sockets.subscribe("live", (msg) => {
        if (!alive || !msg) return;
        if (patient && firstName(patient.name) && msg.first_name && firstName(patient.name) !== msg.first_name) return;
        lastSnapshot = msg;
        lastReceivedAt = new Date();
        applySnapshot(msg, lastReceivedAt);
      });
      unsubPose = ctx.sockets.subscribe("pose", (msg) => {
        if (!alive || !msg) return;
        const eased = lerpJoints(lastJoints, msg.joints || null, POSE_LERP_T);
        lastJoints = isValidJoints(eased) ? eased : (msg.joints || null);
        avatar?.setFrame({ joints: lastJoints, load_l_pct: msg.load_l_pct, flags: msg.flags || [], t: msg.t });
        if (roomRefs && isValidJoints(lastJoints)) roomRefs.poseStatusEl.textContent = "Private pose stream connected · no camera video is shared.";
      });
      unsubEvents = ctx.sockets.subscribe("events", (msg) => {
        if (!alive || !msg || msg.type !== "cue") return;
        cueEvents = [...cueEvents, msg];
        if (cueHistoryRefs) renderCueHistoryList(cueHistoryRefs.listHost, cueHistoryModel(cueEvents));
      });
    }

    function stopSockets() {
      unsubLive?.(); unsubLive = null;
      unsubPose?.(); unsubPose = null;
      unsubEvents?.(); unsubEvents = null;
    }

    async function boot() {
      try {
        const caseloadRes = await ctx.api.caseload();
        if (!alive) return;
        const liveNow = (caseloadRes && caseloadRes.today && caseloadRes.today.live_now) || null;

        pid = requestedPid || (liveNow && liveNow.patient_id) || null;
        if (!pid) { showEmptyNoOneLive(); return; }

        if (!liveNow || liveNow.patient_id !== pid) {
          // Known (from the caseload snapshot) not to be live right now.
          const patientRes = await ctx.api.patient(pid);
          if (!alive) return;
          showEmptyForPatient(patientRes.patient);
          return;
        }

        const patientRes = await ctx.api.patient(pid);
        if (!alive) return;
        patient = patientRes.patient;
        ctx.store.set({ selectedPatientId: pid, live: { pid } });
        mountLiveUi();
        subscribeSockets();
      } catch (err) {
        if (!alive) return;
        // A real failure (401, 503, offline) is an error with Retry, not "no one is live" (final review B, I11).
        bodyHost.replaceChildren(h("div", { class: "card" }, errorState(err, () => boot())));
      }
    }

    boot();

    return () => {
      alive = false;
      unsubWatch?.(); unsubWatch = null;
      stopSockets();
      stopVideo?.(); stopVideo = null;
      avatar?.dispose();
    };
  },
};
