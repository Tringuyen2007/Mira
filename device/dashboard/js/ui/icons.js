// Icon helper: inline <svg> built with svg() (no markup parsing), drawn in `currentColor`, so every icon follows
// the text colour of its context and both themes (light and projector) get a readable icon without per-theme
// image variants or CSS filters. Shapes are copied from assets/icons/*.svg (the Figma exports). Parts that are
// a knock-out on a filled shape (the "!" in the alert triangle, the tick in the check circle) use the
// `icon__cutout` class, which CSS paints with the card colour.
import { svg } from "../core/dom.js";

const S = "stroke"; // stroked in currentColor
const F = "fill";   // filled in currentColor
const C = "cutout"; // knock-out, painted by CSS

// name -> [viewBox, strokeWidth, [[tag, attrs, paint], ...]]
const ICONS = {
  bell: ["0 0 24 24", 1.8, [
    ["path", { d: "M6 10.5a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 14.5 6 10.5Z" }, S],
    ["path", { d: "M10 19a2 2 0 0 0 4 0" }, S]]],
  calendar: ["0 0 24 24", 1.8, [
    ["rect", { x: 3.5, y: 5, width: 17, height: 15.5, rx: 2 }, S],
    ["path", { d: "M3.5 9.5h17M8 2.5v5M16 2.5v5" }, S]]],
  caseload: ["0 0 24 24", 1.8, [
    ["rect", { x: 4, y: 3, width: 16, height: 18, rx: 2 }, S],
    ["path", { d: "M9 2.5h6a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-1a1 1 0 0 1 1-1Z" }, S],
    ["path", { d: "M7.5 11h9M7.5 14.5h9M7.5 18h5.5" }, S]]],
  chevron: ["0 0 24 24", 2, [["path", { d: "M9 5l7 7-7 7" }, S]]],
  copilot: ["0 0 24 24", 1.8, [
    ["path", { d: "M4 5h16v10H9l-4 3.5V15H4Z" }, S],
    ["path", { d: "M12 7.5c.4 1.7 1 2.3 2.7 2.7-1.7.4-2.3 1-2.7 2.7-.4-1.7-1-2.3-2.7-2.7 1.7-.4 2.3-1 2.7-2.7Z" }, F]]],
  diamond: ["0 0 24 24", 1.8, [["path", { d: "M12 3 21 12 12 21 3 12Z" }, S]]],
  heart: ["0 0 26 26", 1.8, [["path", { d: "M13 22.2C13 22.2 4.9 17.4 2.9 12.2C1.6 8.7 3.9 4.9 7.7 4.9C9.9 4.9 11.6 6.1 13 7.9C14.4 6.1 16.1 4.9 18.3 4.9C22.1 4.9 24.4 8.7 23.1 12.2C21.1 17.4 13 22.2 13 22.2Z" }, S]]],
  live: ["0 0 24 24", 1.8, [
    ["circle", { cx: 12, cy: 12, r: 2.2 }, F],
    ["path", { d: "M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4" }, S],
    ["path", { d: "M4.6 4.6a11 11 0 0 0 0 14.8M19.4 4.6a11 11 0 0 1 0 14.8" }, S]]],
  lock: ["0 0 24 24", 1.8, [
    ["rect", { x: 5, y: 10.5, width: 14, height: 10, rx: 2 }, S],
    ["path", { d: "M8 10.5V7a4 4 0 0 1 8 0v3.5" }, S],
    ["circle", { cx: 12, cy: 15, r: 1.4 }, F]]],
  message: ["0 0 24 24", 1.8, [
    ["path", { d: "M4 5h16v10H9l-4 3.5V15H4Z" }, S],
    ["path", { d: "M7.5 8.5h9M7.5 11.5h6" }, S]]],
  patient: ["0 0 24 24", 1.8, [
    ["circle", { cx: 12, cy: 8, r: 3.5 }, S],
    ["path", { d: "M4.5 20.5c0-4.1 3.4-7.5 7.5-7.5s7.5 3.4 7.5 7.5" }, S]]],
  play: ["0 0 24 24", 1.8, [["path", { d: "M7 4.5v15l13-7.5Z" }, S]]],
  prescribe: ["0 0 24 24", 1.8, [
    ["rect", { x: 3, y: 12.5, width: 18, height: 7, rx: 3.5, transform: "rotate(-45 12 16)" }, S],
    ["path", { d: "M9.5 13.5l5 5" }, S],
    ["path", { d: "M4 3h3M5.5 1.5v3" }, S]]],
  search: ["0 0 24 24", 1.8, [
    ["circle", { cx: 10.5, cy: 10.5, r: 6.5 }, S],
    ["path", { d: "M15.3 15.3 20.5 20.5" }, S]]],
  timelapse: ["0 0 24 24", 1.8, [
    ["circle", { cx: 12, cy: 12.5, r: 8.5 }, S],
    ["path", { d: "M12 7.5v5l3.5 2" }, S],
    ["path", { d: "M9 1.5h6" }, S]]],
  "alert-triangle": ["0 0 28 26", 2, [
    ["path", { d: "M14 2L26.5 24H1.5L14 2Z", "stroke-linejoin": "round" }, "fill-stroke"],
    ["path", { d: "M15.3 10.3C15.3 9.58 14.718 9 14 9C13.282 9 12.7 9.58 12.7 10.3V15.7C12.7 16.418 13.282 17 14 17C14.718 17 15.3 16.418 15.3 15.7V10.3Z" }, C],
    ["circle", { cx: 14, cy: 20.2, r: 1.5 }, C]]],
  "check-circle": ["0 0 22 22", 2.4, [
    ["circle", { cx: 11, cy: 11, r: 11 }, F],
    ["path", { d: "M6 11.5L9.5 15L16 7.5" }, "cutout-stroke"]]],
};

function part([tag, attrs, paint], strokeWidth) {
  const a = { ...attrs };
  if (paint === S) Object.assign(a, { fill: "none", stroke: "currentColor", "stroke-width": strokeWidth });
  else if (paint === F) Object.assign(a, { fill: "currentColor", stroke: "none" });
  else if (paint === "fill-stroke") Object.assign(a, { fill: "currentColor", stroke: "currentColor", "stroke-width": strokeWidth });
  else if (paint === C) Object.assign(a, { class: "icon__cutout", stroke: "none" });
  else if (paint === "cutout-stroke") Object.assign(a, { class: "icon__cutout-stroke", fill: "none", "stroke-width": strokeWidth });
  return svg(tag, a);
}

export const ICON_NAMES = Object.freeze(Object.keys(ICONS));

export function icon(name, { label } = {}) {
  const def = ICONS[name] || ICONS.diamond;
  const [viewBox, strokeWidth, parts] = def;
  return svg("svg", {
    class: `icon icon--${name}`, viewBox, focusable: "false",
    "stroke-linecap": "round", "stroke-linejoin": "round",
    role: label ? "img" : null, "aria-label": label || null, "aria-hidden": label ? null : "true",
  }, parts.map((p) => part(p, strokeWidth)));
}
