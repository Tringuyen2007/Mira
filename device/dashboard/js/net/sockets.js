// Socket hub: one ReconnectingSocket per channel, opened on first subscriber and stopped once the last leaves
// (events/notifications are opened by main.js for the whole session and never unsubscribed from).

import { ReconnectingSocket } from "./socket.js";

const PATHS = { events: "/ws/events", notifications: "/ws/notifications", live: "/ws/live", pose: "/ws/pose" };

function wsUrl(location, path, token) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}?token=${encodeURIComponent(token)}`;
}

export function createSocketHub({ location, token, therapistId, SocketImpl = ReconnectingSocket }) {
  const channels = new Map(); // channel -> { socket, listeners: Set<fn>, status }
  const statusListeners = new Set();
  let lastNotificationId = 0;
  let closed = false; // true once closeAll() has run; the hub refuses new subscriptions after that

  function urlFor(channel) {
    let url = wsUrl(location, PATHS[channel], token);
    if (channel === "notifications") {
      if (lastNotificationId > 0) url += `&since_id=${lastNotificationId}`;
      url += `&therapist_id=${encodeURIComponent(therapistId)}`;
    }
    return url;
  }

  function openChannel(channel) {
    const entry = { socket: null, listeners: new Set(), status: "closed" };
    const onMessage = (msg) => {
      if (channel === "notifications" && msg && msg.type === "notification" && typeof msg.id === "number") {
        lastNotificationId = Math.max(lastNotificationId, msg.id);
        // Keep the internally-reconnecting socket's URL current so the *next* reconnect (Wi-Fi blip, sleep,
        // server restart) resumes with since_id instead of silently dropping notifications created while down.
        entry.socket.url = urlFor(channel);
      }
      for (const fn of entry.listeners) fn(msg);
    };
    const onStatus = (status) => {
      entry.status = status;
      for (const fn of statusListeners) fn(channel, status);
    };
    entry.socket = new SocketImpl(urlFor(channel), { onMessage, onStatus });
    entry.socket.start();
    return entry;
  }

  return {
    subscribe(channel, fn) {
      if (closed) return () => {}; // hub is disposed; refuse to open anything new
      let entry = channels.get(channel);
      if (!entry) {
        entry = openChannel(channel);
        channels.set(channel, entry);
      }
      entry.listeners.add(fn);
      return () => {
        // A stale unsubscribe (called twice, or after this channel was closed and re-opened) must not touch
        // the *new* entry's listeners or stop its socket.
        if (channels.get(channel) !== entry) return;
        entry.listeners.delete(fn);
        if (entry.listeners.size === 0) {
          entry.socket.stop();
          channels.delete(channel);
        }
      };
    },
    status(channel) {
      return channels.get(channel)?.status || "closed";
    },
    onStatus(fn) {
      statusListeners.add(fn);
      return () => statusListeners.delete(fn);
    },
    send(channel, obj) {
      channels.get(channel)?.socket.send(obj);
    },
    noteNotificationId(id) {
      lastNotificationId = Math.max(lastNotificationId, id);
      const entry = channels.get("notifications");
      if (entry) entry.socket.url = urlFor("notifications");
    },
    closeAll() {
      closed = true;
      for (const entry of channels.values()) entry.socket.stop();
      channels.clear();
    },
  };
}
