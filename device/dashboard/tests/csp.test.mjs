import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

test("CSP allows exactly the inline importmap and nothing else inline", () => {
  const map = html.match(/<script type="importmap">([\s\S]*?)<\/script>/)[1];
  const hash = createHash("sha256").update(map).digest("base64");
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  assert.match(csp, new RegExp(`script-src 'self' 'sha256-${hash.replace(/[+/=]/g, "\\$&")}'`));
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|\*/);
  assert.match(csp, /connect-src 'self'/);
  const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*type="importmap")[^>]*>/g)];
  assert.equal(inline.length, 0, "no inline scripts besides the importmap");
  assert.doesNotMatch(html, /\sstyle="/, "no inline styles (style-src 'self')");
});
