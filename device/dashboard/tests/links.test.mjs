import { test } from "node:test";
import assert from "node:assert/strict";
import { safeInternalLink } from "../js/core/links.js";

test("only dashboard-internal links survive", () => {
  assert.equal(safeInternalLink("/dashboard#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b"),
    "#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b");
  assert.equal(safeInternalLink("#/proposals/3"), "#/proposals/3");
  for (const bad of [null, "", "https://evil.example/#/x", "javascript:alert(1)", "//evil/#/x",
    "/dashboard#/nope", "/device#/x", "/dashboard#/patients/../x"]) {
    assert.equal(safeInternalLink(bad), null, String(bad));
  }
});
