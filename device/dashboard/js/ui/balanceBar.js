// Left/right load-share bar (Figma "This session"/room card): two proportional bars with percent labels, a
// red bar + "!" marker on the affected side when `flagged`. Pure builder: balanceBar({affected, other,
// affectedSide, flagged}) -> HTMLElement. affected/other are percents (0-100, needn't sum to 100).
// Bar widths are SVG rect widths (presentation attributes, not the forbidden `style` attribute: CSP
// `style-src 'self'` and dom.js both block inline styles), coloured entirely by CSS classes.
import { h, svg } from "../core/dom.js";
import { fmtPct } from "../core/format.js";

const TRACK_W = 200;
const TRACK_H = 14;

function sideLabel(side) {
  return side === "left" ? "Left" : "Right";
}

export function balanceBar({ affected, other, affectedSide, flagged }) {
  const otherSide = affectedSide === "left" ? "right" : "left";
  const a = Math.max(0, Number(affected) || 0);
  const o = Math.max(0, Number(other) || 0);
  const total = a + o > 0 ? a + o : 1;
  const aw = Math.round((a / total) * TRACK_W);
  const ow = TRACK_W - aw;
  const label = `${sideLabel(affectedSide)} ${fmtPct(affected)}, ${sideLabel(otherSide)} ${fmtPct(other)}`;
  return h("div", { class: `balance-bar${flagged ? " balance-bar--flagged" : ""}` },
    svg("svg", {
      class: "balance-bar__track", viewBox: `0 0 ${TRACK_W} ${TRACK_H}`, width: "100%", height: TRACK_H,
      role: "img", "aria-label": label,
    },
      svg("rect", {
        class: "balance-bar__fill balance-bar__fill--affected", x: 0, y: 0, width: aw, height: TRACK_H,
      }),
      svg("rect", {
        class: "balance-bar__fill balance-bar__fill--other", x: aw, y: 0, width: ow, height: TRACK_H,
      })),
    h("div", { class: "balance-bar__labels", "aria-hidden": "true" },
      h("span", { class: "balance-bar__label balance-bar__label--affected" },
        flagged ? h("span", { class: "balance-bar__warn" }, "⚠") : null,
        `${sideLabel(affectedSide)} ${fmtPct(affected)}`),
      h("span", { class: "balance-bar__label" }, `${sideLabel(otherSide)} ${fmtPct(other)}`)));
}
