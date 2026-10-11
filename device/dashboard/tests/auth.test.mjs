import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAuth, saveAuth, clearAuth, AUTH_KEY } from "../js/net/auth.js";

const mem = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k), m }; };

test("round trip, validation, clear", () => {
  const s = mem();
  assert.equal(loadAuth(s), null);
  assert.throws(() => saveAuth(s, { token: "short", therapistId: "okafor" }), /too short/);
  assert.throws(() => saveAuth(s, { token: "x".repeat(24), therapistId: "a b" }), /therapist/);
  saveAuth(s, { token: "x".repeat(24), therapistId: "okafor" });
  assert.deepEqual(loadAuth(s), { token: "x".repeat(24), therapistId: "okafor" });
  s.setItem(AUTH_KEY, "{not json");
  assert.equal(loadAuth(s), null);
  saveAuth(s, { token: "x".repeat(24), therapistId: "okafor" });
  clearAuth(s);
  assert.equal(s.m.size, 0);
});
