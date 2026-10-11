import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// WCAG 2.x contrast of the design tokens in both themes (final review B, I6): text >= 4.5:1, icons and fills
// that carry meaning >= 3:1. Parses css/tokens.css; the projector block overrides the light :root values.
const css = readFileSync(new URL("../css/tokens.css", import.meta.url), "utf8");

function block(selectorRe) {
  const out = {};
  for (const m of css.matchAll(new RegExp(`${selectorRe}\\s*\\{([^}]*)\\}`, "g"))) {
    for (const d of m[1].replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
      out[d[1]] = d[2].trim();
    }
  }
  return out;
}

const light = block(":root(?!\\[)");
const projector = { ...light, ...block(':root\\[data-theme="projector"\\]') };

function resolve(tokens, name) {
  let v = tokens[name];
  for (let i = 0; i < 5 && v && v.startsWith("var("); i++) v = tokens[v.slice(4, -1).trim()];
  assert.match(v || "", /^#[0-9a-f]{6}$/i, `${name} should resolve to a hex colour, got ${v}`);
  return v;
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT = ["--color-ink", "--color-ink-soft", "--color-accent", "--color-accent-text-small", "--color-berry",
  "--color-alert-text", "--color-watch-text"];
const NON_TEXT = ["--color-alert", "--color-flag", "--color-good", "--color-watch", "--color-accent"];
const ON_FILL = [["--color-on-accent", "--color-accent"], ["--color-on-accent", "--color-accent-text-small"],
  ["--color-on-alert", "--color-alert"], ["--color-on-alert", "--color-flag"], ["--color-on-ink", "--color-ink"],
  ["--color-alert-text", "--color-alert-bg"]];

for (const [themeName, tokens, surfaces] of [
  ["light", light, ["--color-card", "--color-card-sunk", "--color-canvas"]],
  ["projector", projector, ["--color-card", "--color-card-sunk", "--canvas"]],
]) {
  test(`${themeName}: text tokens reach 4.5:1 on every surface`, () => {
    for (const fg of TEXT) for (const bg of surfaces) {
      const r = ratio(resolve(tokens, fg), resolve(tokens, bg));
      assert.ok(r >= 4.5, `${themeName} ${fg} on ${bg} is ${r.toFixed(2)}:1`);
    }
  });

  test(`${themeName}: icon and fill tokens reach 3:1 on every surface`, () => {
    for (const fg of NON_TEXT) for (const bg of surfaces) {
      const r = ratio(resolve(tokens, fg), resolve(tokens, bg));
      assert.ok(r >= 3, `${themeName} ${fg} on ${bg} is ${r.toFixed(2)}:1`);
    }
  });

  test(`${themeName}: text on filled controls reaches 4.5:1`, () => {
    for (const [fg, bg] of ON_FILL) {
      const r = ratio(resolve(tokens, fg), resolve(tokens, bg));
      assert.ok(r >= 4.5, `${themeName} ${fg} on ${bg} is ${r.toFixed(2)}:1`);
    }
  });
}
