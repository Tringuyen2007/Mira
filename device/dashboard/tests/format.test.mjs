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

test("invalid/missing dates and non-numeric degrees degrade to an em dash instead of throwing", () => {
  assert.equal(fmtDateLong("not a date"), "—");
  assert.equal(fmtWhen("not a date", now), "—");
  assert.equal(fmtDeg("abc"), "—");
  assert.equal(fmtPct("abc"), "—");
  assert.equal(fmtDeg(undefined), "—");
  assert.equal(fmtPct(Infinity), "—");
});

test("fmtWhenAt, patientMeta, capitalize and loadSplit", async () => {
  const { fmtWhenAt, patientMeta, capitalize, loadSplit } = await import("../js/core/format.js");
  const now = new Date("2026-10-11T12:00:00");
  assert.equal(fmtWhenAt("2026-10-11T09:10:00", now), "today at 9:10");
  assert.equal(fmtWhenAt("2026-10-10T18:05:00", now), "yesterday at 18:05");
  assert.equal(fmtWhenAt("2026-10-07T10:00:00", now), "Oct 7 at 10:00");
  assert.equal(capitalize("right knee replacement"), "Right knee replacement");
  assert.equal(patientMeta({ name: "Maya Ruiz", age: 67, condition: "right knee replacement",
    created_at: "2026-09-20T12:00:00" }, now), "Maya Ruiz, 67 · Right knee replacement · week 4");
  assert.deepEqual(loadSplit(47.5), { left: 48, right: 52 });
  assert.deepEqual(loadSplit(52.5), { left: 53, right: 47 });
  assert.equal(loadSplit(null), null);
});
