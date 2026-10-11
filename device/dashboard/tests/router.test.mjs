import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, href, redirectFor } from "../js/core/router.js";

test("parses every dashboard route", () => {
  assert.deepEqual(parseRoute(""), { name: "caseload", params: {}, query: {} });
  assert.deepEqual(parseRoute("#/caseload?filter=needs"), { name: "caseload", params: {}, query: { filter: "needs" } });
  assert.deepEqual(parseRoute("#/patients/maya"), { name: "patient", params: { pid: "maya" }, query: {} });
  assert.deepEqual(parseRoute("#/patients/maya/prescribe").name, "prescribe");
  assert.deepEqual(parseRoute("#/patients/maya/timelapse").params, { pid: "maya" });
  assert.deepEqual(parseRoute("#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6").params,
    { sid: "0590e71e-b7a4-4add-ab11-c14a27b7a1d6" });
  assert.deepEqual(parseRoute("#/proposals/12"), { name: "proposal", params: { id: "12" }, query: {} });
  assert.deepEqual(parseRoute("#/live/george").params, { pid: "george" });
  assert.equal(parseRoute("#/copilot").name, "copilot");
  assert.deepEqual(parseRoute("#/assistant"), { name: "assistant", params: {}, query: {} });
  assert.deepEqual(parseRoute("#/assistant?patient=maya"), { name: "assistant", params: {}, query: { patient: "maya" } });
  assert.equal(parseRoute("#/nope").name, "notfound");
});

test("the pid-less live route resolves to the live screen with no params", () => {
  assert.deepEqual(parseRoute("#/live"), { name: "live", params: {}, query: {} });
  assert.deepEqual(parseRoute("#/live?x=1"), { name: "live", params: {}, query: { x: "1" } });
  assert.equal(href("live"), "#/live");
  assert.equal(href("live", { pid: "george" }), "#/live/george");
});

test("rejects ids that are not plain ids", () => {
  assert.equal(parseRoute("#/patients/..%2Fx").name, "notfound");
  assert.equal(parseRoute("#/patients/a b").name, "notfound");
  assert.equal(parseRoute("#/proposals/abc").name, "notfound");
});

test("replay session ids must be a UUID", () => {
  assert.equal(parseRoute("#/replay/abc-1").name, "notfound");
  assert.equal(parseRoute("#/replay/----").name, "notfound");
  assert.equal(parseRoute("#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6").name, "replay");
});

test("href round-trips and encodes", () => {
  assert.equal(href("patient", { pid: "maya" }), "#/patients/maya");
  assert.equal(href("caseload", {}, { filter: "watch" }), "#/caseload?filter=watch");
  for (const h of [href("prescribe", { pid: "p02" }),
    href("replay", { sid: "0590e71e-b7a4-4add-ab11-c14a27b7a1d6" })]) {
    assert.notEqual(parseRoute(h).name, "notfound");
  }
});

test("assistant hrefs, and the legacy copilot route redirects to the assistant", () => {
  assert.equal(href("assistant"), "#/assistant");
  assert.equal(href("assistant", {}, { patient: "maya" }), "#/assistant?patient=maya");
  assert.equal(href("proposal", { id: 4 }), "#/proposals/4");
  assert.equal(redirectFor(parseRoute("#/copilot")), "#/assistant");
  assert.equal(redirectFor(parseRoute("#/copilot?patient=maya")), "#/assistant?patient=maya");
  assert.equal(redirectFor(parseRoute("#/assistant")), null);
  assert.equal(redirectFor(parseRoute("#/caseload")), null);
});
