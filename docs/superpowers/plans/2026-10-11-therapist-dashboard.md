# Therapist Web Dashboard (hi-fi) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Review cadence (user instruction):** no per-task code review. Run the code review + debug pass only at the end
> of each milestone (M1…M6), at the "Milestone gate" task.

**Goal:** Build the therapist dashboard at `device/dashboard/` (served at `/dashboard/`) from the Figma Web
Dashboard wireframe, in the Mira hi-fi visual language, wired to the existing FastAPI REST + WebSocket API.

**Architecture:** Static HTML + CSS + vanilla ES modules (no build, no npm), the same pattern as `device/kiosk`.
Pure `model.js` per screen (server JSON → view model, unit-tested against `server/samples`), `view.js` builds DOM
with a `h()` helper (no `innerHTML`). A handful of small server routes in new files fill the gaps the wireframe
needs (caseload triage, therapist memory, progress-note approval, HR limit).

**Tech Stack:** Vanilla JS (ES2022 modules), CSS custom properties, Three.js 0.169.0 (vendored), node:test;
Python 3 / FastAPI / psycopg3 / pytest on the server.

**Spec:** `docs/superpowers/specs/2026-10-11-therapist-dashboard-design.md`.

## Global Constraints

- Work only in the worktree `/Users/tringuyen2007/Downloads/CodeRed/Mira-mvp/wt/therapist-page` on branch
  `therapist-page`. Never `cd` into `Mira-mvp/Mira` (another session is working on `wire-tri` there). Never use bare
  `git stash`.
- No npm, no bundler, no CDN, no runtime network access beyond the page's own origin.
- Never use `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function` in
  `device/dashboard/js/`. All server text goes through `textContent` (the `h()` helper).
- The API token lives only in `sessionStorage` key `mira.dashboard.auth`; never in `localStorage`, never in a URL
  except the WebSocket `?token=` query, never logged.
- Server changes: new code in new files (`server/api/dashboard.py`, `server/db/dashboard_queries.py`,
  `server/db/dashboard_writes.py`, `server/dashboard_triage.py`). `server/main.py` changes are limited to the static
  mount filter and one `include_router` entry.
- Python style: `from __future__ import annotations`, full type hints, module docstring. JS style: ES modules, named
  exports, 2-space indent, double quotes, semicolons, comments only where the code isn't obvious (match
  `device/kiosk/js`).
- Copy: sentence case, plain language, the same strings as the Figma frames. AI output always carries "AI draft"
  until approved.
- Layout designed at 1440×1024; must work from 1280 px wide; projector theme at 1920×1080.
- Tests: `node --test device/dashboard/tests/` and `make test` must pass at every milestone gate.
- Commit after every task with a message in the repo style (imperative, sentence case, no prefix), ending with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## File map

```
device/dashboard/
  index.html  README.md
  css/tokens.css base.css shell.css screens/{signin,caseload,patient,prescribe,copilot,live,timelapse}.css
  js/main.js
  js/core/{dom,router,store,format,links}.js
  js/net/{auth,api,endpoints,socket,sockets,capabilities}.js
  js/ui/{sidebar,toasts,icons,statusIcon,aiDraftChip,proposalCard,balanceBar,lineChart,stepper,slider,
         segmented,states,alertBanner,notificationsBell}.js
  js/three/{joints,skeleton,room}.js
  js/screens/signin/{index}.js
  js/screens/caseload/{model,view,index}.js
  js/screens/patient/{model,view,index,player}.js
  js/screens/prescribe/{model,view,index}.js
  js/screens/copilot/{model,view,index}.js
  js/screens/live/{model,view,index}.js
  js/screens/timelapse/{model,view,index}.js
  vendor/three/{three.module.min.js,addons/controls/OrbitControls.js,LICENSE,VERSION}
  assets/fonts/{Inter-Variable.woff2,OFL.txt}  assets/icons/*.svg  assets/mira-mark.svg
  tests/*.test.mjs
server/
  dashboard_triage.py              pure triage rules
  api/dashboard.py                 /caseload, /patients/{id}/memory, /sessions/{id}/progress-note, /patients/{id}/limits
  db/dashboard_queries.py          SQL for the above
  db/dashboard_writes.py           hr_limit update
  samples/rest_get_caseload.json rest_get_patient_memory.json rest_post_progress_note.json rest_patch_patient_limits.json
  tests/test_dashboard_triage.py test_dashboard_api.py
  main.py                          (2 small edits)
Makefile                           add `test-dashboard` and include it in `test`
```

---

# M1 — Foundation

### Task 1: Serve the dashboard without its tests/dev/README

**Files:**
- Modify: `server/main.py` (`KioskStaticFiles` docstring and `_mount_static`)
- Test: `server/tests/test_security.py` (append one test)

**Interfaces:**
- Produces: `/dashboard/tests/*`, `/dashboard/dev/*`, `/dashboard/**/README.md` → 404.

- [ ] **Step 1: Write the failing test** (append to `server/tests/test_security.py`)

```python
def test_dashboard_served_without_its_tests_dev_or_readme(make_client: Callable[..., TestClient],
                                                          monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from server import main

    dash = tmp_path / "device" / "dashboard"
    for rel, body in {"index.html": "<h1>dashboard</h1>", "dev/fixtures.html": "<h1>dev</h1>",
                      "tests/router.test.mjs": "// test", "README.md": "# notes"}.items():
        (dash / rel).parent.mkdir(parents=True, exist_ok=True)
        (dash / rel).write_text(body)
    monkeypatch.setattr(main, "STATIC_PAGES", {"dashboard": dash, "device": tmp_path / "device" / "kiosk"})
    client = make_client()
    resp = client.get("/dashboard/")
    assert resp.status_code == 200 and "dashboard" in resp.text
    assert client.get("/dashboard/dev/fixtures.html").status_code == 404
    assert client.get("/dashboard/tests/router.test.mjs").status_code == 404
    assert client.get("/dashboard/README.md").status_code == 404
```

- [ ] **Step 2: Run it** — `.venv/bin/pytest server/tests/test_security.py -k dashboard_served_without -v`
  Expected: FAIL (`/dashboard/tests/router.test.mjs` returns 200).

- [ ] **Step 3: Implement** — in `server/main.py`, rename nothing (keeps the `wire-tri` merge trivial); change the
  docstring's first sentence to "Static pages minus what only developers need: dev/, tests/ and READMEs are never
  served." and in `_mount_static`:

```python
            static_cls = KioskStaticFiles if name in ("device", "dashboard") else StaticFiles
```

- [ ] **Step 4: Run** `.venv/bin/pytest server/tests/test_security.py -v` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Serve /dashboard/ without its tests, dev pages or READMEs"`

### Task 2: Scaffold, tokens, fonts, Three.js vendoring, CSP

**Files:**
- Create: `device/dashboard/index.html`, `css/tokens.css`, `css/base.css`, `css/shell.css`,
  `assets/fonts/Inter-Variable.woff2` + `OFL.txt` (copy from `device/kiosk/assets/fonts/`),
  `vendor/three/{three.module.min.js,addons/controls/OrbitControls.js,LICENSE,VERSION}`,
  `tests/csp.test.mjs`, `tests/banned.test.mjs`, `tests/socket-drift.test.mjs`
- Modify: `Makefile` (add `test-dashboard`, and make `test` depend on it)

**Interfaces:**
- Produces: the shell DOM ids used by later tasks: `#app` (root), `#sidebar`, `#main`, `#toasts`
  (`aria-live="polite"`), `#signin`. CSS classes from `base.css`: `.card`, `.card__title`, `.btn`, `.btn--primary`,
  `.btn--secondary`, `.btn--ghost`, `.chip`, `.chip--ai`, `.pill-tabs`, `.meta`, `.page-head`, `.page-title`,
  `.page-sub`, `.grid-main-rail` (main column + 280 px rail), `.status--needs`, `.status--watch`, `.status--ok`.

- [ ] **Step 1: Vendor Three.js 0.169.0 and verify integrity**

```bash
cd device/dashboard && mkdir -p vendor/three/addons/controls && cd /tmp && \
curl -sSLo three-0.169.0.tgz https://registry.npmjs.org/three/-/three-0.169.0.tgz && \
EXPECTED=$(curl -sSL https://registry.npmjs.org/three/0.169.0 | python3 -c "import sys,json;print(json.load(sys.stdin)['dist']['integrity'])") && \
ACTUAL="sha512-$(openssl dgst -sha512 -binary three-0.169.0.tgz | openssl base64 -A)" && \
[ "$EXPECTED" = "$ACTUAL" ] && echo INTEGRITY_OK && tar xzf three-0.169.0.tgz
```
Expected: `INTEGRITY_OK`. Then copy `package/build/three.module.min.js` → `vendor/three/`,
`package/examples/jsm/controls/OrbitControls.js` → `vendor/three/addons/controls/`, `package/LICENSE` →
`vendor/three/LICENSE`, and write `vendor/three/VERSION` containing `three@0.169.0` and the two files' `shasum -a 256`
lines. (OrbitControls imports from `'three'`, resolved by the importmap.)

- [ ] **Step 2: `index.html`**

