import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { lineChart, chartSummary } = await import("../js/ui/lineChart.js");
const { answerModel } = await import("../js/screens/assistant/model.js");

const realAsk = JSON.parse(readFileSync(new URL("../../../server/samples/rest_post_copilot_ask.json", import.meta.url), "utf8"));
const chart = answerModel(realAsk).charts[0];

function walk(el, fn) {
  fn(el);
  for (const c of el.children || []) walk(c, fn);
}

test("an svg with role=img and a label summarising each series first to last", () => {
  const el = lineChart(chart);
  assert.equal(el.tagName, "SVG");
  assert.equal(el.getAttribute("role"), "img");
  const label = el.getAttribute("aria-label");
  assert.match(label, /^Maya Ellis: squat depth vs load on the affected leg\./);
  assert.match(label, /p95 knee depth \(°\): 57\.7 on Sep 19 to 65\.6 on Sep 24/);
  assert.match(label, /affected-leg load \(%\): 48\.2 on Sep 19 to 44\.6 on Sep 24/);
  assert.equal(chartSummary(chart), label);
  assert.ok(el.getAttribute("viewBox"));
});

test("one path per series: the first solid, the second dashed (by class)", () => {
  const el = lineChart(chart);
  const lines = el.querySelectorAll(".chart__line");
  assert.equal(lines.length, 2);
  assert.match(lines[0].getAttribute("class"), /chart__line--0/);
  assert.match(lines[1].getAttribute("class"), /chart__line--1/);
  assert.match(lines[0].getAttribute("d"), /^M[\d.]+ [\d.]+( L[\d.]+ [\d.]+){3}$/);
});

test("date ticks on the x axis and a legend, no inline styles", () => {
  const el = lineChart(chart);
  const xticks = el.querySelectorAll(".chart__xtick").map((t) => t.textContent);
  assert.ok(xticks.length >= 2);
  assert.equal(xticks[0], "Sep 19");
  assert.equal(xticks.at(-1), "Sep 24");
  assert.ok(el.querySelectorAll(".chart__ytick").length >= 3);
  const legend = el.querySelectorAll(".chart__legend-text").map((t) => t.textContent);
  assert.deepEqual(legend, ["p95 knee depth (°)", "affected-leg load (%)"]);
  walk(el, (n) => assert.equal(n.getAttribute("style"), null));
});

test("numeric x values and a single point do not throw", () => {
  const el = lineChart({ title: "t", series: [{ name: "a", points: [{ x: 1, y: 5 }] }] });
  assert.equal(el.querySelectorAll(".chart__line").length, 1);
  assert.match(el.getAttribute("aria-label"), /a: 5 at 1 to 5 at 1/);
});

test("a two-point series at a narrow width anchors ticks so they can't overlap", () => {
  const twoDay = {
    title: "t",
    series: [{ name: "a", points: [{ x: "2026-10-10", y: 1 }, { x: "2026-10-11", y: 2 }] }],
  };
  const el = lineChart(twoDay, { width: 200, height: 120 });
  const xticks = el.querySelectorAll(".chart__xtick");
  assert.equal(xticks.length, 2);
  assert.equal(xticks[0].textContent, "Oct 10");
  assert.equal(xticks[0].getAttribute("text-anchor"), "start");
  assert.equal(xticks.at(-1).textContent, "Oct 11");
  assert.equal(xticks.at(-1).getAttribute("text-anchor"), "end");
});

test("a narrow 3-week daily trend never places two kept ticks within 48 chart units", () => {
  const points = Array.from({ length: 21 }, (_, i) => {
    const d = new Date(Date.UTC(2026, 8, 20 + i));
    return { x: d.toISOString().slice(0, 10), y: i };
  });
  const el = lineChart({ title: "t", series: [{ name: "a", points }] }, { width: 220, height: 120 });
  const xticks = el.querySelectorAll(".chart__xtick");
  assert.ok(xticks.length <= 4);
  const xs = xticks.map((t) => Number(t.getAttribute("x")));
  for (let i = 1; i < xs.length; i++) assert.ok(xs[i] - xs[i - 1] >= 48, `ticks ${i - 1},${i} too close: ${xs}`);
  assert.equal(xticks[0].getAttribute("text-anchor"), "start");
  assert.equal(xticks.at(-1).getAttribute("text-anchor"), "end");
});

test("labels are text nodes even when they look like markup", () => {
  const el = lineChart({ title: "<img src=x onerror=alert(1)>", series: [{ name: "<b>x</b>", points: [{ x: "2026-01-01", y: 1 }, { x: "2026-01-02", y: 2 }] }] });
  const legend = el.querySelector(".chart__legend-text");
  assert.equal(legend.childNodes[0].nodeType, 3);
  assert.equal(legend.textContent, "<b>x</b>");
});
