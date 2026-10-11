import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const { mountSignIn } = await import("../js/screens/signin/index.js");

function storage() {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test("one submit probes once, shows 'Signing in…' while it runs, then hands over", async () => {
  const root = document.createElement("div");
  let release;
  const calls = [];
  const fetchImpl = (url) => { calls.push(url); return new Promise((r) => { release = r; }); };
  let signedIn = null;
  mountSignIn(root, { storage: storage(), fetchImpl, onSignedIn: (a) => { signedIn = a; } });
  const form = root.querySelector("form");
  root.querySelector("#signin-token").value = "t".repeat(24);
  form.dispatch("submit");
  const btn = root.querySelector("button");
  assert.equal(btn.textContent, "Signing in…");
  assert.equal(btn.hasAttribute("disabled"), true);
  form.dispatch("submit"); // a second submit while probing is ignored
  assert.equal(calls.length, 1);
  release({ ok: true, status: 200, json: async () => ({ patients: [] }) });
  for (let i = 0; i < 6; i++) await Promise.resolve();
  assert.equal(btn.textContent, "Sign in");
  assert.deepEqual(signedIn, { token: "t".repeat(24), therapistId: "okafor" });
});

test("an empty token or a rejected token shows an error instead of doing nothing", async () => {
  const root = document.createElement("div");
  const fetchImpl = async () => ({ ok: false, status: 401, statusText: "x", json: async () => ({ detail: "no" }) });
  mountSignIn(root, { storage: storage(), fetchImpl, onSignedIn: () => assert.fail("must not sign in") });
  const form = root.querySelector("form");
  form.dispatch("submit");
  assert.match(root.querySelector(".signin__error").textContent, /Enter your API token/);
  root.querySelector("#signin-token").value = "bad-token-bad-token-1234";
  form.dispatch("submit");
  for (let i = 0; i < 6; i++) await Promise.resolve();
  assert.match(root.querySelector(".signin__error").textContent, /not accepted/);
});
