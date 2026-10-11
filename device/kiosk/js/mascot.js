// Mira's mood → image. assets/mira/mascot.json maps each mood to {src} today; a mood may later be
// {frames: [...], fps, loop} for frame animation without touching the rest of the page.

const MOODS = ["idle", "worried", "cheering", "sleeping", "celebrating"];

export async function loadMascot(fetchImpl = globalThis.fetch, base = "assets/mira/") {
  let manifest = { moods: {} };
  try {
    const res = await fetchImpl(`${base}mascot.json`);
    if (res.ok) {
      const parsed = await res.json();
      // A malformed manifest (not an object, or no object `moods`) falls back to the default file names
      // instead of throwing when entry() indexes into it below.
      if (parsed && typeof parsed === "object" && parsed.moods && typeof parsed.moods === "object") {
        manifest = parsed;
      }
    }
  } catch { /* fall back to the default file names */ }
  const entry = (mood) => {
    const known = MOODS.includes(mood) ? mood : "idle";
    return manifest.moods[known] ?? { src: `${known}.png` };
  };
  return {
    moods: MOODS,
    srcFor: (mood) => `${base}${entry(mood).src ?? `${MOODS.includes(mood) ? mood : "idle"}.png`}`,
    framesFor: (mood) => {
      const e = entry(mood);
      return Array.isArray(e.frames) ? { frames: e.frames.map((f) => `${base}${f}`), fps: e.fps ?? 12,
        loop: e.loop !== false } : null;
    },
  };
}
