// Fakedom coverage for the Mira Assistant mount: Enter sends (Shift+Enter doesn't), the textarea survives every
// answer, follow-ups carry context, Send is disabled while waiting, patient review, explicit progress-note
// approval, drafts gated on the messages capability, proposal decisions (409), highlight and unmount.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { screen } = await import("../js/screens/assistant/index.js");

const load = (rel) => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8"));
const realAsk = load("../../../server/samples/rest_post_copilot_ask.json");
const realReview = load("../../../server/samples/rest_post_copilot_review.json");
const realProposals = load("../../../server/samples/rest_get_proposals.json");
const realPatient = load("../../../server/samples/rest_get_patient.json");
const figma = load("./fixtures/assistant-figma.json");

const flush = () => new Promise((r) => setTimeout(r, 0));

function makeCtx({ caps = [], askImpl, approveImpl, patientJson = realPatient, caseloadDelayMs = 0 } = {}) {
  const calls = [];
  const subs = new Map();
  const toasts = [];
  const storeState = {};
  const rec = (name, fn) => async (...args) => { calls.push([name, ...args]); return fn(...args); };
  const ctx = {
    api: {
      caseload: rec("caseload", async () => { if (caseloadDelayMs) await new Promise((r) => setTimeout(r, caseloadDelayMs)); return { patients: [
        { id: "maya", name: "Maya Ellis", bucket: "needs_you" },
        { id: "linda", name: "Linda Park", bucket: "watch" },
      ] }; }),
      patients: rec("patients", () => ({ patients: [] })),
      patient: rec("patient", () => patientJson),
      proposals: rec("proposals", ({ status }) => (status === "pending" || status === "all" ? realProposals : { proposals: [] })),
      prescriptions: rec("prescriptions", () => ({ prescriptions: realPatient.prescriptions })),
      copilotAsk: rec("copilotAsk", askImpl || (() => realAsk)),
      assistantReview: rec("assistantReview", () => realReview),
      approveProgressNote: rec("approveProgressNote", () => ({ ok: true })),
      sendMessage: rec("sendMessage", () => ({ ok: true })),
      approveProposal: rec("approveProposal", approveImpl || (() => ({}))),
      editProposal: rec("editProposal", () => ({})),
      dismissProposal: rec("dismissProposal", () => ({})),
    },
    sockets: {
      subscribe(ch, fn) { subs.set(ch, fn); return () => subs.delete(ch); },
    },
    store: { set(p) { Object.assign(storeState, p); }, get() { return storeState; }, subscribe() { return () => {}; } },
    caps: { has: (p) => caps.includes(p) },
    toast: (t) => toasts.push(t),
    navigate: () => {},
  };
  return { ctx, calls, subs, toasts, storeState };
}

function mount(opts, route = { name: "assistant", params: {}, query: {} }) {
  const root = document.createElement("div");
  const env = makeCtx(opts);
  const unmount = screen.mount(root, env.ctx, route);
  return { root, unmount, ...env };
}

const byText = (root, sel, text) => root.querySelectorAll(sel).find((b) => b.textContent === text);
const type = (ta, v) => { ta.value = v; ta.dispatch("input", { target: ta }); };
const enter = (ta, shiftKey = false) => ta.dispatch("keydown", { key: "Enter", shiftKey });

test("Enter sends, Shift+Enter doesn't; the textarea is never replaced; follow-ups carry context", async () => {
  const { root, calls, unmount } = mount();
  await flush();
  const ta = root.querySelector("textarea");
  type(ta, "Who is compensating?");
  assert.equal(root.querySelector(".assistant__counter").textContent, "20/500");
  enter(ta, true);
  assert.equal(calls.filter((c) => c[0] === "copilotAsk").length, 0);
  enter(ta);
  assert.deepEqual(calls.find((c) => c[0] === "copilotAsk"), ["copilotAsk", "Who is compensating?", null, []]);
  assert.equal(ta.value, "");
  await flush();
  await flush();
  assert.equal(root.querySelector("textarea"), ta);
  const answer = root.querySelector(".bubble--assistant");
  assert.match(answer.textContent, /Mira Assistant/);
  assert.ok(answer.querySelector(".chip--ai"));
  assert.equal(answer.querySelectorAll("li").length >= 3, true);
  assert.ok(answer.querySelector("svg"));
  assert.equal(byText(answer, "summary", "Show the query") !== undefined, true);
  assert.equal(answer.querySelectorAll("pre").length, 3);
  assert.match(answer.textContent, /I drafted 1 change to the plan\. See Proposals\./);

  type(ta, "And her pain?");
  enter(ta);
  const second = calls.filter((c) => c[0] === "copilotAsk")[1];
  assert.equal(second[1], "And her pain?");
  assert.deepEqual(second[3], [{ question: "Who is compensating?", answer: realAsk.answer }]);
  unmount();
});

