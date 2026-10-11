// Socket hub: one ReconnectingSocket per channel, opened on first subscriber and stopped once the last leaves
// (events/notifications are opened by main.js for the whole session and never unsubscribed from).

import { ReconnectingSocket } from "./socket.js";

const PATHS = { events: "/ws/events", notifications: "/ws/notifications", live: "/ws/live", pose: "/ws/pose" };

function wsUrl(location, path, token) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}?token=${encodeURIComponent(token)}`;
}

export function createSocketHub({ location, token, therapistId, SocketImpl = ReconnectingSocket }) {
  const channels = new Map(); // channel -> { socket, listeners: Set<fn> }
  const statusListeners = new Set();
  let lastNotificationId = 0;

  function urlFor(channel) {
    let url = wsUrl(location, PATHS[channel], token);
    if (channel === "notifications") {
      if (lastNotificationId > 0) url += `&since_id=${lastNotificationId}`;
      url += `&therapist_id=${encodeURIComponent(therapistId)}`;
    }
    return url;
  }

  function openChannel(channel) {
    const onMessage = (msg) => {
      if (channel === "notifications" && msg && msg.type === "notification" && typeof msg.id === "number") {
        lastNotificationId = Math.max(lastNotificationId, msg.id);
      }
      for (const fn of channels.get(channel).listeners) fn(msg);
    };
    const onStatus = (status) => { for (const fn of statusListeners) fn(channel, status); };
    const socket = new SocketImpl(urlFor(channel), { onMessage, onStatus });
    socket.start();
    return socket;
  }

  return {
    subscribe(channel, fn) {
      let entry = channels.get(channel);
      if (!entry) {
        entry = { socket: null, listeners: new Set() };
        channels.set(channel, entry);
        entry.socket = openChannel(channel);
      }
      entry.listeners.add(fn);
      return () => {
        entry.listeners.delete(fn);
        if (entry.listeners.size === 0) {
          entry.socket.stop();
          channels.delete(channel);
        }
      };
    },
    status(channel) {
      return channels.get(channel) ? "open" : "closed";
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
    },
    closeAll() {
      for (const [channel, entry] of channels) {
        entry.socket.stop();
        channels.delete(channel);
      }
    },
  };
}
