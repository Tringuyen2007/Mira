// REST client: bearer token, JSON, 10 s timeout (overridable per request with {timeoutMs}). Errors are ApiError{status, detail}; status 0 = offline/timeout.

export class ApiError extends Error {
  constructor(status, detail) { super(`${status} ${detail}`); this.status = status; this.detail = detail; }
}

export function createApi({ token, fetchImpl = globalThis.fetch.bind(globalThis), base = "", timeoutMs = 10000,
                            onUnauthorized } = {}) {
  async function request(method, path, { query, body, timeoutMs: perRequestMs } = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query || {})) if (v !== null && v !== undefined) qs.set(k, String(v));
    // qs.toString() (not URLSearchParams.size, unsupported on Safari < 17 / Chrome < 113) so params aren't
    // silently dropped on older browsers.
    const qsStr = qs.toString();
    const url = `${base}${path}${qsStr ? `?${qsStr}` : ""}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), perRequestMs ?? timeoutMs);
    const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res;
    let data = null;
    try {
      try {
        res = await fetchImpl(url, { method, headers, signal: ctrl.signal, cache: "no-store", credentials: "omit",
          body: body === undefined ? undefined : JSON.stringify(body) });
      } catch (e) {
        throw new ApiError(0, e?.name === "AbortError" ? "timeout" : "offline");
      }
      // The timer also covers the body: a response whose body stalls is aborted, not left hanging.
      try { data = await res.json(); } catch (e) {
        if (ctrl.signal.aborted || e?.name === "AbortError") throw new ApiError(0, "timeout");
        /* empty or non-JSON body */
      }
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 401) onUnauthorized?.();
    if (!res.ok) throw new ApiError(res.status, (data && typeof data.detail === "string") ? data.detail : res.statusText);
    return data;
  }
  return {
    get: (path, query, opts) => request("GET", path, { query, ...opts }),
    post: (path, body, opts) => request("POST", path, { body: body ?? {}, ...opts }),
    put: (path, body) => request("PUT", path, { body }),
    patch: (path, body) => request("PATCH", path, { body }),
  };
}
