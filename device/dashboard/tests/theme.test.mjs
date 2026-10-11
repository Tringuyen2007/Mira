import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { applyStoredTheme, getTheme, setTheme, toggleTheme } = await import("../js/ui/theme.js");

const STORAGE_KEY = "mira.dashboard.theme";

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem(k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
    setItem(k, v) { data[k] = String(v); },
    removeItem(k) { delete data[k]; },
    _data: data,
  };
}

test("applyStoredTheme defaults to light with no stored value", () => {
  globalThis.localStorage = fakeStorage();
  const theme = applyStoredTheme();
  assert.equal(theme, "light");
  assert.equal(getTheme(), "light");
  assert.equal(document.documentElement.getAttribute("data-theme"), "light");
});

test("applyStoredTheme reads a stored projector theme back", () => {
  globalThis.localStorage = fakeStorage({ [STORAGE_KEY]: "projector" });
  const theme = applyStoredTheme();
  assert.equal(theme, "projector");
  assert.equal(document.documentElement.getAttribute("data-theme"), "projector");
});

test("an unrecognised stored value falls back to light", () => {
  globalThis.localStorage = fakeStorage({ [STORAGE_KEY]: "neon" });
  assert.equal(applyStoredTheme(), "light");
});

test("setTheme applies the attribute and persists the choice", () => {
  globalThis.localStorage = fakeStorage();
  setTheme("projector");
  assert.equal(document.documentElement.getAttribute("data-theme"), "projector");
  assert.equal(globalThis.localStorage.getItem(STORAGE_KEY), "projector");
});

test("toggleTheme flips light <-> projector and persists each flip", () => {
  globalThis.localStorage = fakeStorage();
  setTheme("light");
  assert.equal(toggleTheme(), "projector");
  assert.equal(globalThis.localStorage.getItem(STORAGE_KEY), "projector");
  assert.equal(toggleTheme(), "light");
  assert.equal(globalThis.localStorage.getItem(STORAGE_KEY), "light");
});

test("a localStorage read failure is tolerated: the attribute is still applied, defaulting to light", () => {
  globalThis.localStorage = {
    getItem() { throw new Error("blocked (private window / disabled storage)"); },
    setItem() { throw new Error("blocked"); },
  };
  assert.doesNotThrow(() => applyStoredTheme());
  assert.equal(document.documentElement.getAttribute("data-theme"), "light");
});

test("a localStorage write failure is tolerated: the theme still applies for this tab, just isn't remembered", () => {
  globalThis.localStorage = {
    getItem() { return null; },
    setItem() { throw new Error("quota exceeded"); },
  };
  assert.doesNotThrow(() => setTheme("projector"));
  assert.equal(document.documentElement.getAttribute("data-theme"), "projector");
  assert.equal(getTheme(), "projector");
});

test("works with no localStorage at all (e.g. sandboxed iframe)", () => {
  const prev = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Reflect.deleteProperty(globalThis, "localStorage");
  try {
    assert.doesNotThrow(() => applyStoredTheme());
    assert.equal(getTheme(), "light");
    assert.doesNotThrow(() => setTheme("projector"));
    assert.equal(getTheme(), "projector");
  } finally {
    if (prev) Object.defineProperty(globalThis, "localStorage", prev);
  }
});