```html
<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self' 'sha256-IMPORTMAP_HASH'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>Mira · Care team</title>
<link rel="preload" href="assets/fonts/Inter-Variable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="css/tokens.css">
<link rel="stylesheet" href="css/base.css">
<link rel="stylesheet" href="css/shell.css">
<script type="importmap">{"imports":{"three":"./vendor/three/three.module.min.js","three/addons/":"./vendor/three/addons/"}}</script>
<script type="module" src="js/main.js"></script>
</head>
<body>
<div id="app" data-state="booting">
  <section id="signin" hidden></section>
  <aside id="sidebar" hidden></aside>
  <main id="main" tabindex="-1" hidden></main>
</div>
<div id="toasts" role="status" aria-live="polite"></div>
</body>
</html>
```
Replace `IMPORTMAP_HASH` with the base64 sha256 of the exact importmap text between the tags:
`node -e "const c=require('crypto');const s=require('fs').readFileSync('device/dashboard/index.html','utf8').match(/<script type=\"importmap\">([\s\S]*?)<\/script>/)[1];console.log(c.createHash('sha256').update(s).digest('base64'))"`.
Per-screen stylesheets are linked here too as they are added (`css/screens/*.css`).

- [ ] **Step 3: Tests that guard the security posture**

`tests/csp.test.mjs`:
```js
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
```

`tests/banned.test.mjs`:
```js
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
```

`tests/socket-drift.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("dashboard socket.js stays identical to the kiosk's ReconnectingSocket", () => {
  const kiosk = readFileSync(new URL("../../kiosk/js/socket.js", import.meta.url), "utf8");
  const dash = readFileSync(new URL("../js/net/socket.js", import.meta.url), "utf8");
  assert.equal(dash, kiosk, "fix both copies together (device/kiosk/js/socket.js is the source)");
});
```
Copy `device/kiosk/js/socket.js` to `device/dashboard/js/net/socket.js` byte for byte.

- [ ] **Step 4: `css/tokens.css`** — copy `device/kiosk/css/tokens.css` (fix the `@font-face` url to
  `../assets/fonts/Inter-Variable.woff2`), then append:

```css
:root {
  --color-watch: #b54708;
  --color-line: #e7e3ea;
  --color-line-strong: #1c1a22;
  --color-good: var(--color-accent-bright);
  --color-flag: var(--color-alert);
  --color-card: #ffffff;
  --color-card-sunk: #f6f4f7;
  --color-sidebar: rgba(255, 255, 255, 0.72);
  --radius-card: 20px;
  --radius-control: 12px;
  --shadow-card: 0 1px 2px rgba(28, 26, 34, 0.04), 0 8px 24px rgba(143, 186, 204, 0.12);
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 20px; --space-6: 24px;
  --space-8: 32px;
  --text-meta: 14px; --text-body: 16px; --text-card-title: 20px; --text-page-title: 32px;
  --canvas: radial-gradient(1200px 800px at 85% -10%, var(--color-mist), transparent 60%),
            radial-gradient(900px 700px at -10% 110%, var(--color-sky), transparent 60%), var(--color-canvas);
}
:root[data-theme="projector"] {
  --color-ink: #f4f1f6; --color-ink-soft: #c9c3cf; --color-card: #211d27; --color-card-sunk: #2b2632;
  --color-line: #3a3442; --color-line-strong: #f4f1f6; --color-sidebar: rgba(24, 21, 29, 0.9);
  --canvas: #15121a; --text-meta: 16px; --text-body: 18px; --text-card-title: 22px; --text-page-title: 36px;
}
```

- [ ] **Step 5: `css/base.css` and `css/shell.css`** — implement the classes listed under "Produces", matching the
  Figma components frame `203:109` (nav item 220×44 pill, active = ink fill + white text; Approve button 44 px pill;
  "AI draft" chip dashed 1.5 px berry border, 12 px text, ✦ prefix) translated to hi-fi: primary = accent fill,
  focus ring `outline: 3px solid var(--color-ring-cool); outline-offset: 2px` on `:focus-visible`, body background
  `var(--canvas)`, font `var(--font)`, `@media (prefers-reduced-motion: reduce) { * { animation: none !important;
  transition: none !important; } }`. Shell grid: `#app { display: grid; grid-template-columns: 232px 1fr; }`,
  sidebar sticky full-height, main padding `32px 32px 24px`. Below 1280 px, `.grid-main-rail` becomes one column.

- [ ] **Step 6: Makefile** — add:

```make
test-dashboard:
	node --test device/dashboard/tests/
```
and make the existing `test` target run `test-dashboard` after pytest (keep the existing recipe). Run
`make test-dashboard` — Expected: 3 tests PASS.

- [ ] **Step 7: Commit** `git add -A device/dashboard Makefile && git commit -m "Scaffold the therapist dashboard: tokens, CSP, vendored Three.js, guard tests"`

### Task 3: Core modules — dom, router, links, format, store

**Files:**
- Create: `device/dashboard/js/core/{dom,router,links,format,store}.js`
- Test: `device/dashboard/tests/{dom,router,links,format}.test.mjs`

**Interfaces:**
- Produces:
  - `h(tag: string, attrs?: object|null, ...children: (Node|string|number|null|false|array)[]) → HTMLElement`
    — `attrs` keys: `class`, `id`, `role`, `aria-*`, `data-*`, `hidden`, `disabled`, `type`, `href`, `value`,
    `for`, `title`, `tabindex`, `on<Event>` (function → `addEventListener`), `style` is **not** allowed (throws).
    Strings become text nodes. `svg(tag, attrs, ...children)` same for SVG namespace. `clear(el)` removes children.
  - `parseRoute(hash: string) → {name, params, query}` with names: `caseload`, `patient`, `replay`, `prescribe`,
    `timelapse`, `copilot`, `proposal`, `live`, `notfound`. `href(name, params?, query?) → "#/…"`.
  - `safeInternalLink(link: string|null) → string|null` returns a `#/…` hash for `/dashboard#/…` or `#/…`, else null.
  - `format.js`: `fmtDateLong(iso, now)` → "Tuesday, October 11"; `fmtWhen(iso, now)` → "today, 9:10" |
    "yesterday" | "Oct 7"; `fmtDeg(n)` → "92°"; `fmtPct(n)` → "36%"; `fmtSide(side)` → "Right" | "Left";
    `firstName(name)`; `initials(name)` → "SL"; `weeksSince(iso, now)` → 3.
  - `createStore(initial) → {get(), set(patch), subscribe(fn) → unsubscribe}`.

- [ ] **Step 1: Write the failing tests** — `tests/router.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, href } from "../js/core/router.js";

test("parses every dashboard route", () => {
  assert.deepEqual(parseRoute(""), { name: "caseload", params: {}, query: {} });
  assert.deepEqual(parseRoute("#/caseload?filter=needs"), { name: "caseload", params: {}, query: { filter: "needs" } });
  assert.deepEqual(parseRoute("#/patients/maya"), { name: "patient", params: { pid: "maya" }, query: {} });
  assert.deepEqual(parseRoute("#/patients/maya/prescribe").name, "prescribe");
  assert.deepEqual(parseRoute("#/patients/maya/timelapse").params, { pid: "maya" });
  assert.deepEqual(parseRoute("#/replay/0590e71e-b7a4-4add-ab11-c14a27b7a1d6").params,
    { sid: "0590e71e-b7a4-4add-ab11-c14a27b7a1d6" });
  assert.deepEqual(parseRoute("#/proposals/12"), { name: "proposal", params: { id: "12" }, query: {} });
  assert.deepEqual(parseRoute("#/live/george").params, { pid: "george" });
  assert.equal(parseRoute("#/copilot").name, "copilot");
  assert.equal(parseRoute("#/nope").name, "notfound");
});

test("rejects ids that are not plain ids", () => {
  assert.equal(parseRoute("#/patients/..%2Fx").name, "notfound");
  assert.equal(parseRoute("#/patients/a b").name, "notfound");
  assert.equal(parseRoute("#/proposals/abc").name, "notfound");
});

test("href round-trips and encodes", () => {
  assert.equal(href("patient", { pid: "maya" }), "#/patients/maya");
  assert.equal(href("caseload", {}, { filter: "watch" }), "#/caseload?filter=watch");
  for (const h of [href("prescribe", { pid: "p02" }), href("replay", { sid: "abc-1" })]) {
    assert.notEqual(parseRoute(h).name, "notfound");
  }
});
```
Ids: patient ids match `/^[A-Za-z0-9_-]{1,64}$/`, session ids `/^[0-9a-fA-F-]{8,64}$/`, proposal ids `/^\d{1,12}$/`.

`tests/links.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { safeInternalLink } from "../js/core/links.js";

test("only dashboard-internal links survive", () => {
  assert.equal(safeInternalLink("/dashboard#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b"),
    "#/replay/fea67dbd-f43f-4803-85d5-5e205100a69b");
  assert.equal(safeInternalLink("#/proposals/3"), "#/proposals/3");
  for (const bad of [null, "", "https://evil.example/#/x", "javascript:alert(1)", "//evil/#/x",
    "/dashboard#/nope", "/device#/x", "/dashboard#/patients/../x"]) {
    assert.equal(safeInternalLink(bad), null, String(bad));
  }
});
```

