// One WebSocket that reconnects with backoff (1, 2, 4 … 30 s + jitter), like the iOS app (LiveSocket.swift).
// Server pings are ignored. A policy close (1008) after open means "rejected": stop retrying. A token refused at
// the handshake looks like any network failure to a browser (close 1006), so it shows as "Reconnecting…".

const MAX_BACKOFF_MS = 30000;

export class ReconnectingSocket {
  constructor(url, { onMessage, onStatus, WebSocketImpl = globalThis.WebSocket, random = Math.random } = {}) {
    this.url = url;
    this.onMessage = onMessage;
    this.onStatus = onStatus;
    this.WebSocketImpl = WebSocketImpl;
    this.random = random;
    this.attempt = 0;
    this.ws = null;
    this.timer = null;
    this.stopped = false;
  }

  start() {
    if (this.ws && !this.stopped) return; // already connecting/connected
    this.stopped = false;
    this.connect();
  }

  connect() {
    let ws;
    try {
      ws = new this.WebSocketImpl(this.url);
    } catch {
      // Constructing the socket itself threw (e.g. a bad URL): treat it like a dropped connection.
      this.onStatus?.("closed");
      this.schedule();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (ws !== this.ws) return; // a late callback from a socket we've since replaced/stopped
      this.attempt = 0;
      this.onStatus?.("open");
    };
    ws.onmessage = (event) => {
      if (ws !== this.ws) return;
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (!msg || typeof msg !== "object" || msg.type === "ping") return;
      this.onMessage?.(msg);
    };
    ws.onclose = (event) => {
      if (ws !== this.ws) return;
      if (this.stopped) return;
      if (event.code === 1008) {
        this.onStatus?.("rejected");
        return;
      }
      this.onStatus?.("closed");
      this.schedule();
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  schedule() {
    const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt);
    this.attempt += 1;
    this.timer = setTimeout(() => this.connect(), base + this.random() * 500);
  }

  send(obj) {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj));
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }
}
