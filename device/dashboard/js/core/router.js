// Hash router. Ids are validated raw (never decoded) so a URL-encoded path separator cannot slip through.
const PID = "[A-Za-z0-9_-]{1,64}";
const SID = "[0-9a-fA-F-]{1,64}";
const PROPOSAL_ID = "\\d{1,12}";

const ROUTES = [
  ["caseload", new RegExp(`^/caseload$`), []],
  ["prescribe", new RegExp(`^/patients/(${PID})/prescribe$`), ["pid"]],
  ["timelapse", new RegExp(`^/patients/(${PID})/timelapse$`), ["pid"]],
  ["patient", new RegExp(`^/patients/(${PID})$`), ["pid"]],
  ["replay", new RegExp(`^/replay/(${SID})$`), ["sid"]],
  ["proposal", new RegExp(`^/proposals/(${PROPOSAL_ID})$`), ["id"]],
  ["live", new RegExp(`^/live/(${PID})$`), ["pid"]],
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
  live: (p) => `/live/${p.pid}`,
  copilot: () => "/copilot",
};

export function href(name, params = {}, query = {}) {
  const path = PATHS[name](params);
  const qs = new URLSearchParams(query).toString();
  return qs ? `#${path}?${qs}` : `#${path}`;
}
