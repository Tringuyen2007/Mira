// Alerts banner model: GET /alerts rows -> one per-alert escalation timeline; ws `alert` events (server/agents/
// safety_guardian.py's `log()`) patch it live. Step order/labels are fixed regardless of arrival order; any step
// the banner doesn't know about (call_registered, call_update, acknowledged, call_retry, call_failed, call_ended,
// the RETELL_STEPS) is ignored, not shown as a 5th step.
import { safeInternalLink } from "./links.js";

export const STEP_ORDER = ["detected", "checked_in", "escalated", "resolved"];
const STEP_LABEL = { detected: "Detected", checked_in: "Checked in", escalated: "Escalated", resolved: "Resolved" };
// Mirrors server/db/queries.py's CLOSED_ALERT_STEPS exactly: the guardian's own "resolved" (false alarm, before
// escalation), "closed" (e.g. the patient responded mid-call but no call was ever placed, so nothing else will
// close it) and the dashboard's "resolved_by_therapist" all land on the "Resolved" step and close the banner.
const RESOLVED_STEPS = new Set(["resolved", "closed", "resolved_by_therapist"]);
const KIND_LABEL = { fall: "Fall", pain: "Pain", heart_rate: "Heart rate" };

function kindLabel(kind) {
  if (KIND_LABEL[kind]) return KIND_LABEL[kind];
  return kind ? kind[0].toUpperCase() + kind.slice(1) : "Alert";
}

function canonicalStep(step) {
  return RESOLVED_STEPS.has(step) ? "resolved" : step;
}

function stepsFor(rawSteps) {
  const latest = new Map(); // canonical step -> time
  for (const s of rawSteps || []) {
    const canonical = canonicalStep(s && s.step);
    if (!STEP_ORDER.includes(canonical)) continue; // unknown step ignored
    const prev = latest.get(canonical);
    if (prev === undefined || String(s.time) > String(prev)) latest.set(canonical, s.time);
  }
  return STEP_ORDER.map((step) => {
    const has = latest.has(step);
    return { step, label: STEP_LABEL[step], time: has ? latest.get(step) : null, done: has };
  });
}

function buildAlert(raw, patientsById) {
  const patient = (patientsById && patientsById[raw.patient_id]) || null;
  const name = (patient && patient.name) || raw.patient_id;
  return {
    alertId: raw.alert_id,
    patientId: raw.patient_id,
    sessionId: raw.session_id || null,
    name,
    kind: raw.kind,
    title: `${kindLabel(raw.kind)} — ${name}`,
    steps: stepsFor(raw.steps),
    replayHref: raw.session_id ? safeInternalLink(`#/replay/${raw.session_id}`) : null,
    canResolve: raw.open !== false,
  };
}

export function alertsModel(alertsJson, patientsById) {
  const alerts = (alertsJson && alertsJson.alerts) || [];
  return alerts.map((a) => buildAlert(a, patientsById));
}

// Pure: returns a new array (or the same one, unchanged, for an ignored event) — never mutates `model`.
export function applyAlertEvent(model, evt) {
  if (!evt || !evt.alert_id || !evt.step) return model;
  if (RESOLVED_STEPS.has(evt.step)) {
    const next = model.filter((a) => a.alertId !== evt.alert_id);
    return next.length === model.length ? model : next;
  }
  if (!STEP_ORDER.includes(evt.step)) return model; // unknown step (call progress, acknowledged, ...) ignored
  const idx = model.findIndex((a) => a.alertId === evt.alert_id);
  if (idx === -1) {
    // A brand-new alert the banner hasn't loaded from GET /alerts yet.
    const fresh = buildAlert({
      alert_id: evt.alert_id, patient_id: evt.patient_id, kind: evt.kind, session_id: evt.session_id,
      steps: [{ step: evt.step, time: evt.t }], open: true,
    }, null);
    return [...model, fresh];
  }
  const next = model.slice();
  next[idx] = {
    ...next[idx],
    steps: next[idx].steps.map((s) => (s.step === evt.step ? { ...s, time: evt.t, done: true } : s)),
  };
  return next;
}