test("Send and the suggestions are disabled while waiting, with a Thinking indicator", async () => {
  let resolve;
  const { root, unmount } = mount({ askImpl: () => new Promise((r) => { resolve = r; }) });
  await flush();
  byText(root, "button", "Summarise today").dispatch("click");
  const send = byText(root, "button", "Send");
  assert.ok(send.hasAttribute("disabled"));
  assert.ok(byText(root, "button", "Who missed sessions?").hasAttribute("disabled"));
  assert.match(root.querySelector(".assistant__thinking").textContent, /thinking/);
  resolve(realAsk);
  await flush();
  await flush();
  assert.ok(!send.hasAttribute("disabled"));
  assert.equal(root.querySelector(".assistant__thinking").textContent, "");
  unmount();
});

test("timeouts show an error bubble with Try again", async () => {
  const { root, calls, unmount } = mount({ askImpl: () => { throw Object.assign(new Error("t"), { status: 0, detail: "timeout" }); } });
  await flush();
  byText(root, "button", "Summarise today").dispatch("click");
  await flush();
  const err = root.querySelector(".bubble--error");
  assert.match(err.textContent, /longer than 30 seconds/);
  byText(err, "button", "Try again").dispatch("click");
  assert.equal(calls.filter((c) => c[0] === "copilotAsk").length, 2);
  unmount();
});

test("picking a patient scopes questions, enables Review and an explicit progress-note approval", async () => {
  const { root, calls, toasts, unmount } = mount();
  await flush();
  await flush();
  const select = root.querySelector("select");
  assert.deepEqual(select.querySelectorAll("option").map((o) => o.textContent), ["All patients", "Linda Park", "Maya Ellis"]);
  const review = byText(root, "button", "Review this patient");
  assert.ok(review.hidden);
  select.value = "maya";
  select.dispatch("change", { target: select });
  assert.ok(!review.hidden);
  review.dispatch("click");
  assert.ok(calls.some((c) => c[0] === "assistantReview" && c[1] === "maya"));
  await flush();
  await flush();

  const answer = root.querySelectorAll(".bubble--assistant").at(-1);
  byText(answer, "button", "Add to progress note").dispatch("click");
  await flush();
  const editor = answer.querySelector(".note-editor");
  assert.ok(editor);
  assert.ok(editor.querySelector(".chip--ai"));
  assert.equal(editor.querySelector("textarea").value, realReview.answer);
  assert.equal(calls.filter((c) => c[0] === "approveProgressNote").length, 0, "nothing saved before the click");
  editor.querySelector("textarea").value = "Edited note.";
  byText(editor, "button", "Approve as progress note").dispatch("click");
  await flush();
  assert.deepEqual(calls.find((c) => c[0] === "approveProgressNote"),
    ["approveProgressNote", realPatient.recent_sessions[0].id, "Edited note."]);
  assert.match(answer.textContent, /Approved as a progress note for Maya Ellis/);
  assert.ok(toasts.some((t) => t.kind === "success"));

  const ta = root.querySelector("textarea");
  type(ta, "How is she?");
  enter(ta);
  assert.equal(calls.filter((c) => c[0] === "copilotAsk").at(-1)[2], "maya");
  unmount();
});