`tests/dom.test.mjs` (uses a tiny fake document so no browser is needed):
```js
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
```
`tests/fakedom.mjs`: a ~60-line minimal `document` with `createElement`, `createElementNS`, `createTextNode`,
elements supporting `setAttribute/getAttribute/removeAttribute`, `className`, `append`, `replaceChildren`,
`children`, `childNodes`, `textContent` (getter concatenates descendants' text), `addEventListener` and a test-only
`dispatch(type)`. Installed on `globalThis.document`. Later view tests reuse it.

`tests/format.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { fmtDateLong, fmtWhen, fmtDeg, fmtPct, initials, weeksSince, firstName } from "../js/core/format.js";

const now = new Date("2026-10-11T15:00:00");
test("formats", () => {
  assert.equal(fmtDateLong(now), "Sunday, October 11");
  assert.equal(fmtWhen("2026-10-11T09:10:00", now), "today, 9:10");
  assert.equal(fmtWhen("2026-10-10T18:00:00", now), "yesterday");
  assert.equal(fmtWhen("2026-10-07T10:00:00", now), "Oct 7");
  assert.equal(fmtDeg(91.6), "92°");
  assert.equal(fmtPct(35.99), "36%");
  assert.equal(fmtDeg(null), "—");
  assert.equal(initials("Dr. Sam Lee, PT"), "SL");
  assert.equal(firstName("Maya Ellis"), "Maya");
  assert.equal(weeksSince("2026-09-20T00:00:00", now), 3);
});
```

- [ ] **Step 2: Run** `node --test device/dashboard/tests/` — Expected: FAIL (modules missing).
- [ ] **Step 3: Implement the five modules.** `dom.js` core:

```js
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
```
`router.js` uses an ordered table of `[name, RegExp, keys]`, decodes nothing (ids are validated raw), parses the
query with `URLSearchParams`. `links.js`: strip a leading `/dashboard` (exact) then require `#/`, then
`parseRoute(...)` ≠ `notfound`. `store.js`: shallow-merge `set`, notify subscribers synchronously.
`format.js`: use `Intl.DateTimeFormat("en-US", …)`; "today, 9:10" uses `hour: "numeric", minute: "2-digit"` without
AM/PM (Figma copy).

- [ ] **Step 4: Run** `node --test device/dashboard/tests/` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Dashboard core: safe DOM builder, hash router, link filter, formatters, store"`

### Task 4: Network layer — auth, api, endpoints, sockets, capabilities

**Files:**
- Create: `device/dashboard/js/net/{auth,api,endpoints,sockets,capabilities}.js`
- Test: `device/dashboard/tests/{auth,api,endpoints,sockets,capabilities}.test.mjs`

**Interfaces:**
- Consumes: `ReconnectingSocket` from `js/net/socket.js`.
- Produces:
  - `auth.js`: `AUTH_KEY = "mira.dashboard.auth"`; `loadAuth(storage) → {token, therapistId}|null`;
    `saveAuth(storage, {token, therapistId})`; `clearAuth(storage)`. Token must be ≥ 24 chars (server rule);
    therapistId `/^[A-Za-z0-9_-]{1,64}$/`. Throws `Error("token too short")` / `Error("bad therapist id")`.
  - `api.js`: `class ApiError extends Error {status, detail}`; `createApi({token, fetchImpl=fetch, base="",
    timeoutMs=10000, onUnauthorized}) → {get(path, query?), post(path, body?), put(path, body), patch(path, body)}`.
    Query values that are `null/undefined` are dropped. Bodies are JSON. 401 calls `onUnauthorized()` then throws.
    Non-JSON error bodies → `detail = statusText`. Timeout → `ApiError(0, "timeout")`. Network failure →
    `ApiError(0, "offline")`.
  - `endpoints.js`: `createEndpoints(api, therapistId)` returning:
    `caseload()`, `patients()`, `patient(pid)`, `progress(pid, {exerciseId, days})`, `memory(pid, days=21)`,
    `session(sid)`, `framesLttb(sid)`, `framesPage(sid, t0)`, `alerts()`, `resolveAlert(alertId, note)`,
    `proposals({status, patientId})`, `approveProposal(id)`, `editProposal(id, change)`, `dismissProposal(id, reason)`,
    `prescriptions(pid)`, `putPrescription(pid, body)`, `setLimits(pid, {hrLimit})`,
    `approveProgressNote(sid, text)`, `copilotAsk(question, patientId)`, `notifications({unread, limit, sinceId})`,
    `notificationCount()`, `markRead(id)`, `markAllRead()`, `ack(id)`, `stats()`. Every write injects
    `therapist_id`/`resolved_by`/`created_by` = `therapistId`. All ids are `encodeURIComponent`-ed.
  - `sockets.js`: `createSocketHub({location, token, SocketImpl=ReconnectingSocket}) → {subscribe(channel, fn) →
    unsubscribe, status(channel), onStatus(fn), send(channel, obj), closeAll()}`. Channels: `events`,
    `notifications`, `live`, `pose`. A channel's socket opens on first subscriber and stops when the last one leaves
    (except `events`/`notifications`, opened by `main.js` for the whole session). `notifications` URL carries
    `since_id` = highest id seen and `therapist_id`.
  - `capabilities.js`: `loadCapabilities(fetchImpl) → {has(pathTemplate) → bool}`; fetches `/openapi.json` (no
    token needed); on any failure `has()` is always false.

- [ ] **Step 1: Failing tests.** `tests/api.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createApi, ApiError } from "../js/net/api.js";

function fakeFetch(handler) {
  const calls = [];
  const f = async (url, init) => { calls.push({ url, init }); return handler(url, init); };
  f.calls = calls;
  return f;
}
const json = (status, body) => ({ ok: status < 300, status, statusText: "x", json: async () => body });

test("sends bearer + JSON and drops empty query values", async () => {
  const f = fakeFetch(() => json(200, { ok: true }));
  const api = createApi({ token: "t".repeat(24), fetchImpl: f });
  await api.get("/notifications", { unread: true, since_id: null });
  assert.equal(f.calls[0].url, "/notifications?unread=true");
  assert.equal(f.calls[0].init.headers.Authorization, `Bearer ${"t".repeat(24)}`);
  await api.post("/proposals/1/approve", { therapist_id: "okafor" });
  assert.equal(f.calls[1].init.method, "POST");
  assert.equal(f.calls[1].init.body, '{"therapist_id":"okafor"}');
  assert.equal(f.calls[1].init.headers["Content-Type"], "application/json");
});

test("401 signs out; other errors become ApiError with detail", async () => {
  let out = 0;
  const api = createApi({ token: "t".repeat(24), onUnauthorized: () => out++,
    fetchImpl: fakeFetch((u) => u.includes("401") ? json(401, { detail: "unauthorized" }) : json(409, { detail: "already decided" })) });
  await assert.rejects(api.get("/401"), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(out, 1);
  await assert.rejects(api.post("/409"), (e) => e.status === 409 && e.detail === "already decided");
});

test("network failure and timeout", async () => {
  const off = createApi({ token: "t".repeat(24), fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
  await assert.rejects(off.get("/x"), (e) => e.status === 0 && e.detail === "offline");
  const slow = createApi({ token: "t".repeat(24), timeoutMs: 5,
    fetchImpl: (u, init) => new Promise((_, rej) => init.signal.addEventListener("abort", () => rej(Object.assign(new Error("a"), { name: "AbortError" })))) });
  await assert.rejects(slow.get("/x"), (e) => e.status === 0 && e.detail === "timeout");
});
```

`tests/auth.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAuth, saveAuth, clearAuth, AUTH_KEY } from "../js/net/auth.js";

const mem = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k), m }; };

test("round trip, validation, clear", () => {
  const s = mem();
  assert.equal(loadAuth(s), null);
  assert.throws(() => saveAuth(s, { token: "short", therapistId: "okafor" }), /too short/);
  assert.throws(() => saveAuth(s, { token: "x".repeat(24), therapistId: "a b" }), /therapist/);
  saveAuth(s, { token: "x".repeat(24), therapistId: "okafor" });
  assert.deepEqual(loadAuth(s), { token: "x".repeat(24), therapistId: "okafor" });
  s.setItem(AUTH_KEY, "{not json");
  assert.equal(loadAuth(s), null);
  saveAuth(s, { token: "x".repeat(24), therapistId: "okafor" });
  clearAuth(s);
  assert.equal(s.m.size, 0);
});
```

`tests/endpoints.test.mjs` — a recording fake api asserting paths and bodies:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createEndpoints } from "../js/net/endpoints.js";

const rec = () => { const calls = []; const r = (m) => async (path, arg) => { calls.push([m, path, arg]); return {}; };
  return { calls, get: r("GET"), post: r("POST"), put: r("PUT"), patch: r("PATCH") }; };

test("paths, encoding and therapist id injection", async () => {
  const api = rec();
  const ep = createEndpoints(api, "okafor");
  await ep.patient("a/b");
  await ep.approveProposal(7);
  await ep.dismissProposal(7, "not now");
  await ep.resolveAlert("a1", "called her");
  await ep.putPrescription("maya", { exercise_id: "squat", sets: 3 });
  await ep.setLimits("maya", { hrLimit: 120 });
  await ep.approveProgressNote("s1", "Week 3 …");
  await ep.caseload();
  assert.deepEqual(api.calls, [
    ["GET", "/patients/a%2Fb", undefined],
    ["POST", "/proposals/7/approve", { therapist_id: "okafor" }],
    ["POST", "/proposals/7/dismiss", { therapist_id: "okafor", reason: "not now" }],
    ["POST", "/alerts/a1/resolve", { resolved_by: "okafor", note: "called her" }],
    ["PUT", "/prescriptions/maya", { exercise_id: "squat", sets: 3, created_by: "okafor" }],
    ["PATCH", "/patients/maya/limits", { therapist_id: "okafor", hr_limit: 120 }],
    ["POST", "/sessions/s1/progress-note", { therapist_id: "okafor", text: "Week 3 …" }],
    ["GET", "/caseload", { therapist_id: "okafor" }],
  ]);
});
```

`tests/sockets.test.mjs` — inject a fake `SocketImpl` class that records `url`, `start()`, `stop()` and exposes
`onMessage`; assert: (1) `subscribe("live", fn)` builds `ws://host/ws/live?token=…` and starts once for two
subscribers; (2) messages fan out to both; (3) the socket stops when both unsubscribe; (4) the notifications URL
includes `since_id` = max id seen after a reconnect (`hub.noteNotificationId(42)` then a new subscribe →
`since_id=42&therapist_id=okafor`); (5) `closeAll()` stops every socket.

