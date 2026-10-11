import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mergeNotifications, unreadCount, linkFor, isUrgent, isUnacked } from "../js/core/notifications-model.js";

const sample = JSON.parse(readFileSync(new URL("../../../server/samples/rest_get_notifications.json", import.meta.url)));
const rows = sample.notifications;

test("mergeNotifications dedupes by id, newest-first, and caps to the panel's latest 20", () => {
  const merged = mergeNotifications([], rows);
  assert.deepEqual(merged.map((n) => n.id), [3, 2, 1]);
  const again = mergeNotifications(merged, rows[0]);
  assert.equal(again.length, 3, "re-delivering the same id does not duplicate it");
  const many = Array.from({ length: 25 }, (_, i) => ({ id: i, time: i, kind: "milestone", severity: "low",
    title: "t", link: null, read_at: null, acked_at: null }));
  assert.equal(mergeNotifications([], many).length, 20);
});

test("mergeNotifications keeps the newest copy of a row (e.g. after a read/ack update arrives by id)", () => {
  const merged = mergeNotifications([], rows);
  const updated = { ...rows[0], read_at: 1791667100 };
  const next = mergeNotifications(merged, updated);
  assert.equal(next.find((n) => n.id === rows[0].id).read_at, 1791667100);
});

test("unreadCount counts rows with no read_at", () => {
  assert.equal(unreadCount(rows), 3);
  assert.equal(unreadCount([{ id: 1, read_at: null }, { id: 2, read_at: 123 }]), 1);
  assert.equal(unreadCount([]), 0);
});

test("linkFor filters every notification link through safeInternalLink", () => {
  assert.equal(linkFor(rows[0]), "#/proposals/7");
  assert.equal(linkFor({ link: "https://evil.example/x" }), null);
  assert.equal(linkFor({ link: null }), null);
  assert.equal(linkFor({}), null);
});

test("isUrgent / isUnacked detect the alert row and ignore the normal ones", () => {
  const alertRow = rows.find((n) => n.kind === "alert");
  const milestoneRow = rows.find((n) => n.kind === "milestone");
  assert.equal(isUrgent(alertRow), true);
  assert.equal(isUnacked(alertRow), true);
  assert.equal(isUrgent(milestoneRow), false);
  const acked = { ...alertRow, acked_at: 123 };
  assert.equal(isUnacked(acked), false);
});
