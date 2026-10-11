import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("dashboard socket.js stays identical to the kiosk's ReconnectingSocket", () => {
  const kiosk = readFileSync(new URL("../../kiosk/js/socket.js", import.meta.url), "utf8");
  const dash = readFileSync(new URL("../js/net/socket.js", import.meta.url), "utf8");
  assert.equal(dash, kiosk, "fix both copies together (device/kiosk/js/socket.js is the source)");
});
