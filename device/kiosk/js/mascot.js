// Mira's mood → image. assets/mira/mascot.json maps each mood to {src} today; a mood may later be
// {frames: [...], fps, loop} for frame animation without touching the rest of the page.

const MOODS = ["idle", "worried", "cheering", "sleeping", "celebrating"];

export async function loadMascot(fetchImpl = globalThis.fetch, base = "assets/mira/") {
  let manifest = { moods: {} };
  try {
    const res = await fetchImpl(`${base}mascot.json`);
    if (res.ok) manifest = await res.json();
  } catch { /* fall back to the default file names */ }
  const entry = (mood) => manifest.moods?.[mood] ?? { src: `${mood}.png` };
  return {
    moods: MOODS,
    srcFor: (mood) => `${base}${entry(MOODS.includes(mood) ? mood : "idle").src ?? `${mood}.png`}`,
    framesFor: (mood) => {
      const e = entry(mood);
      return Array.isArray(e.frames) ? { frames: e.frames.map((f) => `${base}${f}`), fps: e.fps ?? 12,
        loop: e.loop !== false } : null;
    },
  };
}
