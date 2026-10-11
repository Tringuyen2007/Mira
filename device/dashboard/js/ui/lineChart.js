// Line chart as plain SVG (no library, no inline styles: colours and the dashed second line live in
// css/screens/assistant.css under .chart__*). chart = {title, xLabel, yLabel, series: [{name, points: [{x, y}]}]}.
// x is an ISO date ("2026-09-19…"), a number, or any label (then plotted by index). Shared by the Mira Assistant
// and, later, Patient review.
import { svg } from "../core/dom.js";

const W_DEFAULT = 560;
const H_DEFAULT = 260;
const PAD = { top: 36, right: 16, bottom: 32, left: 44 };
const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;
const Y_TICKS = 4;
const MAX_X_TICKS = 5;

const fmtDay = (d) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(d);

function xKind(series) {
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  if (xs.every((x) => typeof x === "string" && ISO_DAY.test(x) && !Number.isNaN(Date.parse(x.slice(0, 10))))) {
    return "date";
  }
  if (xs.every((x) => typeof x === "number" && Number.isFinite(x))) return "number";
  return "label";
}

function fmtNum(n) {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

export function chartSummary(chart) {
  const kind = xKind(chart.series);
  const xText = (x) => (kind === "date" ? fmtDay(new Date(Date.parse(String(x).slice(0, 10)))) : String(x));
  const word = kind === "date" ? "on" : "at";
  const parts = chart.series.map((s) => {
    const a = s.points[0];
    const b = s.points[s.points.length - 1];
    return `${s.name}: ${fmtNum(a.y)} ${word} ${xText(a.x)} to ${fmtNum(b.y)} ${word} ${xText(b.x)}`;
  });
  return `${chart.title ? `${chart.title}. ` : ""}${parts.join("; ")}`;
}

function niceRange(min, max) {
  if (min === max) return [min - 1, max + 1];
  const pad = (max - min) * 0.1;
  return [Math.floor(min - pad), Math.ceil(max + pad)];
}

// opts.width/height set the viewBox (default 560 × 260). A card narrower than 560 px should pass its own width
// so the 12 px tick labels aren't scaled down below 12 px (browser bug 7: the Patient review trend).
export function lineChart(chart, { width = W_DEFAULT, height = H_DEFAULT } = {}) {
  const W = width;
  const H = height;
  const series = chart.series;
  const kind = xKind(series);
  const labels = kind === "label" ? [...new Set(series.flatMap((s) => s.points.map((p) => String(p.x))))] : [];
  const xVal = (x) => (kind === "date" ? Date.parse(String(x).slice(0, 10))
    : kind === "number" ? x : labels.indexOf(String(x)));
  const xsAll = series.flatMap((s) => s.points.map((p) => xVal(p.x)));
  const ysAll = series.flatMap((s) => s.points.map((p) => p.y));
  const x0 = Math.min(...xsAll);
  const x1 = Math.max(...xsAll);
  const [y0, y1] = niceRange(Math.min(...ysAll), Math.max(...ysAll));
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const sx = (v) => PAD.left + (x1 === x0 ? plotW / 2 : ((v - x0) / (x1 - x0)) * plotW);
  const sy = (v) => PAD.top + plotH - ((v - y0) / (y1 - y0)) * plotH;
  const r1 = (n) => Math.round(n * 10) / 10;

  const grid = [];
  for (let i = 0; i <= Y_TICKS; i++) {
    const v = y0 + ((y1 - y0) * i) / Y_TICKS;
    const y = r1(sy(v));
    grid.push(svg("line", { class: "chart__grid", x1: PAD.left, x2: W - PAD.right, y1: y, y2: y }));
    grid.push(svg("text", { class: "chart__ytick", x: PAD.left - 6, y: y + 4, "text-anchor": "end" }, fmtNum(r1(v))));
  }

  // X ticks: the distinct x values, thinned to at most MAX_X_TICKS, always keeping the first and last.
  const distinct = [...new Map(series.flatMap((s) => s.points.map((p) => [xVal(p.x), p.x]))).entries()]
    .sort((a, b) => a[0] - b[0]);
  const step = Math.max(1, Math.ceil((distinct.length - 1) / (MAX_X_TICKS - 1)));
  const ticks = distinct.filter((_, i) => i % step === 0 || i === distinct.length - 1);
  const xTicks = ticks.map(([v, raw]) => svg("text", {
    class: "chart__xtick", x: r1(sx(v)), y: H - PAD.bottom + 18, "text-anchor": "middle",
  }, kind === "date" ? fmtDay(new Date(v)) : String(raw)));

  const lines = series.map((s, i) => {
    const d = s.points.map((p, j) => `${j === 0 ? "M" : "L"}${r1(sx(xVal(p.x)))} ${r1(sy(p.y))}`).join(" ");
    return svg("path", { class: `chart__line chart__line--${i % 4}`, d });
  });
  const dots = series.flatMap((s, i) => s.points.map((p) => svg("circle", {
    class: `chart__dot chart__dot--${i % 4}`, cx: r1(sx(xVal(p.x))), cy: r1(sy(p.y)), r: 3,
  })));

  // Legend across the top; item width estimated from the label length (no layout measuring in a pure builder).
  let lx = PAD.left;
  const legend = series.map((s, i) => {
    const g = svg("g", { class: "chart__legend-item" },
      svg("line", { class: `chart__swatch chart__swatch--${i % 4}`, x1: lx, x2: lx + 20, y1: 14, y2: 14 }),
      svg("text", { class: "chart__legend-text", x: lx + 26, y: 18 }, s.name));
    lx += 26 + s.name.length * 7 + 20;
    return g;
  });

  return svg("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": chartSummary(chart),
    preserveAspectRatio: "xMidYMid meet",
  },
    svg("g", { "aria-hidden": "true" }, grid, xTicks, lines, dots, legend));
}
