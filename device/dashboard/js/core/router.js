// Hash router. Ids are validated raw (never decoded) so a URL-encoded path separator cannot slip through.
const PID = "[A-Za-z0-9_-]{1,64}";
// Strict UUID (8-4-4-4-12 hex, case-insensitive): the server only ever accepts uuid.UUID for session ids
// (server/api/sessions.py), so a loose charset here just produces routes that 422 at the server instead of
// 404ing locally.
const SID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const PROPOSAL_ID = "\\d{1,12}";

const ROUTES = [
  ["caseload", new RegExp(`^/caseload$`), []],
  ["prescribe", new RegExp(`^/patients/(${PID})/prescribe$`), ["pid"]],
  ["timelapse", new RegExp(`^/patients/(${PID})/timelapse$`), ["pid"]],
  ["patient", new RegExp(`^/patients/(${PID})$`), ["pid"]],
  ["replay", new RegExp(`^/replay/(${SID})$`), ["sid"]],
  ["proposal", new RegExp(`^/proposals/(${PROPOSAL_ID})$`), ["id"]],
  ["live", new RegExp(`^/live/(${PID})$`), ["pid"]],
  ["live", new RegExp(`^/live$`), []],
  ["assistant", new RegExp(`^/assistant$`), []],
  // Legacy name of the Mira Assistant screen; main.js redirects it (redirectFor) so old links keep working.
  ["copilot", new RegExp(`^/copilot$`), []],
];

export function parseRoute(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  if (raw === "" || raw === "/") return { name: "caseload", params: {}, query: {} };
  const [path, queryStr] = raw.split("?");
  const query = Object.fromEntries(new URLSearchParams(queryStr || ""));
  for (const [name, re, keys] of ROUTES) {
    const m = re.exec(path);
    if (!m) continue;
    const params = Object.fromEntries(keys.map((k, i) => [k, m[i + 1]]));
    return { name, params, query };
  }
  return { name: "notfound", params: {}, query: {} };
}

const PATHS = {
  caseload: () => "/caseload",
  patient: (p) => `/patients/${p.pid}`,
  prescribe: (p) => `/patients/${p.pid}/prescribe`,
  timelapse: (p) => `/patients/${p.pid}/timelapse`,
  replay: (p) => `/replay/${p.sid}`,
  proposal: (p) => `/proposals/${p.id}`,
  live: (p) => (p && p.pid ? `/live/${p.pid}` : "/live"),
  assistant: () => "/assistant",
  copilot: () => "/copilot",
};

export function href(name, params = {}, query = {}) {
  const path = PATHS[name](params);
  const qs = new URLSearchParams(query).toString();
  return qs ? `#${path}?${qs}` : `#${path}`;
}

// Routes that only exist to forward to another one: returns the hash to replace the current one with, or null.
export function redirectFor(route) {
  if (route && route.name === "copilot") return href("assistant", {}, route.query || {});
  return null;
}
