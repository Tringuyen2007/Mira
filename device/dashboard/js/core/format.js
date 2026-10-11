// Date/number copy for the dashboard; strings match the Figma frames exactly.
function toDate(iso) {
  return iso instanceof Date ? iso : new Date(iso);
}

export function fmtDateLong(iso, now) {
  const d = toDate(iso ?? now);
  return new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" }).format(d);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function fmtWhen(iso, now) {
  const d = toDate(iso);
  const n = toDate(now);
  if (sameDay(d, n)) {
    return `today, ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const yesterday = new Date(n);
  yesterday.setDate(n.getDate() - 1);
  if (sameDay(d, yesterday)) return "yesterday";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(d);
}

export function fmtDeg(n) {
  return n === null || n === undefined || Number.isNaN(n) ? "—" : `${Math.round(n)}°`;
}

export function fmtPct(n) {
  return n === null || n === undefined || Number.isNaN(n) ? "—" : `${Math.round(n)}%`;
}

export function fmtSide(side) {
  return side === "right" || side === "R" ? "Right" : side === "left" || side === "L" ? "Left" : side;
}

export function firstName(name) {
  return String(name || "").trim().split(/\s+/)[0] || "";
}

export function initials(name) {
  const words = String(name || "")
    .replace(/,.*/, "")
    .split(/\s+/)
    .filter((w) => w && !/^(dr|mr|mrs|ms)\.?$/i.test(w));
  return words.slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

export function weeksSince(iso, now) {
  const ms = toDate(now) - toDate(iso);
  return Math.floor(ms / (7 * 24 * 60 * 60 * 1000));
}