`tests/capabilities.test.mjs`: `/openapi.json` with `{"paths": {"/pair": {}, "/patients/{patient_id}/messages": {}}}`
→ `has("/pair") === true`, `has("/live/say") === false`; fetch throwing → `has("/pair") === false`.

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement** (api.js core shown; the rest follows the interfaces):

```js
// REST client: bearer token, JSON, 10 s timeout. Errors are ApiError{status, detail}; status 0 = offline/timeout.
export class ApiError extends Error {
  constructor(status, detail) { super(`${status} ${detail}`); this.status = status; this.detail = detail; }
}

export function createApi({ token, fetchImpl = globalThis.fetch.bind(globalThis), base = "", timeoutMs = 10000,
                            onUnauthorized } = {}) {
  async function request(method, path, { query, body } = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query || {})) if (v !== null && v !== undefined) qs.set(k, String(v));
    const url = `${base}${path}${qs.size ? `?${qs}` : ""}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetchImpl(url, { method, headers, signal: ctrl.signal, cache: "no-store", credentials: "omit",
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new ApiError(0, e?.name === "AbortError" ? "timeout" : "offline");
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty or non-JSON body */ }
    if (res.status === 401) onUnauthorized?.();
    if (!res.ok) throw new ApiError(res.status, (data && typeof data.detail === "string") ? data.detail : res.statusText);
    return data;
  }
  return {
    get: (path, query) => request("GET", path, { query }),
    post: (path, body) => request("POST", path, { body: body ?? {} }),
    put: (path, body) => request("PUT", path, { body }),
    patch: (path, body) => request("PATCH", path, { body }),
  };
}
```
Note: in `endpoints.js`, GET helpers call `api.get(path, query)`; the endpoints test records the second arg as the
query object.

- [ ] **Step 4: Run** — Expected: PASS. **Step 5: Commit** `git commit -m "Dashboard network layer: auth, REST client, endpoint map, socket hub, capability probe"`

### Task 5: Shell — sign-in, sidebar, toasts, router outlet, boot

**Files:**
- Create: `device/dashboard/js/main.js`, `js/screens/signin/index.js`, `js/ui/{sidebar,toasts,icons,states}.js`,
  `css/screens/signin.css`, `assets/icons/*.svg` (caseload, patient, prescribe, timelapse, live, copilot, lock,
  search, chevron, alert-triangle, diamond, check-circle, sparkle, play, heart, calendar, message, bell),
  `assets/mira-mark.svg` (export the flower mascot from Figma iPhone Hi-Fi `1:3` via `get_screenshot`/asset
  download, or draw a simple 5-petal SVG in berry/mauve if export is not possible)
- Test: `device/dashboard/tests/sidebar.test.mjs`

**Interfaces:**
- Consumes: everything from Tasks 3–4.
- Produces: `ctx` object passed to every screen: `{ api: endpoints, sockets, auth: {therapistId, signOut},
  caps, navigate(hashString), toast({kind: "info"|"success"|"error", text, action?: {label, onClick}}), store }`.
  Screen module contract: `export function mount(root: HTMLElement, ctx, route) → (() => void)`.
  `store` keys used later: `proposalsWaiting` (number, sidebar badge), `selectedPatientId` (last viewed patient,
  so "Patient review/Prescribe/Time-lapse" nav items know whom to open), `therapist` ({id, name, clinic}).
  `ui/states.js`: `loading(text)`, `empty(title, body)`, `errorState(err: ApiError, retry)` — 503 shows "History
  needs the database"; 0/offline shows "Can't reach Mira's server"; others show the detail.
  `ui/icons.js`: `icon(name, {label?})` → `<img src="assets/icons/<name>.svg" alt=label||"" aria-hidden=!label>`.

- [ ] **Step 1: Failing test** — `tests/sidebar.test.mjs` (fakedom): `renderSidebar({route: {name: "prescribe"},
  proposalsWaiting: 3, selectedPatientId: "maya", therapist: {name: "Dr. Sam Lee, PT", clinic: "Lakeside Rehab"},
  live: null})` →
  - six nav links in order Caseload, Patient review, Prescribe, Time-lapse, Live sessions, Care Copilot;
  - "Prescribe" has `aria-current="page"`; Patient review links to `#/patients/maya`;
  - Care Copilot shows badge text "3" with `aria-label="3 proposals waiting"`;
  - with `selectedPatientId: null`, patient-scoped items link to `#/caseload` and carry `aria-disabled="true"`;
  - the privacy note text "No video. Mira shares skeleton data only." and footer initials "SL".
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.**
  - `signin/index.js`: a form (no `action`, `form-action 'none'`): "API token" (`type="password"`,
    `autocomplete="off"`, `spellcheck="false"`), "Therapist ID" (default `okafor`), "Sign in". On submit:
    `preventDefault`, `saveAuth(sessionStorage, …)`, then probe `GET /patients` with the new token; 401 → inline
    error "That token was not accepted", and `clearAuth`. Success → boot the shell. Explain under the form: "Your
    token stays in this browser tab and is cleared when you close it."
  - `main.js` boot: `loadAuth(sessionStorage)` → none: show sign-in. Else create api (with `onUnauthorized` =
    signOut), endpoints, `loadCapabilities`, socket hub; subscribe `events` + `notifications`; render sidebar;
    `window.addEventListener("hashchange", render)`; render: `unmount?.()`, `clear(main)`, look up the screen in a
    `SCREENS` map (`caseload`, `patient`, `replay`→patient, `prescribe`, `timelapse`, `copilot`, `proposal`→copilot,
    `live`, `notfound`), `unmount = mount(main, ctx, route)`, `main.focus()`. Screens not yet built show
    `empty("Coming soon", "")` (replaced in later milestones). `signOut()`: `clearAuth`, `sockets.closeAll()`,
    `location.hash = ""`, show sign-in.
  - Toasts: max 3 visible, auto-dismiss 6 s (errors stay until closed), close button, `role="status"` container.
  - Therapist name: derive from `GET /patients/{first}` → `therapist_name` when available, else the id.
- [ ] **Step 4: Run** `make test-dashboard` — PASS. Then `make run` (mock mode) and open
  `http://localhost:8000/dashboard/`: sign in with the `.env` token; sidebar renders like Figma `205:205`;
  hash routes switch; reload keeps you signed in; closing the tab signs you out; DevTools console shows no CSP
  violations.
- [ ] **Step 5: Commit** `git commit -m "Dashboard shell: sign-in, sidebar, toasts, router outlet"`

### Task 6: Milestone gate M1 — review and debug

- [ ] Dispatch the code-reviewer subagent (superpowers:requesting-code-review) over `git diff main...HEAD` with the
  spec's Security section and Global Constraints as the checklist. Fix every Critical/Important finding.
- [ ] Run `make test` and `make test-dashboard`; load the page in Chrome; check the console for CSP errors and the
  Network tab for any request not to the page's own origin.
- [ ] Commit fixes: `git commit -m "M1 review fixes"`.

---

# M2 — Caseload

### Task 7: Server — triage rules (pure)

**Files:**
- Create: `server/dashboard_triage.py`
- Test: `server/tests/test_dashboard_triage.py`

**Interfaces:**
- Produces:

```python
Bucket = Literal["needs_you", "watch", "on_track"]

@dataclass(frozen=True)
class Signals:
    open_alert_kind: str | None            # "fall" | "pain" | "hr" (latest open alert)
    last_pain_post: int | None             # last finished session
    prev_pain_post: int | None             # the one before
    last_effort: list[int]                 # newest first, up to 3
    flagged_reps_last: int                 # flagged reps in the last session
    flagged_reps_prev: int
    reps_last: int
    top_flag_last: str | None              # most common flag in the last session, e.g. "knee_cave_r"
    memory: list[dict[str, Any]]           # unacknowledged memory_events, newest first: {kind, message, evidence, time}
    missed_sessions: int                   # consecutive expected sessions missed (0 if none)
    last_session_at: datetime | None
    live_now: bool

@dataclass(frozen=True)
class Triage:
    bucket: Bucket
    severity: int                          # 3 needs_you, 2 watch, 1 on_track; higher sorts first
    reason: str
    evidence: str
    kind: str                              # rule id: alert|pain|compensation|asymmetry|missed|flags|effort|progress|steady

def triage(s: Signals) -> Triage: ...
```

- [ ] **Step 1: Failing tests** (`server/tests/test_dashboard_triage.py`):

```python
from __future__ import annotations

from datetime import UTC, datetime

from server.dashboard_triage import Signals, triage

BASE = dict(open_alert_kind=None, last_pain_post=3, prev_pain_post=3, last_effort=[5], flagged_reps_last=0,
            flagged_reps_prev=0, reps_last=12, top_flag_last=None, memory=[], missed_sessions=0,
            last_session_at=datetime(2026, 10, 11, 9, 10, tzinfo=UTC), live_now=False)


def sig(**over) -> Signals:
    return Signals(**{**BASE, **over})


def test_open_alert_wins() -> None:
    t = triage(sig(open_alert_kind="fall", last_pain_post=8))
    assert (t.bucket, t.kind) == ("needs_you", "alert")
    assert t.reason == "Fall detected, not resolved yet"


def test_pain_seven_or_more_needs_you_with_change_as_evidence() -> None:
    t = triage(sig(last_pain_post=7, prev_pain_post=3))
    assert (t.bucket, t.kind, t.evidence) == ("needs_you", "pain", "Pain 3 → 7")
    assert t.reason == "Reported pain 7 out of 10 after the last session"


def test_compensation_memory_needs_you() -> None:
    mem = [{"kind": "compensation", "message": "Bending deeper, but taking weight off her right leg",
            "evidence": {"load_from": 47.0, "load_to": 36.0, "side": "right"}, "time": "2026-10-11T09:00:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind) == ("needs_you", "compensation")
    assert t.reason == "Bending deeper, but taking weight off her right leg"
    assert t.evidence == "Right leg 47% → 36%"


def test_missed_three_needs_you() -> None:
    t = triage(sig(missed_sessions=3))
    assert (t.bucket, t.reason, t.evidence) == ("needs_you", "Missed 3 sessions in a row", "0 of 3 sessions done")


def test_flags_and_effort_watch() -> None:
    t = triage(sig(flagged_reps_last=4, flagged_reps_prev=1, top_flag_last="knee_cave_r"))
    assert (t.bucket, t.kind, t.evidence) == ("watch", "flags", "Flagged reps 1 → 4")
    assert t.reason == "Knee drifting inward on 4 of 12 reps"
    e = triage(sig(last_effort=[8, 8, 9]))
    assert (e.bucket, e.kind) == ("watch", "effort")
    assert e.reason == "Rated effort “hard” 3 sessions running"


def test_on_track_uses_progress_memory_else_steady() -> None:
    mem = [{"kind": "depth_gain", "message": "Reached the 110° knee bend goal", "evidence": {"gain_deg": 6},
            "time": "2026-10-11T07:45:00Z"}]
    t = triage(sig(memory=mem))
    assert (t.bucket, t.kind, t.evidence) == ("on_track", "progress", "Depth +6° this week")
    assert triage(sig()).kind == "steady"


def test_severity_orders_buckets() -> None:
    assert triage(sig(open_alert_kind="hr")).severity > triage(sig(flagged_reps_last=4)).severity > triage(sig()).severity
```

- [ ] **Step 2: Run** `.venv/bin/pytest server/tests/test_dashboard_triage.py -v` — FAIL.
- [ ] **Step 3: Implement** `server/dashboard_triage.py` with the rule order from the spec ("Triage rules"). Flag
  names → phrases: `knee_cave_l|knee_cave_r|knees_out` → "Knee drifting inward", `trunk_lean` → "Leaning forward",
  `weight_shift` → "Shifting weight off the affected leg", `depth_more` → "Not reaching depth", anything else →
  "Form flagged". Alert kinds: `fall` → "Fall detected, not resolved yet", `pain` → "Pain alert, not resolved yet",
  `hr` → "Heart rate alert, not resolved yet". Memory `asymmetry_worse` → reason = message, evidence =
  "Asymmetry {from}% → {to}%" when those keys exist, else "". Effort rule: the newest 3 efforts all ≥ 8. Pain < 7
  `pain` memory → watch with its message.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `git commit -m "Caseload triage rules for the therapist dashboard"`

### Task 8: Server — `GET /caseload` and `GET /patients/{id}/memory`

**Files:**
- Create: `server/db/dashboard_queries.py`, `server/api/dashboard.py`,
  `server/samples/rest_get_caseload.json`, `server/samples/rest_get_patient_memory.json`
- Modify: `server/main.py` (add `dashboard` to the `(copilot_api, proposals)` tuple of token-protected routers, and
  its import)
- Test: `server/tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `triage`, `Signals` (Task 7); `Database.fetch`; `require_db`; `get_state().live` for `live_now`.
- Produces (response shapes; sample files hold one realistic example each):

```json
{ "therapist_id": "okafor", "generated_at": "2026-10-11T15:00:00Z",
  "today": { "sessions_done": 4, "live_now": { "patient_id": "george", "name": "George Alvarez",
             "exercise_name": "Squats", "set": 2, "sets_target": 3, "rep": 7 } },
  "counts": { "all": 9, "needs_you": 3, "watch": 2, "on_track": 4 },
  "patients": [ { "id": "maya", "name": "Maya Ruiz", "age": 67, "condition": "right knee replacement",
                  "affected_side": "right", "week": 3, "simulated": true, "bucket": "needs_you", "severity": 3,
                  "kind": "compensation", "reason": "Bending deeper, but taking weight off her right leg",
                  "evidence": "Right leg 47% → 36%", "last_session_at": "2026-10-11T14:10:00Z",
                  "last_session_id": "…", "live_now": false } ] }
```
```json
{ "patient_id": "maya", "days": 21, "events": [ { "id": 3, "time": "…", "kind": "compensation",
  "message": "…", "evidence": {}, "acknowledged": false } ] }
```
`week` = whole weeks since the patient's first session (or `created_at`), minimum 1.

- [ ] **Step 1: Failing tests** (`server/tests/test_dashboard_api.py`), following `test_api_db.py` patterns:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.tests.conftest import AUTH

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_dashboard_routes_need_the_token(client: TestClient) -> None:
    for path in ("/caseload", "/patients/maya/memory"):
        assert client.get(path).status_code == 401


def test_dashboard_routes_503_without_db(client: TestClient) -> None:
    assert client.get("/caseload", headers=AUTH).status_code == 503
    assert client.get("/patients/maya/memory", headers=AUTH).status_code == 503


def test_caseload_sample_shape() -> None:
    body = json.loads((SAMPLES / "rest_get_caseload.json").read_text())
    assert set(body) == {"therapist_id", "generated_at", "today", "counts", "patients"}
    assert set(body["counts"]) == {"all", "needs_you", "watch", "on_track"}
    p = body["patients"][0]
    for key in ("id", "name", "bucket", "severity", "reason", "evidence", "week", "last_session_at", "live_now"):
        assert key in p
    assert [x["severity"] for x in body["patients"]] == sorted((x["severity"] for x in body["patients"]), reverse=True)
```
  Plus DB-backed tests (skipped without the local Docker DB, using `server/tests/dbutil.py` like
  `test_api_db.py`): seed demo data, `GET /caseload?therapist_id=okafor` returns only okafor's patients, every
  patient has a bucket, buckets sorted by severity; a patient with an open fall alert is first; a patient of another
  therapist is absent; `GET /patients/maya/memory?days=21` returns newest first and `days` outside 1–90 → 422.
- [ ] **Step 2: Run** `.venv/bin/pytest server/tests/test_dashboard_api.py -v` — FAIL.
- [ ] **Step 3: Implement.** `dashboard_queries.caseload_signals(db, therapist_id) → list[dict]` in **one** SQL
  statement (CTEs: `pts` patients of therapist; `last2` last two finished sessions per patient via
  `ROW_NUMBER()`; `eff` last 3 efforts; `fl` flagged-rep counts and top flag via `unnest(flags)` on `reps` of those
  two sessions; `al` latest open alert per patient from `alerts` grouped by `alert_id` with no `resolved` step;
  `mem` unacknowledged `memory_events` of the last 21 days as `jsonb_agg` newest first; `missed` = days since last
  session when the active prescription's `targets->>'sessions_per_week'` ≥ 5, else 0, capped at 7). Bounded:
  `LIMIT 500` patients, `timeout_s=3.0`. `memory_events(db, patient_id, days)` simple SELECT ordered by time desc,
  `LIMIT 100`. `api/dashboard.py`: `GET /caseload(therapist_id: str = Query(pattern=ID_RE, max_length=64))`,
  builds `Signals`, calls `triage`, sorts by `(-severity, last signal time desc)`, computes `counts` and `today`
  (`sessions_done` = sessions of those patients started today in the server's local day; `live_now` from
  `get_state().live` snapshot when `phase` not in `("idle", "done")`). `GET /patients/{patient_id}/memory(days:
  int = Query(21, ge=1, le=90))`, 404 if the patient doesn't exist. Path ids validated with the same
  `^[A-Za-z0-9_-]{1,64}$` pattern. Write both sample files from a real response against demo data
  (`make demo-data`), trimmed to 3 patients / 3 events.
- [ ] **Step 4: Run** `make test` — PASS (DB tests run when the local DB is up: `make db-local && make db-setup
  DSN=… && make demo-data`).
- [ ] **Step 5: Commit** `git commit -m "Add GET /caseload (triage) and GET /patients/{id}/memory for the dashboard"`

### Task 9: Caseload screen

**Files:**
- Create: `device/dashboard/js/screens/caseload/{model,view,index}.js`, `css/screens/caseload.css`,
  `js/ui/statusIcon.js`, `js/ui/aiDraftChip.js`
- Test: `device/dashboard/tests/caseload.test.mjs`

**Interfaces:**
- Consumes: `ep.caseload()`, `ep.proposals({status: "pending"})`, sockets `events` (refresh on `alert`,
  `proposal_created`, `proposal_decided`, `summary_ready`, `set_done`), store `proposalsWaiting`.
- Produces: `caseloadModel(caseloadJson, proposalsJson, {filter, search, showAllOnTrack, now}) →
  {dateLabel, subtitle, tabs: [{key, label, count, active}], groups: [{key, label, count, rows: [{id, name, age,
  sub, statusIcon, reason, when, evidence, evidenceTone: "alert"|"plain", href}]}], moreOnTrack: number,
  today: {done, live}, copilot: {count, items: [{text}]}, live: {name, line, href}|null}`.
  `statusIcon(kind: "needs"|"watch"|"ok")` → icon + visually hidden label ("Needs you" / "Watch" / "On track").

- [ ] **Step 1: Failing test** using `server/samples/rest_get_caseload.json` and `rest_get_proposals.json`:
  - subtitle `"9 patients, sorted by who needs you first"` (uses `counts.all`);
  - tabs `All 9, Needs you 3, Watch 2, On track 4`, active per `filter` (`needs` → `needs_you`);
  - row `sub` = `"Right knee replacement · wk 3"` (condition capitalised); `when` = `"Last session today, 9:10"` or
    `"Live now"` when `live_now`; needs-you evidence tone `alert`, others `plain`;
  - `filter: "on_track"` shows only that group; on-track group shows 1 row + `moreOnTrack: 3` until
    `showAllOnTrack` (Figma "Show 3 more on track");
  - `search: "lin"` matches "Linda Park" case-insensitively across all buckets;
  - Copilot items: `"<first name>: <proposal summary>"` for up to 3, where summary comes from `change`
    (`add: ["weight_shift_drill"]` → "add a weight-shift drill"; `sets/reps` → "change squats to 3 × 8";
    `targets.knee_deg` → "raise knee bend target to 95°") falling back to the first sentence of `rationale`;
  - `live` = `{name: "George Alvarez", line: "Squats · set 2 of 3 · rep 7", href: "#/live/george"}`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** model + view per Figma `205:205`:
  page head (date meta, "Caseload", subtitle; right: search input with icon, "Add patient" primary button rendered
  only if `caps.has("/patients/{patient_id}/pairing-code")` — click opens a small dialog that calls it and shows the
  6-character code in large type with "Read this to the patient in the Mira app"), pill tabs (`role="tablist"`,
  updates `#/caseload?filter=`), table card (`role="table"` with column headers PATIENT / WHY NOW / EVIDENCE, group
  header rows, each row a link `<a class="row" href="#/patients/:id">` with status icon, name + sub, reason + when,
  evidence, chevron), right rail cards: Today (Sessions done / Live right now), Care Copilot (✦ AI draft chip, "N
  proposals are waiting for your review.", up to 3 sunk items, "Review proposals" → `#/copilot`), Live now (dot,
  name, line, "Watch live twin" → live href; hidden when null). Empty bucket → `empty()` "Nobody here right now".
  `index.js`: load both in parallel (`Promise.allSettled`; caseload error → `errorState` with retry; proposals error →
  hide that card), subscribe to `events` for refresh (debounced 1 s), set `store.proposalsWaiting`, set
  `store.selectedPatientId` on row click. Unmount unsubscribes.
- [ ] **Step 4: Run** tests — PASS. Manual: compare with Figma `205:205`, `227:794`, `227:1023`, `227:1200` at
  1440×1024.
- [ ] **Step 5: Commit** `git commit -m "Caseload screen: triage table, filters, search, Today, Copilot and Live-now cards"`

### Task 10: Milestone gate M2 — review and debug

- [ ] Code review over the M2 commits (SQL: one query, bounded, parameterised, no f-strings; triage rule order
  matches spec; screen escapes all text). Fix findings.
- [ ] `make test`, `make test-dashboard`; manual check with `make demo-data` + `make run`; trigger
  `POST /demo/trigger_fall` (with `DEMO_TOOLS_ENABLED=1`) and confirm the patient jumps to "Needs you" within ~1 s.
- [ ] Commit fixes.

---

# M3 — Patient review

### Task 11: Server — progress-note approval

**Files:**
- Modify: `server/api/dashboard.py`; Create: `server/samples/rest_post_progress_note.json`
- Test: `server/tests/test_dashboard_api.py` (append)

**Interfaces:**
- Produces: `POST /sessions/{session_id}/progress-note {therapist_id, text}` (text 1–4000 chars) → `{"ok": true,
  "session_id": "…", "approved_by": "okafor", "approved_at": "…"}`; 404 unknown session; 503 without DB.
  Stored with `writes.insert_agent_action(db, "therapist", "progress_note_approved", patient_id, session_id,
  {"text": text, "edited": text != session.ai_note})`. `GET /sessions/{id}` is unchanged; the dashboard reads the
  approval through `GET /sessions/{id}/progress-note` → `{"approved": bool, "text": str|null, "approved_by",
  "approved_at"}` (latest such action).

- [ ] Steps: failing tests (401 without token, 503 without DB, 422 on empty text / 4001 chars, DB-backed round trip:
  POST then GET returns `approved: true` and the text) → implement → pass → commit
  `"Add progress-note approval routes for the dashboard"`.

### Task 12: 3D room (Three.js)

**Files:**
- Create: `device/dashboard/js/three/{joints,skeleton,room}.js`
- Test: `device/dashboard/tests/joints.test.mjs`

**Interfaces:**
- Produces:
  - `joints.js` (pure, testable): MediaPipe 33-landmark indices `NOSE=0, L_SHOULDER=11, R_SHOULDER=12, L_ELBOW=13,
    R_ELBOW=14, L_WRIST=15, R_WRIST=16, L_HIP=23, R_HIP=24, L_KNEE=25, R_KNEE=26, L_ANKLE=27, R_ANKLE=28,
    L_HEEL=29, R_HEEL=30, L_FOOT=31, R_FOOT=32`; `BONES` = list of `[a, b, segment]` with `segment` ∈
    `"trunk"|"l_arm"|"r_arm"|"l_thigh"|"r_thigh"|"l_shin"|"r_shin"|"l_foot"|"r_foot"`;
    `kneeFlexion(joints, side)` (degrees, 0 = straight, per README "Frame contract");
    `hipFlexion(joints, side)`; `trunkLean(joints)`; `flagSegments(flags: string[], affectedSide) → Set<segment>`
    (`knee_cave_r` → r_thigh+r_shin; `weight_shift` → affected side thigh+shin+foot; `trunk_lean` → trunk);
    `lerpJoints(a, b, t)`; `isValidJoints(j)` (array of 33 `[x,y,z]` finite numbers).
  - `skeleton.js`: `createSkeleton(THREE, {ghost=false}) → {group, update(joints, {redSegments}), dispose()}` —
    cylinders between joints (radius 0.035 m limbs, trunk as a flat box from shoulders to hips), icosahedron head at
    the nose, small spheres at joints (ink), grey `#9a96a0` limbs, red `#d92d20` when in `redSegments`; ghost =
    opacity 0.35, `depthWrite: false`.
  - `room.js`: `createRoom(container, {interactive=true}) → {addSkeleton(opts) → skeleton, setView("front"|"side"|
    "top"), labels: {set(key, worldPos, text, tone)}, resize(), render(), dispose()}`. Scene: white background
    (projector: `--color-card`), floor disc (ellipse 1.6 × 1.0 m, `#eeedf0`), hemispheric light, no shadows,
    `PerspectiveCamera(35°)`, `OrbitControls` (damping, min/max distance, no pan below floor), presets: front
    (0, 1.0, 3.2), side (3.2, 1.0, 0), top (0, 4.0, 0.01). Joint-angle labels are HTML overlays projected each frame
    (`Vector3.project`), pill style from components frame (`R knee 84°`), `aria-hidden` with the same values
    repeated in the "This session" card for screen readers. Renders on demand (`requestAnimationFrame` only while
    playing/live or orbiting). `dispose()` frees geometries, materials, renderer, listeners.
- Coordinate mapping: joints are in the mat frame (x across, y up, z toward Mira, metres) = Three.js y-up; map
  directly (`VICTORIA.md`).

- [ ] **Step 1: Failing test** `tests/joints.test.mjs` with `server/samples/ws_pose.json`: `isValidJoints` true;
  `kneeFlexion` of the sample standing pose is < 25; build a synthetic right leg with hip (0,1,0), knee (0,0.5,0),
  ankle (0,0.5,0.5) → right knee flexion 90 ± 0.5; `flagSegments(["knee_cave_r"], "right")` = {r_thigh, r_shin};
  `lerpJoints` midpoint; malformed input (32 joints, NaN) → `isValidJoints` false.
- [ ] **Step 2–4:** implement, run `make test-dashboard`, then a manual smoke page
  `device/dashboard/dev/room.html` (dev-only, not served) that renders `ws_pose.json` in the room; check 60 fps in
  the Performance panel at 1440×1024.
- [ ] **Step 5: Commit** `git commit -m "Dashboard 3D room: 33-joint skeleton, view presets, angle labels"`

### Task 13: Patient review — models, replay player, rep timeline

**Files:**
- Create: `device/dashboard/js/screens/patient/{model,player}.js`, `js/ui/{balanceBar,lineChart}.js`
- Test: `device/dashboard/tests/patient-model.test.mjs`, `tests/player.test.mjs`

**Interfaces:**
- Produces:
  - `sessionCardModel(sessionJson, patient) → {good, total, avgDepth, load: {affected, other, affectedSide,
    flagged: bool}, flags: {count, label}, pain, effort}` — `affected` = 100 − `tw_load_left_pct` when the affected
    side is right; `flagged` when `affected < 50 − threshold` (threshold = prescription `rule_overrides.weight_shift_pct`
    or 15).
  - `repTimelineModel(sessionJson) → [{rep, set, flagged, flags, tStart, tEnd, label}]` — `tStart` = previous rep's
    `ended_at` (or `session.started_at`), `flagged` = `flags.length > 0 || quality < 1`.
  - `trendModel(progressJson, affectedSide, now, days=21) → {points: {depth: [[iso, v]], load: [[iso, v]]},
    labels: [4 date ticks], caption: "Depth 70° → 92°", loadCaption: "Right leg load 47% → 36%", compensating: bool,
    insight: "Depth is up while load on the injured leg drops. Maya is compensating." | null}`. Compensating =
    depth up ≥ 5° and affected load down ≥ 3 points (first 3 days vs last 3 days, the Copilot's rule).
  - `memoryModel(memoryJson, lastVisitIso) → {subtitle: "What changed since your visit on Oct 4", items: [text]}`.
  - `noteModel(sessionJson, approval) → {text, label: "AI draft — review before use"|"Approved by you",
    canApprove: bool}`.
  - `player.js`: `createPlayer({frames, reps, onFrame}) → {play(), pause(), seekRep(n), seekTime(t), speed(x),
    state() → {playing, t, duration, rep}}`; frames are sorted by time; uses `requestAnimationFrame` injected for
    tests (`raf`, `now`). Loads full frames lazily: first page via `framesPage(sid)`, follows `next_t0` until null
    (max 20 pages, abort on unmount).
  - `lineChart(series: [{name, points: [[x, y]], style: "solid"|"dashed", tone}], {height, xLabels, yLabel}) →
    SVGElement` — used by the trend card and by Copilot answer charts. Pure SVG, `role="img"` with an
    `aria-label` summarising first/last values.
  - `balanceBar({affected, other, affectedSide, flagged}) → element` (two bars + labels, ⚠ on the flagged side).

- [ ] **Step 1: Failing tests** against `rest_get_session.json`, `rest_get_patient_progress.json`,
  `rest_get_patient_memory.json`, and a synthetic 21-day progress series reproducing the Figma numbers (depth 70→92,
  load 47→36 → `compensating: true`, insight text exact). Player test: 3 frames at t=0, 0.5, 1.0 s, fake raf/clock:
  `play()` advances, `seekRep(2)` jumps to that rep's `tStart`, `speed(0.5)` halves progress, `pause()` stops.
- [ ] **Steps 2–5:** implement, pass, commit `"Patient review models, replay player, line chart and balance bar"`.

### Task 14: Patient review screen

**Files:**
- Create: `device/dashboard/js/screens/patient/{view,index}.js`, `css/screens/patient.css`
- Modify: `js/main.js` (`patient`/`replay` routes → this screen)

**Interfaces:**
- Consumes: Tasks 11–13; `ep.patient`, `ep.session`, `ep.framesPage`, `ep.progress`, `ep.memory`,
  `ep.approveProgressNote`, `GET /sessions/{id}/progress-note`; sockets `pose` + `live` for the Live tab.
- Behaviour (Figma `206:202`):
  - Head: meta "Maya Ruiz, 67 · Right knee replacement · week 3", title "Squats, today at 9:10", segmented
    Live/Replay (Live enabled only when `/ws/live` shows this patient active), session picker button (calendar icon,
    lists the 10 recent sessions from `GET /patients/{id}`), "Message Maya" only if
    `caps.has("/patients/{patient_id}/messages")`.
  - `#/patients/:pid` opens the most recent session; `#/replay/:sid` opens that session (and its patient).
  - Room card: Front/Side/Top segmented, "Drag to orbit · scroll to zoom", "Replaying rep 7 of 12" pill, angle
    labels for both knees, hips and trunk, flag callout card ("Weight shift — 28% more weight on the left leg this
    rep") when the current rep is flagged, balance bar under the floor.
  - Timeline card: play/pause, `m:ss / m:ss`, rep blocks (good = green, flagged = red with ⚠ above, current =
    outlined), legend, click to jump, keyboard: ←/→ previous/next rep, space play/pause.
  - "This session" card, "3-week trend" card (lineChart, captions, insight callout), "Therapist Memory" card,
    "Progress note" card (AI draft chip; Edit turns the text into a `<textarea>`; Approve posts and swaps the chip
    to "Approved by you"; disabled while saving; errors as toast).
  - Live tab: subscribe `pose` (lerp ~60 ms behind), `live` for rep/set/hr; unsubscribe on tab change/unmount.
  - Missing data: no sessions → empty state with "No sessions yet"; frames 503 → room shows `errorState`; each card
    loads independently (`Promise.allSettled`).
- [ ] Steps: build view; manual test against `make demo-data` + mock engine (replay a demo session, switch to Live
  during a mock session); verify no memory leak by switching routes 20× and checking the JS heap in DevTools;
  commit `"Patient review screen: 3D replay, rep timeline, session, trend, memory and progress-note cards"`.

### Task 15: Milestone gate M3 — review and debug

- [ ] Code review (Three.js disposal, socket unsubscribe on unmount, frames paging bounds, player correctness,
  approval flow, all text via `textContent`). Fix findings. `make test`, `make test-dashboard`, manual run.
  Commit fixes.

---

# M4 — Prescribe

### Task 16: Server — heart-rate limit

**Files:**
- Create: `server/db/dashboard_writes.py`, `server/samples/rest_patch_patient_limits.json`
- Modify: `server/api/dashboard.py`; Test: `server/tests/test_dashboard_api.py` (append)

**Interfaces:**
- Produces: `PATCH /patients/{patient_id}/limits {therapist_id, hr_limit: int 40..220}` → `{"patient_id",
  "hr_limit"}`; 404 unknown patient; 403 if `patients.therapist_id != therapist_id` (same rule as proposals);
  records `agent_actions(agent="therapist", action="hr_limit_set", detail={"from", "to"})`.
- [ ] Steps: failing tests (401, 422 for 39/221, 503 no DB, DB-backed: 403 for another therapist, value persisted and
  visible in `GET /patients/{id}` as `hr_limit`) → implement (`dashboard_writes.set_hr_limit(db, patient_id,
  hr_limit) → int|None` using `_safe`-style error handling) → pass → commit `"Add PATCH /patients/{id}/limits (heart-rate limit)"`.

### Task 17: Prescribe screen + shared proposal cards

**Files:**
- Create: `device/dashboard/js/screens/prescribe/{model,view,index}.js`, `css/screens/prescribe.css`,
  `js/ui/{proposalCard,stepper,slider,segmented}.js`
- Test: `device/dashboard/tests/prescribe-model.test.mjs`, `tests/proposal-card.test.mjs`

**Interfaces:**
- Produces:
  - `prescribeModel(patientJson, prescriptionsJson, progressJson) → {exercises: [{id, name, dose: "3 × 10",
    active, selected}], editor: {exerciseId, sets, reps, restS, kneeTarget, tempoS, lastChanged: "Oct 4",
    todayAvg: "Today’s average was 92°."}, flags: {weightShiftPct, kneeCaveDeg, trunkLeanDeg}, hrLimit,
    emergency: {name, relation, phone}}`. Defaults when absent: rest 60 s, tempo 3 s, flags 15 % / 10° / 20°,
    hrLimit = `patient.hr_limit` or 120.
  - `diffPrescription(original, edited) → {exercise_id, sets?, reps?, targets?, limits?, rule_overrides?}|null` —
    only changed fields; `targets.knee_deg`, `targets.tempo_s`, `limits.min_rest_s/max_rest_s` stay inside existing
    limits; `rule_overrides` keys: `weight_shift_pct`, `knee_cave_deg`, `trunk_lean_deg` (documented in the README
    as the contract for the vision engine's form rules).
  - `proposalCard(proposal, {onApprove, onEdit, onDismiss, compact}) → element` — patient name with status icon,
    AI draft chip, title (summary from the caseload model's `proposalSummary`, moved to `js/ui/proposalCard.js` and
    re-exported), change row ("Squats: 3 × 12 → 3 × 8" / "Cue: none → “Even out your weight”"), "Why: …"
    (rationale's first sentence), Dismiss / Edit / Approve. Edit opens an inline form for `sets`, `reps`,
    `rest_days_between`, `targets.knee_deg` (the `PlanChange` fields) and posts `editProposal`.
  - Steppers: −/+ buttons with `aria-label`s, min/max from `limits` (sets 1–10, reps `min_reps..max_reps`, rest
    `min_rest_s..max_rest_s` in 15 s steps). Sliders: native `<input type="range">` styled, with the value shown and
    `aria-valuetext` ("15 percent").
- Behaviour (Figma `207:282`): Exercise plan card (checkbox list, selecting an exercise loads it in the editor;
  "Add from exercise library" search filters `server/exercises.py` ids when `GET /exercises` exists (capability),
  otherwise hidden), editor card, "When should Mira flag a rep?" card, Heart rate limit card, Emergency contact card
  (read-only; "Edit" hidden — no endpoint), Copilot proposals rail (pending proposals for this patient via
  `proposalCard`). "Reference rep" card is shown with a "Stretch" chip and a disabled button with a tooltip
  "Coming later". "Save plan": computes `diffPrescription` for each edited exercise and `PUT`s them in sequence,
  then `PATCH` limits if changed; shows a toast "Saved. Changes reach Mira the next time Maya starts a session.";
  disabled when nothing changed. "Discard changes" resets. Leaving the route with unsaved changes asks via an
  in-page confirm bar (never `window.confirm`).
- [ ] Steps: failing model tests (defaults; diff produces only changed fields; knee target change goes to
  `targets.knee_deg`; untouched → `null`; proposal summary strings exactly as in Figma `209:490`) → implement →
  manual run (save, reload, values persist; approve a proposal, it disappears and the plan reloads) → commit
  `"Prescribe screen and shared Copilot proposal cards"`.

### Task 18: Milestone gate M4 — review and debug

- [ ] Code review (writes only on explicit click; diff minimal; 409 handling refreshes the card; limits respected).
  Fix findings, run all tests, manual run, commit.

---

# M5 — Care Copilot, alerts, notifications

### Task 19: Care Copilot screen

**Files:**
- Create: `device/dashboard/js/screens/copilot/{model,view,index}.js`, `css/screens/copilot.css`
- Test: `device/dashboard/tests/copilot-model.test.mjs`

**Interfaces:**
- Produces: `answerModel(askJson) → {paragraphs: [string], charts: [lineChart series], evidence: [chip text],
  sql: [string], proposalsNote: "I drafted 1 change to her plan. See Proposals." | null, fallback: string|null,
  cached: bool}`; `proposalsTabsModel(list, tab) → {tabs: [{key:"pending", label:"Waiting", count}, {key:"approved"},
  {key:"dismissed"}], items}`.
- Behaviour (Figma `209:490`): chat column (therapist bubbles right, dark; Copilot bubbles left with "Copilot ✦ AI
  draft"), evidence chips, charts via `lineChart`, collapsible "Show the query" (`<details>` with `<pre>` per SQL),
  suggested questions ("Summarise today", "Who missed sessions?", "Draft progress notes", plus "Which of my
  patients are compensating?"), input + Send (Enter sends, Shift+Enter newline, disabled while waiting, 30 s
  client timeout for this call, a typing indicator). "All patients" dropdown sets `patient_id`. Drafted messages
  (from `agent_actions` draft_message) show "Approve and send" only if `caps.has("/patients/{patient_id}/messages")`,
  otherwise "Copy text". Proposals rail with Waiting/Approved/Dismissed tabs using `proposalCard`;
  `#/proposals/:id` scrolls to and highlights that card. Conversation kept in memory for the tab only (no storage:
  it can contain patient data).
- [ ] Steps: failing model tests with `rest_post_copilot_ask.json` (paragraph split on blank lines/numbered list,
  evidence chips, chart conversion, fallback text) → implement → manual run with `GEMINI_MODE=fake` → commit
  `"Care Copilot screen: chat, charts, show-the-query, proposals tabs"`.

### Task 20: Alerts banner, notifications bell and toasts

**Files:**
- Create: `device/dashboard/js/ui/{alertBanner,notificationsBell}.js`, `js/core/alerts-model.js`
- Modify: `js/main.js`, `js/ui/sidebar.js` (bell + unread count in the sidebar header)
- Test: `device/dashboard/tests/alerts-model.test.mjs`

**Interfaces:**
- Produces: `alertsModel(alertsJson, patientsById) → [{alertId, patientId, name, kind, title: "Fall — Maya Ruiz",
  steps: [{step, label: "Detected"|"Checked in"|"Escalated"|"Resolved", time, done}], replayHref, canResolve}]`;
  `applyAlertEvent(model, wsAlertEvent)`; banner shown at the top of `#main` on every screen while any alert is open
  (`role="alert"`), with the escalation timeline (detected → checked in → escalated → resolved), "View replay"
  (`#/replay/<sid>`) and "Mark resolved" (note field, `resolveAlert`). Bell: unread count from
  `GET /notifications/count`, live from `/ws/notifications`; panel lists the latest 20 with read/ack actions;
  urgent unacked items show a toast with "Acknowledge". Notification links go through `safeInternalLink`.
  "Enable desktop alerts" button (only on a secure context) requests `Notification.requestPermission()` from the
  click (README rule).
- [ ] Steps: failing tests with `rest_get_alerts.json` + `ws_events_alert.json` (step order, labels, resolved
  removes the banner, unknown step ignored) → implement → manual: `POST /demo/trigger_fall` shows the banner and
  updates steps live → commit `"Dashboard alerts banner, notifications bell and toasts"`.

### Task 21: Milestone gate M5 — review and debug

- [ ] Code review (AI text rendering, SQL shown as text only, link filtering, notification permission only on
  click, no patient data persisted in storage). Fix, test, manual run, commit.

---

# M6 — Live session, Time-lapse, projector polish

### Task 22: Live session screen

**Files:**
- Create: `device/dashboard/js/screens/live/{model,view,index}.js`, `css/screens/live.css`
- Test: `device/dashboard/tests/live-model.test.mjs`

**Interfaces:**
- Produces: `liveModel(liveSnapshot, patient, now) → {active, title, rep: "Rep 7", line: "Squats · set 2 of 3",
  hr: "104 bpm"|null, latency: "20 Hz · about 140 ms behind · smoothed", balance: {...}, angles: {kneeL, kneeR},
  paused, incident}`; `cueHistoryModel(events) → [{time: "9:41", text}]` from `cue` events with `source` text.
- Behaviour (Figma `210:820`): LIVE badge, room with live skeleton (`pose` channel, lerp 60 ms behind), rep card,
  HR chip, knee labels, balance bar; "Send a cue" panel rendered only if `caps.has("/live/say")` (the bridge
  endpoint); otherwise the panel shows "Cues from the dashboard arrive with the device bridge." Quick cues as
  chips. "Said this session" list from `/ws/events` `cue` events. "Leave session" → back to the patient. When no
  session is live for this patient: empty state "George isn't in a session right now" with a link to the replay.
  `#/live` (no pid) → the patient currently live, else caseload.
- [ ] Steps: failing model tests with `ws_live.json` and `ws_events_cue.json` → implement → manual run with the mock
  engine (smooth 60 fps, sockets closed after leaving: check `GET /stats` hub client counts) → commit
  `"Live session screen with the live 3D twin"`.

### Task 23: Recovery time-lapse screen

**Files:**
- Create: `device/dashboard/js/screens/timelapse/{model,view,index}.js`, `css/screens/timelapse.css`
- Test: `device/dashboard/tests/timelapse-model.test.mjs`

**Interfaces:**
- Produces: `bestRep(sessionJson) → {rep, tStart, tEnd}` (highest `peak_angle` among unflagged reps, else any);
  `timelapseModel(sessA, sessB, patient) → {left: {label: "Week 1", date: "Sep 20", depth, load}, right: {...},
  headline: "Depth +22°, right-leg load 36% → 47%"}`; `syncClock(durA, durB, t)` maps one shared 0..1 progress to
  both reps.
- Behaviour (Figma `208:339`): two date pickers (sessions of the chosen exercise), exercise picker, Front/Side view,
  Side-by-side / Overlay toggle (overlay = one room, earlier session as ghost skeleton), the two rooms replay the
  best rep of each session in sync, 0.5× / 1× speed, play/pause, "Add to progress note" (appends the headline to the
  progress-note draft of the latest session and navigates there; the therapist still approves it).
- [ ] Steps: failing model tests → implement (reuse `createRoom`, `createSkeleton({ghost: true})`, `createPlayer`)
  → manual run → commit `"Recovery time-lapse: synced side-by-side and overlay replays"`.

### Task 24: Projector theme, responsive and accessibility pass

**Files:**
- Modify: `css/*.css`, `js/ui/sidebar.js` (theme toggle in the footer), `device/dashboard/README.md` (create)
- [ ] Theme toggle "Projector mode" sets `data-theme="projector"` on `<html>`, remembered in `localStorage`
  key `mira.dashboard.theme` (convenience only, wrapped in try/catch); the Three.js scene reads the CSS variables on
  theme change.
- [ ] Check every screen at 1280×800, 1440×1024 and 1920×1080 in both themes; fix overflow; tab through every
  screen (logical order, visible focus, no traps); run Lighthouse accessibility ≥ 95 on Caseload and Patient review.
- [ ] README: how to run (`make run`, open `/dashboard/`), sign in, test (`make test-dashboard`), the screen →
  endpoint table, the capability-gated features and the endpoints they wait for, the `rule_overrides` keys, and the
  security rules (no innerHTML, token storage, CSP hash update command).
- [ ] Commit `"Dashboard projector theme, responsive and accessibility pass, README"`.

### Task 25: Milestone gate M6 — final review and debug

- [ ] Full-branch code review (superpowers:requesting-code-review) against the spec, then fix.
- [ ] `make test`, `make test-dashboard`, manual end-to-end demo script: sign in → Caseload → Maya (needs you) →
  replay rep 7 → approve progress note → Prescribe: raise knee target to 95°, save → Copilot: ask "Which of my
  patients are compensating?", approve the proposal → trigger a fall: banner + escalation → Live session → Time-lapse.
- [ ] Use superpowers:finishing-a-development-branch to decide merge/PR.
