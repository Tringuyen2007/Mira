import { test } from "node:test";
import assert from "node:assert/strict";
import { fmtDateLong, fmtWhen, fmtDeg, fmtPct, initials, weeksSince, firstName } from "../js/core/format.js";

const now = new Date("2026-10-11T15:00:00");
test("formats", () => {
  assert.equal(fmtDateLong(now), "Sunday, October 11");
  assert.equal(fmtWhen("2026-10-11T09:10:00", now), "today, 9:10");
  assert.equal(fmtWhen("2026-10-10T18:00:00", now), "yesterday");
  assert.equal(fmtWhen("2026-10-07T10:00:00", now), "Oct 7");
  assert.equal(fmtDeg(91.6), "92°");
  assert.equal(fmtPct(35.99), "36%");
  assert.equal(fmtDeg(null), "—");
  assert.equal(initials("Dr. Sam Lee, PT"), "SL");
  assert.equal(firstName("Maya Ellis"), "Maya");
  assert.equal(weeksSince("2026-09-20T00:00:00", now), 3);
});
