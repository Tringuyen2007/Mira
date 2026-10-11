import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, href } from "../js/core/router.js";

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
  assert.equal(parseRoute("#/nope").name, "notfound");
});

test("rejects ids that are not plain ids", () => {
  assert.equal(parseRoute("#/patients/..%2Fx").name, "notfound");
  assert.equal(parseRoute("#/patients/a b").name, "notfound");
  assert.equal(parseRoute("#/proposals/abc").name, "notfound");
});

test("href round-trips and encodes", () => {
  assert.equal(href("patient", { pid: "maya" }), "#/patients/maya");
  assert.equal(href("caseload", {}, { filter: "watch" }), "#/caseload?filter=watch");
  for (const h of [href("prescribe", { pid: "p02" }), href("replay", { sid: "abc-1" })]) {
    assert.notEqual(parseRoute(h).name, "notfound");
  }
});
