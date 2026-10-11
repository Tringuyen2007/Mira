// DOM builder. Text is always a text node: server strings (names, AI drafts, Copilot answers) can never become markup.
const SVG_NS = "http://www.w3.org/2000/svg";
const SAFE_HREF = /^(#\/|\.\/|\/dashboard\/|https?:\/\/[^/]*\/dashboard\/)/;

function build(el, attrs, children) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "style") throw new Error("style attribute not allowed (CSP style-src 'self'); use a class");
    if (k === "href" && !SAFE_HREF.test(String(v))) throw new Error(`href not allowed: ${v}`);
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.setAttribute("class", String(v));
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
