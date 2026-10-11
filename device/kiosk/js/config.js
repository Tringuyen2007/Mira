// Kiosk configuration. The device token arrives once in the URL fragment (#token=…, never sent to the server
// or written to its logs), is kept in localStorage on the kiosk, and the fragment is removed from the address bar.

export const TOKEN_KEY = "mira.deviceToken";

export function readToken(location, storage, history) {
  const params = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  if (params.has("token")) {
    // Strip the fragment either way, so a stale or empty #token= never lingers in the address bar.
    history.replaceState(null, "", location.pathname + (location.search || ""));
    const fromHash = params.get("token");
    if (fromHash) {
      try { storage.setItem(TOKEN_KEY, fromHash); } catch { /* private mode: keep it for this load only */ }
      return fromHash;
    }
  }
  try { return storage.getItem(TOKEN_KEY); } catch { return null; }
}

export function wsUrl(location, path, token) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}?token=${encodeURIComponent(token)}`;
}
