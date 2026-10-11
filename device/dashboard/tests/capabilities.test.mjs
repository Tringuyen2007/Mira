import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCapabilities } from "../js/net/capabilities.js";

test("has() checks openapi paths exactly", async () => {
  const fetchImpl = async () => ({ ok: true, json: async () => ({ paths: { "/pair": {}, "/patients/{patient_id}/messages": {} } }) });
  const caps = await loadCapabilities(fetchImpl);
  assert.equal(caps.has("/pair"), true);
  assert.equal(caps.has("/live/say"), false);
});

test("has() is always false when the probe fails", async () => {
  const caps = await loadCapabilities(async () => { throw new Error("offline"); });
  assert.equal(caps.has("/pair"), false);
});

test("has() is always false on a non-ok response", async () => {
  const caps = await loadCapabilities(async () => ({ ok: false, json: async () => ({ paths: {} }) }));
  assert.equal(caps.has("/pair"), false);
});
