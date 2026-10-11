// Session auth: the API token lives only in sessionStorage (never localStorage, never logged).

export const AUTH_KEY = "mira.dashboard.auth";

const THERAPIST_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function loadAuth(storage) {
  let raw;
  try { raw = storage.getItem(AUTH_KEY); } catch { return null; }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.token !== "string" || typeof parsed.therapistId !== "string") return null;
    return { token: parsed.token, therapistId: parsed.therapistId };
  } catch {
    return null;
  }
}

export function saveAuth(storage, { token, therapistId }) {
  if (typeof token !== "string" || token.length < 24) throw new Error("token too short");
  if (typeof therapistId !== "string" || !THERAPIST_ID_RE.test(therapistId)) throw new Error("bad therapist id");
  storage.setItem(AUTH_KEY, JSON.stringify({ token, therapistId }));
}

export function clearAuth(storage) {
  storage.removeItem(AUTH_KEY);
}
