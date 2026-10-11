import { test } from "node:test";
import assert from "node:assert/strict";
import { readToken, wsUrl } from "../js/config.js";
import { isSafeAudioUrl } from "../js/audio.js";

function fakes(hash) {
  const store = new Map();
  const calls = [];
  return {
    location: { hash, pathname: "/device/", search: "", protocol: "http:", host: "mira.local:8000" },
    storage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    history: { replaceState: (...a) => calls.push(a) },
    store, calls,
  };
}

test("token from fragment is stored and the fragment is stripped", () => {
  const f = fakes("#token=abc%2Bdef");
  assert.equal(readToken(f.location, f.storage, f.history), "abc+def");
  assert.equal(f.store.get("mira.deviceToken"), "abc+def");
  assert.deepEqual(f.calls[0].slice(2), ["/device/"]);
});

test("token from storage when no fragment", () => {
  const f = fakes("");
  f.store.set("mira.deviceToken", "saved");
  assert.equal(readToken(f.location, f.storage, f.history), "saved");
  assert.equal(f.calls.length, 0);
});

test("no token anywhere", () => {
  const f = fakes("");
  assert.equal(readToken(f.location, f.storage, f.history), null);
});

test("empty #token= is not stored or used, but the fragment is still stripped", () => {
  const f = fakes("#token=");
  f.store.set("mira.deviceToken", "saved");
  assert.equal(readToken(f.location, f.storage, f.history), "saved");
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0].slice(2), ["/device/"]);
});

test("ws url follows the page scheme and encodes the token", () => {
  assert.equal(wsUrl({ protocol: "http:", host: "h:8000" }, "/ws/live", "a+b"), "ws://h:8000/ws/live?token=a%2Bb");
  assert.equal(wsUrl({ protocol: "https:", host: "h" }, "/ws/events", "t"), "wss://h/ws/events?token=t");
});

test("audio url allow-list", () => {
  assert.equal(isSafeAudioUrl("/audio/check_in.mp3"), true);
  for (const bad of [null, "", "http://evil/a.mp3", "/audio/../x.mp3", "/audio/a.wav", "//evil/a.mp3",
    "/audio/a b.mp3", "javascript:alert(1)"]) {
    assert.equal(isSafeAudioUrl(bad), false, String(bad));
  }
});
