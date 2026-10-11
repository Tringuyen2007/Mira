import { test } from "node:test";
import assert from "node:assert/strict";
import { createSocketHub } from "../js/net/sockets.js";

class FakeSocket {
  constructor(url, opts) {
    this.url = url;
    this.opts = opts;
    this.onMessage = opts.onMessage;
    this.onStatus = opts.onStatus;
    this.startCount = 0;
    this.stopCount = 0;
    this.sent = [];
    FakeSocket.instances.push(this);
  }
  start() { this.startCount += 1; }
  stop() { this.stopCount += 1; }
  send(obj) { this.sent.push(obj); }
}
FakeSocket.instances = [];

const location = { protocol: "http:", host: "localhost:8000" };

test("subscribe opens one socket per channel for the first subscriber, builds the right url", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "tok en", therapistId: "okafor", SocketImpl: FakeSocket });
  const seen1 = [];
  const seen2 = [];
  const unsub1 = hub.subscribe("live", (m) => seen1.push(m));
  const unsub2 = hub.subscribe("live", (m) => seen2.push(m));
  assert.equal(FakeSocket.instances.length, 1);
  assert.equal(FakeSocket.instances[0].url, "ws://localhost:8000/ws/live?token=tok%20en");
  assert.equal(FakeSocket.instances[0].startCount, 1);

  // (2) messages fan out to both subscribers
  FakeSocket.instances[0].onMessage({ type: "live", foo: "bar" });
  assert.deepEqual(seen1, [{ type: "live", foo: "bar" }]);
  assert.deepEqual(seen2, [{ type: "live", foo: "bar" }]);

  // (3) the socket stops when both unsubscribe
  unsub1();
  assert.equal(FakeSocket.instances[0].stopCount, 0);
  unsub2();
  assert.equal(FakeSocket.instances[0].stopCount, 1);
});

test("notifications url carries since_id from noteNotificationId, and therapist_id", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  const unsub = hub.subscribe("notifications", () => {});
  assert.equal(FakeSocket.instances[0].url,
    `ws://localhost:8000/ws/notifications?token=${"t".repeat(24)}&therapist_id=okafor`);
  unsub();

  hub.noteNotificationId(42);
  hub.subscribe("notifications", () => {});
  assert.equal(FakeSocket.instances[1].url,
    `ws://localhost:8000/ws/notifications?token=${"t".repeat(24)}&since_id=42&therapist_id=okafor`);
});

test("the hub updates the highest notification id automatically from incoming messages", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  const unsub = hub.subscribe("notifications", () => {});
  FakeSocket.instances[0].onMessage({ type: "notification", id: 10 });
  FakeSocket.instances[0].onMessage({ type: "notification", id: 5 });
  unsub();
  hub.subscribe("notifications", () => {});
  assert.equal(FakeSocket.instances[1].url,
    `ws://localhost:8000/ws/notifications?token=${"t".repeat(24)}&since_id=10&therapist_id=okafor`);
});

test("onStatus fans out (channel, status) for every socket", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  const events = [];
  hub.onStatus((channel, status) => events.push([channel, status]));
  hub.subscribe("events", () => {});
  FakeSocket.instances[0].onStatus("open");
  assert.deepEqual(events, [["events", "open"]]);
});

test("send(channel, obj) forwards to that channel's socket", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("events", () => {});
  hub.send("events", { type: "ack" });
  assert.deepEqual(FakeSocket.instances[0].sent, [{ type: "ack" }]);
});

test("the notifications socket's url is refreshed with since_id after a message, without unsubscribing", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("notifications", () => {});
  assert.doesNotMatch(FakeSocket.instances[0].url, /since_id/);
  FakeSocket.instances[0].onMessage({ type: "notification", id: 42 });
  // Same instance (ReconnectingSocket.connect() re-reads this.url on every internal reconnect attempt).
  assert.equal(FakeSocket.instances.length, 1);
  assert.match(FakeSocket.instances[0].url, /since_id=42/);
});

test("noteNotificationId also refreshes an already-open notifications socket's url", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("notifications", () => {});
  hub.noteNotificationId(7);
  assert.match(FakeSocket.instances[0].url, /since_id=7/);
});

test("subscribe after closeAll is refused: no new socket is opened and the returned unsubscribe is a safe no-op", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("events", () => {});
  hub.closeAll();
  assert.equal(FakeSocket.instances.length, 1);
  const unsub = hub.subscribe("events", () => {});
  assert.equal(FakeSocket.instances.length, 1, "closeAll() must refuse new subscriptions");
  assert.doesNotThrow(() => unsub());
  assert.doesNotThrow(() => hub.send("events", { type: "ack" }));
});

test("a stale unsubscribe (double-call, or after re-subscribe) does not stop the new channel entry", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  const unsubA = hub.subscribe("events", () => {});
  unsubA();
  assert.equal(FakeSocket.instances[0].stopCount, 1);
  hub.subscribe("events", () => {}); // re-opens a fresh socket for "events"
  assert.equal(FakeSocket.instances.length, 2);
  unsubA(); // stale: must not touch the new entry
  assert.equal(FakeSocket.instances[1].stopCount, 0);
});

test("status() reflects the last onStatus value, not just whether the channel exists", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("events", () => {});
  assert.equal(hub.status("events"), "closed");
  FakeSocket.instances[0].onStatus("open");
  assert.equal(hub.status("events"), "open");
  assert.equal(hub.status("live"), "closed");
});

test("closeAll stops every open socket", () => {
  FakeSocket.instances = [];
  const hub = createSocketHub({ location, token: "t".repeat(24), therapistId: "okafor", SocketImpl: FakeSocket });
  hub.subscribe("events", () => {});
  hub.subscribe("live", () => {});
  hub.subscribe("pose", () => {});
  hub.closeAll();
  for (const s of FakeSocket.instances) assert.equal(s.stopCount, 1);
});
