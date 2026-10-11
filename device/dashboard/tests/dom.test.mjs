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

test("clear removes children", () => {
  const ul = h("ul", null, h("li", null, "a"), h("li", null, "b"));
  clear(ul);
  assert.equal(ul.children.length, 0);
});
