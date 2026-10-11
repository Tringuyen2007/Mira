// Mira Assistant screen mount. Persistent regions (head, chat log, composer, rail) are built once; answers are
// appended to the log, and the proposals rail reuses each card element while its proposal is unchanged, so neither
// the question <textarea> nor an open proposal edit form is ever rebuilt under the therapist's fingers.
// The conversation lives only in this closure (never in storage: it carries patient data) and dies with unmount,
// which main.js runs on every route change and on sign-out.
import { h } from "../../core/dom.js";
import { MESSAGE_MAX_CHARS } from "../../net/endpoints.js";
import { href } from "../../core/router.js";
import { proposalCard } from "../../ui/proposalCard.js";
import { answerModel, contextFrom, proposalsTabsModel } from "./model.js";
import {
  renderHead, patientOptions, renderComposer, renderIntro, renderQuestion, renderAnswer, renderError,
  renderNoteEditor, renderNoteApproved, renderRailTabs, renderRailEmpty, MAX_QUESTION,
} from "./view.js";

const PROPOSAL_EVENTS = new Set(["proposal_created", "proposal_decided"]);
const REFRESH_DEBOUNCE_MS = 800;
const LEAVE_MS = 300;
const PID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const STATUS_FOR_BUCKET = { needs_you: "needs", watch: "watch", on_track: "ok" };
const MESSAGES_CAP = "/patients/{patient_id}/messages";
const NO_SESSIONS = "No sessions yet for this patient, so there is nothing to attach a note to.";

function reducedMotion() {
  try { return Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches); } catch { return false; }
}

function askErrorText(err) {
  const status = err && err.status;
  if (status === 0 && err.detail === "timeout") {
    return "Mira Assistant took longer than 30 seconds. Try a narrower question.";
  }
  if (status === 0) return "Can't reach Mira's server. Check the connection and try again.";
  if (status === 503) return "Mira Assistant needs the database to answer.";
  if (status === 404) return "That patient wasn't found.";
  if (status === 422) return `That question couldn't be sent. Keep it under ${MAX_QUESTION} characters.`;
  return (err && err.detail) || "Something went wrong.";
}

// Replaces the URL's query (?patient=) without firing hashchange, so main.js doesn't remount the screen.
function syncPatientQuery(pid) {
  if (typeof globalThis.history === "undefined") return;
  globalThis.history.replaceState(null, "", href("assistant", {}, pid ? { patient: pid } : {}));
}

