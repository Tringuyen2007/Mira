import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";
installFakeDom();
const { h, clear } = await import("../js/core/dom.js");

test("h sets text via text nodes, never parses HTML", () => {
  const el = h("p", { class: "x", "data-k": "1" }, "<img src=x onerror=1>", 3, null, false, ["a", "b"]);
  assert.equal(el.className, "x");
  assert.equal(el.getAttribute("data-k"), "1");
  assert.equal(el.textContent, "<img src=x onerror=1>3ab");
  assert.equal(el.children.length, 0);
});

test("on* attributes become listeners; style is refused", () => {
  let n = 0;
  const b = h("button", { onClick: () => n++ }, "Go");
  b.dispatch("click");
  assert.equal(n, 1);
  assert.throws(() => h("div", { style: "color:red" }), /style/);
  assert.throws(() => h("a", { href: "javascript:alert(1)" }), /href/);
});

test("on* attributes reject string values (no inline handler strings)", () => {
  assert.throws(() => h("button", { onclick: "doStuff()" }), /onclick/);
  assert.throws(() => h("form", { onsubmit: "return false" }), /onsubmit/);
});

test("url-bearing attributes allow only in-app hash routes and safe relative/same-origin paths", () => {
  for (const good of [
    ["a", "href", "#/caseload"],
    ["a", "href", "./patient.html"],
    ["img", "src", "assets/icons/live.svg"],
    ["img", "src", "./assets/icons/live.svg"],
    ["script", "src", "vendor/three/three.module.min.js"],
    ["a", "href", "/dashboard/#/caseload"],
  ]) {
    const [tag, attr, value] = good;
    assert.doesNotThrow(() => h(tag, { [attr]: value }), `${attr}=${value} should be allowed`);
  }
  for (const bad of [
    ["a", "href", "https://evil.example/dashboard/x"],
    ["a", "href", "https://example.com/#/caseload"],
    ["a", "href", "//evil.example/x"],
    ["a", "href", "javascript:alert(1)"],
    ["a", "href", "/dashboard/../etc/passwd"],
    ["a", "href", "./../../etc/passwd"],
    ["a", "href", "assets/../../../etc/passwd"],
    ["img", "src", "https://evil.example/x.png"],
  ]) {
    const [tag, attr, value] = bad;
    assert.throws(() => h(tag, { [attr]: value }), new RegExp(attr), `${attr}=${value} should throw`);
  }
});

test("url attribute names are checked case-insensitively", () => {
  assert.throws(() => h("a", { HREF: "https://evil.example/x" }), /HREF/);
  assert.throws(() => h("img", { SRC: "https://evil.example/x.png" }), /SRC/);
  assert.throws(() => h("a", { "xlink:href": "https://evil.example/x" }), /xlink:href/);
  assert.throws(() => h("form", { formaction: "https://evil.example/x" }), /formaction/);
  assert.throws(() => h("form", { action: "https://evil.example/x" }), /action/);
  assert.throws(() => h("img", { srcset: "https://evil.example/x.png 1x" }), /srcset/);
  assert.throws(() => h("video", { poster: "https://evil.example/x.png" }), /poster/);
  assert.doesNotThrow(() => h("img", { SRC: "assets/icons/live.svg" }));
});

test("clear removes children", () => {
  const ul = h("ul", null, h("li", null, "a"), h("li", null, "b"));
  clear(ul);
  assert.equal(ul.children.length, 0);
});
