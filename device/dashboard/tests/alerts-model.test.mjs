import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { alertsModel, applyAlertEvent } from "../js/core/alerts-model.js";

const alertsJson = JSON.parse(readFileSync(new URL("../../../server/samples/rest_get_alerts.json", import.meta.url)));
const alertEvent = JSON.parse(readFileSync(new URL("../../../server/samples/ws_events_alert.json", import.meta.url)));

const patientsById = { maya: { id: "maya", name: "Maya Ruiz" } };

test("alertsModel builds the escalation timeline in fixed step order with labels", () => {
  const model = alertsModel(alertsJson, patientsById);
  assert.equal(model.length, 1);
  const a = model[0];
  assert.equal(a.alertId, "a1825878-8f95-456b-8458-d4533db46e06");
  assert.equal(a.patientId, "maya");
  assert.equal(a.name, "Maya Ruiz");
  assert.equal(a.kind, "fall");
  assert.equal(a.title, "Fall — Maya Ruiz");
  assert.deepEqual(a.steps.map((s) => s.step), ["detected", "checked_in", "escalated", "resolved"]);
  assert.deepEqual(a.steps.map((s) => s.label), ["Detected", "Checked in", "Escalated", "Resolved"]);
  assert.deepEqual(a.steps.map((s) => s.done), [true, true, true, false]);
  assert.equal(a.steps[3].time, null);
  assert.equal(a.canResolve, true);
  assert.equal(a.replayHref, "#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6");
});

test("falls back to the patient id when no patient record is known", () => {
  const model = alertsModel(alertsJson, {});
  assert.equal(model[0].name, "maya");
  assert.equal(model[0].title, "Fall — maya");
});

test("applyAlertEvent advances the matching step and marks it done", () => {
  const model = alertsModel(alertsJson, patientsById);
  // The fixture's alert is already escalated; re-apply the escalated event and confirm the step stays done.
  const next = applyAlertEvent(model, alertEvent);
  const a = next.find((x) => x.alertId === alertEvent.alert_id);
  const escalated = a.steps.find((s) => s.step === "escalated");
  assert.equal(escalated.done, true);
  assert.equal(escalated.time, alertEvent.t);
});

test("applyAlertEvent creates a brand-new alert the banner hasn't seen from GET /alerts yet", () => {
  const evt = { type: "alert", alert_id: "new-alert-1", kind: "fall", step: "detected", patient_id: "maya",
    session_id: "fea67dbd-f43f-4803-85d5-5e205100a69b", t: 1791664000 };
  const next = applyAlertEvent([], evt);
  assert.equal(next.length, 1);
  assert.equal(next[0].alertId, "new-alert-1");
  assert.deepEqual(next[0].steps.map((s) => s.done), [true, false, false, false]);
});

test("a resolved step removes that alert from the model (banner closes)", () => {
  const model = alertsModel(alertsJson, patientsById);
  const resolved = { type: "alert", alert_id: model[0].alertId, kind: "fall", step: "resolved_by_therapist",
    patient_id: "maya", t: 1791664400 };
  const next = applyAlertEvent(model, resolved);
  assert.equal(next.length, 0);
});

test("the guardian's own 'resolved' (false alarm) step also removes the banner", () => {
  const model = alertsModel(alertsJson, patientsById);
  const resolved = { type: "alert", alert_id: model[0].alertId, kind: "fall", step: "resolved",
    patient_id: "maya", t: 1791664400 };
  const next = applyAlertEvent(model, resolved);
  assert.equal(next.length, 0);
});

test("'closed' (patient responded mid-call with no call ever placed) also removes the banner", () => {
  const model = alertsModel(alertsJson, patientsById);
  const closed = { type: "alert", alert_id: model[0].alertId, kind: "fall", step: "closed",
    patient_id: "maya", t: 1791664400 };
  const next = applyAlertEvent(model, closed);
  assert.equal(next.length, 0);
});

test("'patient_responded' (mid-call, incident stays open) is ignored, not treated as resolved", () => {
  const model = alertsModel(alertsJson, patientsById);
  const evt = { type: "alert", alert_id: model[0].alertId, kind: "fall", step: "patient_responded",
    patient_id: "maya", t: 1791664400, payload: { response: "thumbs_up" } };
  const next = applyAlertEvent(model, evt);
  assert.deepEqual(next, model);
});

test("an unknown step (call progress, not part of the banner timeline) is ignored", () => {
  const model = alertsModel(alertsJson, patientsById);
  const evt = { type: "alert", alert_id: model[0].alertId, kind: "fall", step: "call_update",
    patient_id: "maya", t: 1791664400, message: "Still on the line." };
  const next = applyAlertEvent(model, evt);
  assert.deepEqual(next, model);
});

test("an event for an unknown alert id with an unknown step is dropped, not fabricated", () => {
  const model = alertsModel(alertsJson, patientsById);
  const evt = { type: "alert", alert_id: "does-not-exist", kind: "fall", step: "acknowledged",
    patient_id: "maya", t: 1791664400 };
  const next = applyAlertEvent(model, evt);
  assert.deepEqual(next, model);
});

test("a brand-new alert over the socket is titled with the patient's name when patientsById is passed", () => {
  const next = applyAlertEvent([], { type: "alert", alert_id: "new-1", patient_id: "maya", kind: "fall",
    step: "detected", t: 1 }, { maya: { id: "maya", name: "Maya Ruiz" } });
  assert.equal(next.length, 1);
  assert.match(next[0].title, /Maya/);
  assert.doesNotMatch(next[0].title, /— maya$/);
});
