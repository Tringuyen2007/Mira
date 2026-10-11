// Projector theme: data-theme="projector" on <html>, remembered in localStorage key "mira.dashboard.theme"
// (convenience only, wrapped in try/catch — a blocked or full store never breaks the toggle, it just forgets the
// choice on reload). main.js calls applyStoredTheme() synchronously as its first statement, before anything else
// runs, so the right theme is already set when the page paints (no inline script is allowed by the CSP, so this
// is the earliest JS can run).
const STORAGE_KEY = "mira.dashboard.theme";
const THEMES = new Set(["light", "projector"]);

function readStoredTheme() {
  try {
    const v = globalThis.localStorage?.getItem(STORAGE_KEY);
    return THEMES.has(v) ? v : "light";
  } catch {
    return "light";
  }
}

function writeStoredTheme(theme) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, theme);
  } catch { /* convenience only; the toggle still works for the rest of this tab's session */ }
}

function root(doc) {
  return (doc || globalThis.document)?.documentElement || null;
}

// Call once, synchronously, before first paint. Returns the theme it applied.
export function applyStoredTheme(doc) {
  const theme = readStoredTheme();
  root(doc)?.setAttribute("data-theme", theme);
  return theme;
}

export function getTheme(doc) {
  const value = root(doc)?.getAttribute("data-theme");
  return THEMES.has(value) ? value : "light";
}

// Sets the theme, persists it (best-effort) and tells anything that reads raw colours instead of CSS variables
// (a future canvas/WebGL avatar renderer per js/avatar/README.md) that it changed.
export function setTheme(theme, doc) {
  const next = THEMES.has(theme) ? theme : "light";
  root(doc)?.setAttribute("data-theme", next);
  writeStoredTheme(next);
  globalThis.dispatchEvent?.(new globalThis.CustomEvent("mira:theme-change", { detail: { theme: next } }));
  return next;
}

export function toggleTheme(doc) {
  return setTheme(getTheme(doc) === "projector" ? "light" : "projector", doc);
}
