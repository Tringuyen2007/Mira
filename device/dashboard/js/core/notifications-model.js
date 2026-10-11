// Notifications bell/panel model: dedupe REST + live WS rows by id, newest-first, capped to the panel's latest
// 20; unread/urgent helpers; every row's `link` goes through safeInternalLink (spec "Security").
import { safeInternalLink } from "./links.js";

const PANEL_LIMIT = 20;

export function linkFor(notif) {
  return safeInternalLink(notif && notif.link);
}

export function isUrgent(notif) {
  return Boolean(notif) && notif.severity === "urgent";
}

export function isUnacked(notif) {
  return Boolean(notif) && notif.acked_at == null;
}

export function unreadCount(list) {
  return (list || []).filter((n) => n && n.read_at == null).length;
}

// `incoming` is one row or an array of rows (REST list at boot, a single WS push later). A row with the same id
// already in `list` is replaced by the newer copy (a WS push can re-deliver a row after its own read/ack REST
// call changed it). Sorted newest-first by time and capped to PANEL_LIMIT.
export function mergeNotifications(list, incoming) {
  const byId = new Map();
  for (const n of list || []) if (n && typeof n.id === "number") byId.set(n.id, n);
  const rows = Array.isArray(incoming) ? incoming : [incoming];
  for (const n of rows) {
    if (!n || typeof n.id !== "number") continue;
    byId.set(n.id, { ...byId.get(n.id), ...n });
  }
  return [...byId.values()].sort((a, b) => b.time - a.time).slice(0, PANEL_LIMIT);
}
