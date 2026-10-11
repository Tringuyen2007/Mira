// Date/number copy for the dashboard; strings match the Figma frames exactly.
function toDate(iso) {
  return iso instanceof Date ? iso : new Date(iso);
}

function isValidDate(d) {
  return d instanceof Date && !Number.isNaN(d.getTime());
}

export function fmtDateLong(iso, now) {
  const d = toDate(iso ?? now);
  if (!isValidDate(d)) return "—";
  return new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" }).format(d);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function fmtWhen(iso, now) {
  const d = toDate(iso);
  if (!isValidDate(d)) return "—";
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
  const num = Number(n);
  return n === null || n === undefined || !Number.isFinite(num) ? "—" : `${Math.round(num)}°`;
}

export function fmtPct(n) {
  const num = Number(n);
  return n === null || n === undefined || !Number.isFinite(num) ? "—" : `${Math.round(num)}%`;
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

export function capitalize(s) {
  const str = String(s || "");
  return str ? str[0].toUpperCase() + str.slice(1) : str;
}

function clock(d) {
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// Session titles (Figma): "today at 9:10", "yesterday at 18:00", "Oct 7 at 10:00".
export function fmtWhenAt(iso, now) {
  const d = toDate(iso);
  if (!isValidDate(d)) return "—";
  const n = toDate(now);
  if (sameDay(d, n)) return `today at ${clock(d)}`;
  const yesterday = new Date(n);
  yesterday.setDate(n.getDate() - 1);
  if (sameDay(d, yesterday)) return `yesterday at ${clock(d)}`;
  return `${new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(d)} at ${clock(d)}`;
}

// Head meta line shared by Patient review, Prescribe and Time-lapse: "Maya Ruiz, 67 · Right knee replacement ·
// week 3". GET /patients/{id} has no first-session date, so the week counts from the patient's created_at.
export function patientMeta(patient, now = new Date()) {
  if (!patient) return "";
  const who = [patient.name, patient.age != null ? String(patient.age) : null].filter(Boolean).join(", ");
  const week = patient.created_at != null && isValidDate(toDate(patient.created_at))
    ? `week ${Math.max(1, weeksSince(patient.created_at, now) + 1)}` : null;
  return [who, patient.condition ? capitalize(patient.condition) : null, week].filter(Boolean).join(" · ");
}

// One load value, two labels that always add up to 100: left = round(load_l_pct), right = 100 - left
// (rounding each side separately can print 48% / 53%). Null when the value is missing.
export function loadSplit(loadLeftPct) {
  const n = Number(loadLeftPct);
  if (loadLeftPct === null || loadLeftPct === undefined || !Number.isFinite(n)) return null;
  const left = Math.min(100, Math.max(0, Math.round(n)));
  return { left, right: 100 - left };
}
