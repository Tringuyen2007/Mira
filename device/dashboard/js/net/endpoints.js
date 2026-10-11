// One function per server route (see README "For the therapist dashboard" and server/api/*.py). Every write
// injects therapist_id/resolved_by/created_by = therapistId; every id in a path is encodeURIComponent-ed.

const enc = (id) => encodeURIComponent(id);
export const ASSISTANT_TIMEOUT_MS = 30000;
// POST /patients/{id}/messages takes {body (1-1000 chars), kind} (server/api/care.py TherapistMessageIn).
export const MESSAGE_MAX_CHARS = 1000;

export function createEndpoints(api, therapistId) {
  return {
    caseload: () => api.get("/caseload", { therapist_id: therapistId }),
    patients: () => api.get("/patients"),
    patient: (pid) => api.get(`/patients/${enc(pid)}`),
    progress: (pid, { exerciseId, days } = {}) =>
      api.get(`/patients/${enc(pid)}/progress`, { exercise_id: exerciseId, days }),
    memory: (pid, days = 21) => api.get(`/patients/${enc(pid)}/memory`, { days }),
    // Finished sessions, newest first (Time-lapse date pickers; up to 200).
    sessionHistory: (pid, { exerciseId, limit = 200 } = {}) =>
      api.get(`/patients/${enc(pid)}/session-history`, { exercise_id: exerciseId, limit }),

    session: (sid) => api.get(`/sessions/${enc(sid)}`),
    framesLttb: (sid) => api.get(`/sessions/${enc(sid)}/frames`, { downsample: 300 }),
    framesPage: (sid, t0) => api.get(`/sessions/${enc(sid)}/frames`, { downsample: 0, t0 }),
    progressNote: (sid) => api.get(`/sessions/${enc(sid)}/progress-note`),
    approveProgressNote: (sid, text) =>
      api.post(`/sessions/${enc(sid)}/progress-note`, { therapist_id: therapistId, text }),

    // Planned route (decisions): the Live session screen's "Send a cue" panel only calls this (and only
    // renders at all) when caps.has("/live/say") — the device-bridge endpoint that says a cue through Mira.
    sayLive: (pid, text) => api.post("/live/say", { patient_id: pid, therapist_id: therapistId, text }),
    liveMedia: () => api.get("/live/media"),

    alerts: () => api.get("/alerts"),
    resolveAlert: (alertId, note) =>
      api.post(`/alerts/${enc(alertId)}/resolve`, { resolved_by: therapistId, note }),

    proposals: ({ status, patientId } = {}) => api.get("/proposals", { status, patient_id: patientId }),
    approveProposal: (id) => api.post(`/proposals/${enc(id)}/approve`, { therapist_id: therapistId }),
    editProposal: (id, change) => api.post(`/proposals/${enc(id)}/edit`, { therapist_id: therapistId, change }),
    dismissProposal: (id, reason) =>
      api.post(`/proposals/${enc(id)}/dismiss`, { therapist_id: therapistId, reason }),

    prescriptions: (pid) => api.get(`/prescriptions/${enc(pid)}`),
    putPrescription: (pid, body) =>
      api.put(`/prescriptions/${enc(pid)}`, { ...body, created_by: therapistId }),
    setLimits: (pid, { hrLimit }) =>
      api.patch(`/patients/${enc(pid)}/limits`, { therapist_id: therapistId, hr_limit: hrLimit }),
    // Planned route (decisions): the Prescribe screen's "Add from exercise library" search only calls this
    // (and only renders the box at all) when caps.has("/exercises").
    exercises: (q) => api.get("/exercises", { q }),

    // Mira Assistant (server name: copilot). Agent runs take longer than ordinary reads, hence the 30 s timeout.
    copilotAsk: (question, patientId, context) => {
      const body = { question };
      if (patientId !== null && patientId !== undefined) body.patient_id = patientId;
      if (Array.isArray(context) && context.length > 0) body.context = context;
      return api.post("/copilot/ask", body, { timeoutMs: ASSISTANT_TIMEOUT_MS });
    },
    assistantReview: (pid) => api.post(`/copilot/review/${enc(pid)}`, {}, { timeoutMs: ASSISTANT_TIMEOUT_MS }),
    // Only called when caps.has("/patients/{patient_id}/messages"). Callers check the length first; the slice
    // is a last guard so an over-long body can never 422.
    sendMessage: (pid, text) =>
      api.post(`/patients/${enc(pid)}/messages`, { body: String(text).slice(0, MESSAGE_MAX_CHARS), kind: "message" }),

    notifications: ({ unread, limit, sinceId } = {}) =>
      api.get("/notifications", { unread, limit, since_id: sinceId, therapist_id: therapistId }),
    notificationCount: () => api.get("/notifications/count", { therapist_id: therapistId }),
    markRead: (id) => api.post(`/notifications/${enc(id)}/read`),
    markAllRead: () => api.post("/notifications/read-all", { therapist_id: therapistId }),
    ack: (id) => api.post(`/notifications/${enc(id)}/ack`, { therapist_id: therapistId }),

    stats: () => api.get("/stats"),
    pairingCode: (pid) => api.post(`/patients/${enc(pid)}/pairing-code`, { therapist_id: therapistId }),
  };
}
