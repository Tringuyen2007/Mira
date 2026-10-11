import { test } from "node:test";
import assert from "node:assert/strict";
import { createEndpoints } from "../js/net/endpoints.js";

const rec = () => { const calls = []; const r = (m) => async (path, arg) => { calls.push([m, path, arg]); return {}; };
  return { calls, get: r("GET"), post: r("POST"), put: r("PUT"), patch: r("PATCH") }; };

test("paths, encoding and therapist id injection", async () => {
  const api = rec();
  const ep = createEndpoints(api, "okafor");
  await ep.patient("a/b");
  await ep.approveProposal(7);
  await ep.dismissProposal(7, "not now");
  await ep.resolveAlert("a1", "called her");
  await ep.putPrescription("maya", { exercise_id: "squat", sets: 3 });
  await ep.setLimits("maya", { hrLimit: 120 });
  await ep.approveProgressNote("s1", "Week 3 …");
  await ep.caseload();
  await ep.sayLive("maya", "Slow down");
  assert.deepEqual(api.calls, [
    ["GET", "/patients/a%2Fb", undefined],
    ["POST", "/proposals/7/approve", { therapist_id: "okafor" }],
    ["POST", "/proposals/7/dismiss", { therapist_id: "okafor", reason: "not now" }],
    ["POST", "/alerts/a1/resolve", { resolved_by: "okafor", note: "called her" }],
    ["PUT", "/prescriptions/maya", { exercise_id: "squat", sets: 3, created_by: "okafor" }],
    ["PATCH", "/patients/maya/limits", { therapist_id: "okafor", hr_limit: 120 }],
    ["POST", "/sessions/s1/progress-note", { therapist_id: "okafor", text: "Week 3 …" }],
    ["GET", "/caseload", { therapist_id: "okafor" }],
    ["POST", "/live/say", { patient_id: "maya", therapist_id: "okafor", text: "Slow down" }],
  ]);
});

test("more endpoints: query building, omission rules, and other writes", async () => {
  const api = rec();
  const ep = createEndpoints(api, "okafor");
  await ep.patients();
  await ep.progress("maya", { exerciseId: "squat", days: 30 });
  await ep.memory("maya");
  await ep.memory("maya", 7);
  await ep.session("s1");
  await ep.framesLttb("s1");
  await ep.framesPage("s1", null);
  await ep.framesPage("s1", "2024-01-01T00:00:00Z");
  await ep.alerts();
  await ep.proposals({ status: "pending", patientId: "maya" });
  await ep.editProposal(9, { sets: 4 });
  await ep.prescriptions("maya");
  await ep.progressNote("s1");
  await ep.copilotAsk("How is Maya doing?", "maya");
  await ep.copilotAsk("How is Maya doing?", null);
  await ep.notifications({ unread: true, limit: 10, sinceId: 5 });
  await ep.notificationCount();
  await ep.markRead(3);
  await ep.markAllRead();
  await ep.ack(3);
  await ep.stats();
  assert.deepEqual(api.calls, [
    ["GET", "/patients", undefined],
    ["GET", "/patients/maya/progress", { exercise_id: "squat", days: 30 }],
    ["GET", "/patients/maya/memory", { days: 21 }],
    ["GET", "/patients/maya/memory", { days: 7 }],
    ["GET", "/sessions/s1", undefined],
    ["GET", "/sessions/s1/frames", { downsample: 300 }],
    ["GET", "/sessions/s1/frames", { downsample: 0, t0: null }],
    ["GET", "/sessions/s1/frames", { downsample: 0, t0: "2024-01-01T00:00:00Z" }],
    ["GET", "/alerts", undefined],
    ["GET", "/proposals", { status: "pending", patient_id: "maya" }],
    ["POST", "/proposals/9/edit", { therapist_id: "okafor", change: { sets: 4 } }],
    ["GET", "/prescriptions/maya", undefined],
    ["GET", "/sessions/s1/progress-note", undefined],
    ["POST", "/copilot/ask", { question: "How is Maya doing?", patient_id: "maya" }],
    ["POST", "/copilot/ask", { question: "How is Maya doing?" }],
    ["GET", "/notifications", { unread: true, limit: 10, since_id: 5, therapist_id: "okafor" }],
    ["GET", "/notifications/count", { therapist_id: "okafor" }],
    ["POST", "/notifications/3/read", undefined],
    ["POST", "/notifications/read-all", { therapist_id: "okafor" }],
    ["POST", "/notifications/3/ack", { therapist_id: "okafor" }],
    ["GET", "/stats", undefined],
  ]);
});

test("assistant calls: context only when non-empty, review route, 30 s timeout per call", async () => {
  const calls = [];
  const api = { post: async (path, body, opts) => { calls.push([path, body, opts]); return {}; } };
  const ep = createEndpoints(api, "okafor");
  const ctx = [{ question: "Who is compensating?", answer: "Maya." }];
  await ep.copilotAsk("And her pain?", "maya", ctx);
  await ep.copilotAsk("Summarise today", null, []);
  await ep.assistantReview("a/b");
  await ep.sendMessage("maya", "Hi Maya");
  assert.deepEqual(calls, [
    ["/copilot/ask", { question: "And her pain?", patient_id: "maya", context: ctx }, { timeoutMs: 30000 }],
    ["/copilot/ask", { question: "Summarise today" }, { timeoutMs: 30000 }],
    ["/copilot/review/a%2Fb", {}, { timeoutMs: 30000 }],
    ["/patients/maya/messages", { therapist_id: "okafor", text: "Hi Maya" }, undefined],
  ]);
});
