// DOM builder. Text is always a text node: server strings (names, AI drafts, Copilot answers) can never become markup.
const SVG_NS = "http://www.w3.org/2000/svg";

// Every attribute name that can carry a URL, checked case-insensitively so HREF/Src/XLink:Href etc. can't slip
// past the guard. No absolute-origin escape hatch: an in-app hash route, a relative ./ or bare assets/vendor/
// path, or a same-origin /dashboard/... path only. No scheme, no protocol-relative "//", no ../ traversal.
const URL_ATTR_NAMES = new Set(["href", "src", "xlink:href", "formaction", "action", "srcset", "poster"]);
const SAFE_URL = /^(#\/|\.\/|assets\/|vendor\/|\/dashboard\/)/;
const HAS_TRAVERSAL = /(^|\/)\.\.(\/|$)/;

function isSafeUrl(v) {
  const s = String(v);
  return SAFE_URL.test(s) && !HAS_TRAVERSAL.test(s);
}

function build(el, attrs, children) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    const lower = k.toLowerCase();
    if (lower === "style") throw new Error("style attribute not allowed (CSP style-src 'self'); use a class");
    if (URL_ATTR_NAMES.has(lower) && !isSafeUrl(v)) throw new Error(`${k} not allowed: ${v}`);
    if (lower.startsWith("on")) {
      if (typeof v !== "function") throw new Error(`${k} must be a function, not a string (no inline handlers)`);
      el.addEventListener(lower.slice(2), v);
      continue;
    }
    if (lower === "class") el.setAttribute("class", String(v));
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
}

export const h = (tag, attrs, ...children) => build(document.createElement(tag), attrs, children);
export const svg = (tag, attrs, ...children) => build(document.createElementNS(SVG_NS, tag), attrs, children);
export const clear = (el) => { el.replaceChildren(); return el; };