test("a patient with no sessions can't get a progress note", async () => {
  const { root, calls, unmount } = mount({ patientJson: { ...realPatient, recent_sessions: [] } },
    { name: "assistant", params: {}, query: { patient: "maya" } });
  await flush();
  await flush();
  byText(root, "button", "Review this patient").dispatch("click");
  await flush();
  await flush();
  const btn = byText(root, "button", "Add to progress note");
  assert.ok(btn.hasAttribute("disabled"), "rendered disabled: the patient's sessions were already loaded");
  btn.dispatch("click");
  await flush();
  assert.ok(btn.hasAttribute("disabled"));
  assert.match(btn.getAttribute("title"), /No sessions yet/);
  assert.equal(calls.filter((c) => c[0] === "approveProgressNote").length, 0);
  unmount();
});

test("drafts offer Copy text; Approve and send only with the messages capability", async () => {
  const a = mount({ askImpl: () => figma });
  await flush();
  byText(a.root, "button", "Draft progress notes").dispatch("click");
  await flush();
  const draft = a.root.querySelector(".draft");
  assert.match(draft.textContent, /Draft to Linda Park/);
  assert.ok(draft.querySelector(".chip--ai"));
  assert.ok(byText(draft, "button", "Copy text"));
  assert.equal(byText(draft, "button", "Approve and send"), undefined);
  a.unmount();

  const b = mount({ askImpl: () => figma, caps: ["/patients/{patient_id}/messages"] });
  await flush();
  byText(b.root, "button", "Draft progress notes").dispatch("click");
  await flush();
  const send = byText(b.root.querySelector(".draft"), "button", "Approve and send");
  assert.ok(send);
  assert.equal(b.calls.filter((c) => c[0] === "sendMessage").length, 0);
  send.dispatch("click");
  await flush();
  assert.deepEqual(b.calls.find((c) => c[0] === "sendMessage"), ["sendMessage", "linda", figma.drafts[0].text]);
  b.unmount();
});

test("proposals rail: Waiting tab with current → new values; 409 toasts and refreshes", async () => {
  const err = Object.assign(new Error("409"), { status: 409, detail: "proposal already approved" });
  const { root, calls, toasts, storeState, unmount } = mount({ approveImpl: () => { throw err; } });
  for (let i = 0; i < 4; i++) await flush();
  const tabs = root.querySelectorAll('[role="tab"]');
  assert.deepEqual(tabs.map((t) => t.textContent), ["Waiting 1", "Approved", "Dismissed"]);
  const card = root.querySelector(".proposal-card");
  assert.match(card.textContent, /Rest days between squat days: 0 → 1/);
  assert.equal(storeState.proposalsWaiting, 1);
  const before = calls.filter((c) => c[0] === "proposals").length;
  byText(card, "button", "Approve").dispatch("click");
  await flush();
  assert.ok(toasts.some((t) => t.text === "Already decided"));
  assert.ok(calls.filter((c) => c[0] === "proposals").length > before);
  unmount();
});

test("#/proposals/:id highlights that card; unmount unsubscribes from events", async () => {
  const { root, subs, unmount } = mount({}, { name: "proposal", params: { id: "1" }, query: {} });
  for (let i = 0; i < 4; i++) await flush();
  assert.ok(root.querySelector(".proposal-card").classList.contains("proposal-card--highlight"));
  assert.ok(subs.has("events"));
  unmount();
  assert.equal(subs.has("events"), false);
});

test("AI text is never parsed as markup", async () => {
  const evil = { ...realAsk, answer: "<img src=x onerror=alert(1)>\n1. <script>bad()</script>", charts: [], sql: ["<b>q</b>"] };
  const { root, unmount } = mount({ askImpl: () => evil });
  await flush();
  byText(root, "button", "Summarise today").dispatch("click");
  await flush();
  const answer = root.querySelector(".bubble--assistant");
  assert.equal(answer.querySelector("script"), null);
  assert.match(answer.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(answer.querySelector("pre").textContent, "<b>q</b>");
  unmount();
});

test("the highlight survives the patient list arriving after the proposals (card rebuilt with a new status)", async () => {
  const { root, unmount } = mount({ caseloadDelayMs: 20 }, { name: "proposal", params: { id: "1" }, query: {} });
  for (let i = 0; i < 4; i++) await flush();
  await new Promise((r) => setTimeout(r, 40));
  const card = root.querySelector(".proposal-card");
  assert.ok(card.classList.contains("proposal-card--highlight"));
  unmount();
});
