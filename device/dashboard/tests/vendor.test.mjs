// Recomputes the sha256 of every vendored three.js file against vendor/three/VERSION so an accidental edit of
// the 1.5k-line OrbitControls (or three.module.min.js) is caught instead of silently drifting from the audited
// npm package (M22).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../../../", import.meta.url));
const versionPath = fileURLToPath(new URL("../vendor/three/VERSION", import.meta.url));
const lines = readFileSync(versionPath, "utf8").split("\n").map((l) => l.trim()).filter(Boolean);

// First line is "three@<version>"; every remaining line is "<sha256 hex>  <path relative to repo root>".
const entries = lines.slice(1).map((line) => {
  const m = line.match(/^([0-9a-f]{64})\s+(.+)$/);
  assert.ok(m, `VERSION line is not "<sha256>  <path>": ${line}`);
  return { hash: m[1], path: m[2] };
});

test("vendor/three/VERSION lists at least the two vendored files", () => {
  assert.ok(entries.length >= 2);
});

test("every vendored three.js file's sha256 matches VERSION", () => {
  for (const { hash, path } of entries) {
    const bytes = readFileSync(`${repoRoot}${path}`);
    const actual = createHash("sha256").update(bytes).digest("hex");
    assert.equal(actual, hash, `${path} does not match the hash recorded in VERSION`);
  }
});
