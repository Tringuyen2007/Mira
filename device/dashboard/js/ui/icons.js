// Icon helper: <img src="assets/icons/<name>.svg">. Icons are loaded through <img> (CSP img-src 'self'), not
// inlined, so they cannot carry currentColor; the source SVGs are drawn with a fixed stroke/fill instead.
import { h } from "../core/dom.js";

export function icon(name, { label } = {}) {
  return h("img", {
    src: `assets/icons/${name}.svg`,
    alt: label || "",
    "aria-hidden": label ? null : "true",
  });
}
