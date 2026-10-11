// Kiosk configuration. The device token arrives once in the URL fragment (#token=…, never sent to the server
// or written to its logs), is kept in localStorage on the kiosk, and the fragment is removed from the address bar.

export const TOKEN_KEY = "mira.deviceToken";

export function readToken(location, storage, history) {
  const params = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  const fromHash = params.get("token");
  if (fromHash) {
    try { storage.setItem(TOKEN_KEY, fromHash); } catch { /* private mode: keep it for this load only */ }
    history.replaceState(null, "", location.pathname + (location.search || ""));
    return fromHash;
  }
  try { return storage.getItem(TOKEN_KEY); } catch { return null; }
}

export function wsUrl(location, path, token) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}?token=${encodeURIComponent(token)}`;
}