export const screen = {
  mount(root, ctx, route) {
    let alive = true;
    const query = (route && route.query) || {};
    const state = {
      patients: [],
      patientId: PID_RE.test(query.patient || "") ? query.patient : null,
      waiting: false,
      turns: [], // {question, status, answerText}
      proposals: [],
      proposalsLoaded: false,
      tab: "pending",
      highlightId: route && route.name === "proposal" ? Number(route.params.id) : null, // to apply after load
      highlightedId: null, // the card currently highlighted (kept across card rebuilds)
    };
    const details = new Map(); // pid -> Promise<{latestSessionId: string | null}>
    const resolvedDetails = new Map(); // pid -> {latestSessionId} once loaded (to render the note button disabled)
    const cards = new Map(); // proposal id -> {el, key}
    let rxByPatient = new Map(); // pid -> active prescriptions
    let proposalsSeq = 0;
    let refreshTimer = null;
    const timers = new Set();
    const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };

    // ---- regions ----
    const head = renderHead({ onPatientChange, onReview });
    const logEl = h("div", { class: "assistant__log", role: "log", "aria-label": "Conversation" });
    const introEl = renderIntro();
    logEl.append(introEl);
    const composer = renderComposer({ onSend: () => send(composer.textarea.value), onSuggestion: (q) => send(q),
      onInput });
    const railTabsHost = h("div", {});
    const railListHost = h("div", { class: "assistant__cards" });
    const rail = h("section", { class: "assistant__rail", "aria-labelledby": "assistant-proposals-title" },
      h("div", { class: "assistant__rail-head" },
        h("h2", { id: "assistant-proposals-title", class: "card__title" }, "Proposals"),
        railTabsHost),
      railListHost);
    root.append(h("div", { class: "assistant" },
      head.el,
      h("div", { class: "assistant__grid" },
        h("section", { class: "card assistant__chat", "aria-label": "Chat with Mira Assistant" }, logEl, composer.el),
        rail)));

    // ---- patients ----
    function patientName(pid) {
      const p = state.patients.find((x) => x.id === pid);
      return p ? p.name : pid;
    }

    function syncPatientUi() {
      const pid = state.patientId;
      head.reviewBtn.hidden = !pid;
      head.eyebrowEl.textContent = pid ? `Asking about ${patientName(pid)}` : "Across your caseload";
      composer.textarea.setAttribute("placeholder",
        pid ? `Ask Mira Assistant about ${patientName(pid)}…` : "Ask Mira Assistant about a patient…");
    }

    function ensureDetail(pid) {
      if (!details.has(pid)) {
        const p = ctx.api.patient(pid).then((d) => {
          const recent = (d && Array.isArray(d.recent_sessions)) ? d.recent_sessions : [];
          const detail = { latestSessionId: recent.length ? recent[0].id : null }; // newest first (started_at DESC)
          resolvedDetails.set(pid, detail);
          return detail;
        });
        p.catch(() => details.delete(pid)); // let the next attempt retry
        details.set(pid, p);
      }
      return details.get(pid);
    }

    function onPatientChange(pid) {
      state.patientId = pid && PID_RE.test(pid) ? pid : null;
      syncPatientQuery(state.patientId);
      if (state.patientId) {
        ctx.store.set({ selectedPatientId: state.patientId });
        ensureDetail(state.patientId).catch(() => {});
      }
      syncPatientUi();
    }

    async function loadPatients() {
      let list = [];
      try {
        const res = await ctx.api.caseload();
        list = ((res && res.patients) || []).map((p) => ({ id: p.id, name: p.name, bucket: p.bucket }));
      } catch {
        try {
          const res = await ctx.api.patients();
          list = ((res && res.patients) || []).map((p) => ({ id: p.id, name: p.name, bucket: null }));
        } catch {
          if (alive) ctx.toast({ kind: "error", text: "Couldn't load your patients. Questions cover the whole caseload." });
        }
      }
      if (!alive) return;
      state.patients = list.slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
      if (state.patientId && !state.patients.some((p) => p.id === state.patientId)) {
        state.patientId = null;
        syncPatientQuery(null);
      }
      patientOptions(head.select, state.patients, state.patientId);
      if (state.patientId) ensureDetail(state.patientId).catch(() => {});
      syncPatientUi();
      renderRail();
    }

    // ---- chat ----
    function onInput(value) {
      composer.counter.textContent = `${value.length}/${MAX_QUESTION}`;
    }

    function setWaiting(on) {
      state.waiting = on;
      for (const b of [composer.sendBtn, head.reviewBtn, ...composer.chips]) {
        if (on) b.setAttribute("disabled", ""); else b.removeAttribute("disabled");
      }
      logEl.setAttribute("aria-busy", on ? "true" : "false");
      composer.thinkingEl.textContent = on ? "Mira Assistant is thinking…" : "";
      composer.thinkingEl.classList.toggle("is-active", on);
    }

    function scrollLog() {
      logEl.scrollTop = logEl.scrollHeight;
    }

    function send(raw) {
      const question = String(raw || "").trim().slice(0, MAX_QUESTION);
      if (!question || state.waiting) return;
      if (raw === composer.textarea.value) {
        composer.textarea.value = "";
        onInput("");
      }
      const pid = state.patientId;
      run({ question, pid, call: (context) => ctx.api.copilotAsk(question, pid, context) });
    }

    function onReview() {
      const pid = state.patientId;
      if (!pid || state.waiting) return;
      const question = `Review ${patientName(pid)}'s last week`;
      run({ question, pid, call: () => ctx.api.assistantReview(pid) });
    }

    async function run({ question, pid, call }) {
      introEl.remove();
      const context = contextFrom(state.turns);
      const turn = { question, status: "pending", answerText: "" };
      state.turns.push(turn);
      logEl.append(renderQuestion(question));
      scrollLog();
      setWaiting(true);
      let json;
      try {
        json = await call(context);
      } catch (err) {
        if (!alive) return;
        turn.status = "error";
        const retry = () => { if (!state.waiting) run({ question, pid, call }); };
        logEl.append(renderError(askErrorText(err), retry));
        setWaiting(false);
        scrollLog();
        return;
      }
      if (!alive) return;
      const model = answerModel(json);
      turn.status = "done";
      turn.answerText = model.text;
      logEl.append(answerEl(model, pid));
      setWaiting(false);
      scrollLog();
      if (model.proposalIds.length) reloadProposals(true);
    }

    function answerEl(model, pid) {
      const canSend = Boolean(ctx.caps && ctx.caps.has && ctx.caps.has(MESSAGES_CAP));
      const { el } = renderAnswer(model, {
        draftName: patientName,
        canSend,
        note: { show: Boolean(pid),
          disabledReason: pid && resolvedDetails.get(pid) && !resolvedDetails.get(pid).latestSessionId ? NO_SESSIONS : null },
        onAddNote: (host, btn) => openNoteEditor(host, btn, pid, model.text),
        onCopy: copyDraft,
        onSendDraft: sendDraft,
        onShowProposal: (id) => highlightProposal(id),
      });
      return el;
    }

    async function openNoteEditor(host, btn, pid, text) {
      btn.setAttribute("disabled", "");
      let detail;
      try {
        detail = await ensureDetail(pid);
      } catch {
        if (!alive) return;
        btn.removeAttribute("disabled");
        ctx.toast({ kind: "error", text: "Couldn't load this patient's sessions. Try again." });
        return;
      }
      if (!alive) return;
      if (!detail.latestSessionId) {
        btn.setAttribute("title", NO_SESSIONS);
        host.replaceChildren(h("p", { class: "meta" }, NO_SESSIONS));
        return;
      }
      const name = patientName(pid);
      const editor = renderNoteEditor({
        patientName: name,
        text,
        onCancel: () => { host.replaceChildren(); btn.removeAttribute("disabled"); btn.focus?.(); },
        onApprove: async (value, { approveBtn, cancelBtn, errorEl }) => {
          const note = String(value || "").trim();
          if (!note) { errorEl.textContent = "Write something before approving."; return; }
          if (note.length > 4000) { errorEl.textContent = "Keep the note under 4000 characters."; return; }
          errorEl.textContent = "";
          approveBtn.setAttribute("disabled", "");
          cancelBtn.setAttribute("disabled", "");
          try {
            await ctx.api.approveProgressNote(detail.latestSessionId, note);
          } catch (err) {
            if (!alive) return;
            approveBtn.removeAttribute("disabled");
            cancelBtn.removeAttribute("disabled");
            errorEl.textContent = err && err.status === 403
              ? "Only the patient's therapist can approve this note."
              : "Couldn't save the note. Try again.";
            return;
          }
          if (!alive) return;
          host.replaceChildren(renderNoteApproved(name));
          ctx.toast({ kind: "success", text: `Progress note saved for ${name}.` });
        },
      });
      host.replaceChildren(editor.el);
      editor.textarea.focus?.();
    }

    async function copyDraft(draft, textEl) {
      try {
        await globalThis.navigator.clipboard.writeText(draft.text);
        ctx.toast({ kind: "success", text: "Copied." });
      } catch {
        try { globalThis.getSelection?.().selectAllChildren(textEl); } catch { /* nothing to select with */ }
        ctx.toast({ kind: "info", text: "Text selected. Press Ctrl+C (⌘C on a Mac) to copy." });
      }
    }

    async function sendDraft(draft, btn, draftEl) {
      if (String(draft.text).length > MESSAGE_MAX_CHARS) {
        ctx.toast({ kind: "error", text: `This draft is over ${MESSAGE_MAX_CHARS} characters. Copy it instead.` });
        return;
      }
      btn.setAttribute("disabled", "");
      try {
        await ctx.api.sendMessage(draft.patientId, draft.text);
      } catch {
        if (!alive) return;
        btn.removeAttribute("disabled");
        ctx.toast({ kind: "error", text: "Couldn't send the message. Try again." });
        return;
      }
      if (!alive) return;
      btn.replaceWith?.(h("span", { class: "meta" }, "Sent"));
      draftEl.querySelector(".draft__chip")?.replaceChildren();
      ctx.toast({ kind: "success", text: `Message sent to ${patientName(draft.patientId)}.` });
    }

    // ---- proposals rail ----
    function currentRx(p) {
      const list = rxByPatient.get(p.patient_id) || [];
      const ex = p.change && p.change.exercise_id;
      return list.find((r) => r.exercise_id === ex && r.active !== false) || null;
    }

    function patientStatus(pid) {
      const p = state.patients.find((x) => x.id === pid);
      return (p && STATUS_FOR_BUCKET[p.bucket]) || "watch";
    }

    function cardFor(p) {
      const current = p.status === "pending" ? currentRx(p) : null;
      const key = JSON.stringify([p.status, p.change, p.decided_by, p.decided_at, current, patientStatus(p.patient_id)]);
      const existing = cards.get(p.id);
      if (existing && existing.key === key) return existing.el;
      const el = proposalCard(p, {
        current, patientStatus: patientStatus(p.patient_id),
        onApprove: (prop) => decide(prop, () => ctx.api.approveProposal(prop.id),
          `Approved. ${prop.patient_name || "The patient"}'s plan is updated.`),
        onEdit: (prop, change) => decide(prop, () => ctx.api.editProposal(prop.id, change),
          `Approved with your changes. ${prop.patient_name || "The patient"}'s plan is updated.`),
        onDismiss: (prop) => decide(prop, () => ctx.api.dismissProposal(prop.id, null), "Proposal dismissed."),
      });
      cards.set(p.id, { el, key });
      return el;
    }

    function renderRail() {
      if (!alive) return;
      const m = proposalsTabsModel(state.proposals, state.tab);
      state.tab = m.tab;
      railTabsHost.replaceChildren(renderRailTabs(m, (key) => { state.tab = key; renderRail(); }));
      if (!state.proposalsLoaded) {
        railListHost.replaceChildren(h("p", { class: "meta assistant__rail-empty", role: "status" }, "Loading proposals…"));
      } else if (m.items.length === 0) {
        railListHost.replaceChildren(renderRailEmpty(m.tab));
      } else {
        railListHost.replaceChildren(...m.items.map(cardFor));
      }
      const live = new Set(state.proposals.map((p) => p.id));
      for (const id of cards.keys()) if (!live.has(id)) cards.delete(id);
      // Toggled in place (not part of the card key) so highlighting never rebuilds a card with an open edit form.
      for (const [id, { el }] of cards) el.classList.toggle("proposal-card--highlight", id === state.highlightedId);
    }

    async function decide(p, call, successText) {
      try {
        await call();
      } catch (err) {
        if (!alive) return false;
        const status = err && err.status;
        if (status === 409) {
          ctx.toast({ kind: "info", text: "Already decided" });
          reloadProposals(true);
        } else if (status === 403) {
          ctx.toast({ kind: "error", text: "Only the patient's therapist can decide this" });
        } else if (status === 400 || status === 422) {
          ctx.toast({ kind: "error", text: status === 400 && err.detail ? `Not saved: ${err.detail}.` : "Check the values and try again." });
        } else {
          ctx.toast({ kind: "error", text: "Couldn't save the decision. Try again." });
        }
        return false;
      }
      if (!alive) return true;
      ctx.toast({ kind: "success", text: successText });
      const entry = cards.get(p.id);
      entry?.el.classList.add("proposal-card--leaving");
      later(() => reloadProposals(true), reducedMotion() ? 0 : LEAVE_MS);
      return true;
    }

    async function reloadProposals(isRefresh) {
      const mySeq = ++proposalsSeq;
      const [pendingRes, allRes] = await Promise.allSettled([
        ctx.api.proposals({ status: "pending" }),
        ctx.api.proposals({ status: "all" }),
      ]);
      if (!alive || mySeq !== proposalsSeq) return;
      if (pendingRes.status !== "fulfilled" && allRes.status !== "fulfilled") {
        if (isRefresh) ctx.toast({ kind: "error", text: "Couldn't refresh proposals. Showing the last loaded list." });
        else railListHost.replaceChildren(h("p", { class: "meta assistant__rail-empty" }, "Couldn't load proposals."));
        return;
      }
      const byId = new Map();
      for (const res of [allRes, pendingRes]) {
        if (res.status !== "fulfilled") continue;
        for (const p of (res.value && res.value.proposals) || []) byId.set(p.id, p);
      }
      const list = [...byId.values()].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || b.id - a.id);
      const pending = list.filter((p) => p.status === "pending");
      // Current plan values for the "3 × 12 → 3 × 8" rows (best effort: a failure just shows the new values).
      const pids = [...new Set(pending.map((p) => p.patient_id))];
      const rxRes = await Promise.allSettled(pids.map((pid) => ctx.api.prescriptions(pid)));
      if (!alive || mySeq !== proposalsSeq) return;
      const rx = new Map();
      pids.forEach((pid, i) => {
        if (rxRes[i].status === "fulfilled") rx.set(pid, (rxRes[i].value && rxRes[i].value.prescriptions) || []);
      });
      rxByPatient = rx;
      state.proposals = list;
      state.proposalsLoaded = true;
      ctx.store.set({ proposalsWaiting: pending.length });
      renderRail();
      if (state.highlightId != null) {
        const id = state.highlightId;
        state.highlightId = null;
        highlightProposal(id, { quietIfMissing: false });
      }
    }

    function tabFor(status) {
      return status === "pending" ? "pending" : status === "dismissed" ? "dismissed" : "approved";
    }

    function highlightProposal(id, { quietIfMissing = true } = {}) {
      const p = state.proposals.find((x) => x.id === id);
      if (!p) {
        if (!quietIfMissing) ctx.toast({ kind: "info", text: "That proposal is no longer in the list." });
        return;
      }
      state.tab = tabFor(p.status);
      state.highlightedId = id;
      renderRail();
      const el = cards.get(id)?.el;
      if (!el) return;
      el.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
      el.focus?.({ preventScroll: true });
    }

    function onEvents(msg) {
      if (!msg || !PROPOSAL_EVENTS.has(msg.type)) return;
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => reloadProposals(true), REFRESH_DEBOUNCE_MS);
    }

    const unsubEvents = ctx.sockets.subscribe("events", onEvents);
    syncPatientUi();
    setWaiting(false);
    renderRail();
    loadPatients();
    reloadProposals(false);

    return () => {
      alive = false;
      clearTimeout(refreshTimer);
      for (const t of timers) clearTimeout(t);
      timers.clear();
      unsubEvents();
      state.turns = [];
    };
  },
};
