// One function per server route (see README "For the therapist dashboard" and server/api/*.py). Every write
// injects therapist_id/resolved_by/created_by = therapistId; every id in a path is encodeURIComponent-ed.

const enc = (id) => encodeURIComponent(id);

export function createEndpoints(api, therapistId) {
  return {
    caseload: () => api.get("/caseload", { therapist_id: therapistId }),
    patients: () => api.get("/patients"),
    patient: (pid) => api.get(`/patients/${enc(pid)}`),
    progress: (pid, { exerciseId, days } = {}) =>
      api.get(`/patients/${enc(pid)}/progress`, { exercise_id: exerciseId, days }),
    memory: (pid, days = 21) => api.get(`/patients/${enc(pid)}/memory`, { days }),

    session: (sid) => api.get(`/sessions/${enc(sid)}`),
    framesLttb: (sid) => api.get(`/sessions/${enc(sid)}/frames`, { downsample: 300 }),
    framesPage: (sid, t0) => api.get(`/sessions/${enc(sid)}/frames`, { downsample: 0, t0 }),
    progressNote: (sid) => api.get(`/sessions/${enc(sid)}/progress-note`),
    approveProgressNote: (sid, text) =>
      api.post(`/sessions/${enc(sid)}/progress-note`, { therapist_id: therapistId, text }),

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

    copilotAsk: (question, patientId) => {
      const body = { question };
      if (patientId !== null && patientId !== undefined) body.patient_id = patientId;
      return api.post("/copilot/ask", body);
    },

    notifications: ({ unread, limit, sinceId } = {}) =>
      api.get("/notifications", { unread, limit, since_id: sinceId, therapist_id: therapistId }),
    notificationCount: () => api.get("/notifications/count"),
    markRead: (id) => api.post(`/notifications/${enc(id)}/read`),
    markAllRead: () => api.post("/notifications/read-all", { therapist_id: therapistId }),
    ack: (id) => api.post(`/notifications/${enc(id)}/ack`, { therapist_id: therapistId }),

    stats: () => api.get("/stats"),
  };
}
