import { test } from "node:test";
import assert from "node:assert/strict";
import { createApi, ApiError } from "../js/net/api.js";

function fakeFetch(handler) {
  const calls = [];
  const f = async (url, init) => { calls.push({ url, init }); return handler(url, init); };
  f.calls = calls;
  return f;
}
const json = (status, body) => ({ ok: status < 300, status, statusText: "x", json: async () => body });

test("sends bearer + JSON and drops empty query values", async () => {
  const f = fakeFetch(() => json(200, { ok: true }));
  const api = createApi({ token: "t".repeat(24), fetchImpl: f });
  await api.get("/notifications", { unread: true, since_id: null });
  assert.equal(f.calls[0].url, "/notifications?unread=true");
  assert.equal(f.calls[0].init.headers.Authorization, `Bearer ${"t".repeat(24)}`);
  await api.post("/proposals/1/approve", { therapist_id: "okafor" });
  assert.equal(f.calls[1].init.method, "POST");
  assert.equal(f.calls[1].init.body, '{"therapist_id":"okafor"}');
  assert.equal(f.calls[1].init.headers["Content-Type"], "application/json");
});

test("401 signs out; other errors become ApiError with detail", async () => {
  let out = 0;
  const api = createApi({ token: "t".repeat(24), onUnauthorized: () => out++,
    fetchImpl: fakeFetch((u) => u.includes("401") ? json(401, { detail: "unauthorized" }) : json(409, { detail: "already decided" })) });
  await assert.rejects(api.get("/401"), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(out, 1);
  await assert.rejects(api.post("/409"), (e) => e.status === 409 && e.detail === "already decided");
});

test("network failure and timeout", async () => {
  const off = createApi({ token: "t".repeat(24), fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
  await assert.rejects(off.get("/x"), (e) => e.status === 0 && e.detail === "offline");
  const slow = createApi({ token: "t".repeat(24), timeoutMs: 5,
    fetchImpl: (u, init) => new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(Object.assign(new Error("a"), { name: "AbortError" })))) });
  await assert.rejects(slow.get("/x"), (e) => e.status === 0 && e.detail === "timeout");
});

test("a per-request timeout overrides the client default", async () => {
  const hang = (u, init) => new Promise((resolve, rej) => {
    const t = setTimeout(() => resolve(json(200, { ok: true })), 30);
    init.signal.addEventListener("abort", () => { clearTimeout(t); rej(Object.assign(new Error("a"), { name: "AbortError" })); });
  });
  const api = createApi({ token: "t".repeat(24), timeoutMs: 5, fetchImpl: hang });
  assert.deepEqual(await api.post("/slow", {}, { timeoutMs: 1000 }), { ok: true });
  assert.deepEqual(await api.get("/slow", {}, { timeoutMs: 1000 }), { ok: true });
  await assert.rejects(api.post("/slow", {}), (e) => e.status === 0 && e.detail === "timeout");
});
