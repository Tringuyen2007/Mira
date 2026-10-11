import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../js", import.meta.url));
const files = (dir) => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : p.endsWith(".js") ? [p] : [];
});
const BANNED = [/\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\beval\(/,
  /new Function\(/, /localStorage\.setItem\([^)]*auth/i, /console\.(log|info|debug)\([^)]*token/i];

test("no HTML-injection or eval APIs, no token in localStorage or logs", () => {
  for (const f of files(root)) {
    const src = readFileSync(f, "utf8");
    for (const re of BANNED) assert.doesNotMatch(src, re, `${f} matches ${re}`);
  }
});
