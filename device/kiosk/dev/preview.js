// Dev only (not loaded by index.html): draws the real stage markup from ../index.html in one fixture state,
// so the page can be compared with the Figma frames without a server, sockets or a device token.

import { initialState, reduce, view } from "../js/state.js";
import { render } from "../js/view.js";
import { loadMascot } from "../js/mascot.js";

const base = { phase: "idle", rep: 0, reps_target: 12, set: 2, sets_target: 3, countdown: null, load_l_pct: null,
  hr: 96, paused: false, caption: null, first_name: "Maya", exercise_name: "Mini squats", incident: false };
const live = (over) => ({ channel: "live", msg: { ...base, ...over } });
const ev = (msg) => ({ channel: "events", msg });
const cap = (text) => ({ text });
const open = { channel: "socket", msg: { status: "open" } };

// Copy and numbers as in the Figma frames.
const FIXTURES = {
  greeting: [open, ev({ type: "arrived" }), live({ set: 1, hr: 88,
    caption: cap("Good morning, Maya. It's mini squats today, and I'll count every one with you.") })],
  gesture: [open, ev({ type: "awaiting_gesture" }), live({ hr: null,
    caption: cap("Give me a thumbs up or raise your hand whenever you're ready.") })],
  countdown: [open, live({ phase: "countdown", countdown: 3, caption: cap("Get ready. Three, two, one.") })],
  cue: [open, live({ phase: "active", rep: 4, load_l_pct: 68, hr: 96,
    caption: cap("Put a little more weight on your right leg. Keep both feet flat.") })],
  rep: [open, live({ phase: "active", rep: 7, load_l_pct: 48, hr: 98,
    caption: cap("That's 7! Nice and even, keep it up.") }), ev({ type: "rep", quality: "good" })],
  // Rep screen while a `say` clip plays: Mira bobs and the caption's voice bars move.
  speaking: [open, live({ phase: "active", rep: 3, load_l_pct: 49, hr: 97, caption: cap("Three!") }),
    { channel: "speaking", msg: { on: true } }],
  paused: [open, live({ phase: "active", paused: true, rep: 7, hr: 84,
    caption: cap("Paused. Take your time, I'll wait right here.") })],
  setDone: [open, live({ phase: "rest", rep: 12, load_l_pct: 52, hr: 92,
    caption: cap("Set 2 done. Twelve steady reps. Rest for 60 seconds, you earned it.") })],
  fall: [open, live({ phase: "active", incident: true, rep: 7 })],
  idle: [open, live({ caption: null })],
  unpaired: [{ channel: "socket", msg: { status: "rejected" } }],
  offline: [open, ev({ type: "arrived" }), live({ set: 1, hr: 88, caption: cap("Good morning, Maya.") }),
    { channel: "socket", msg: { status: "closed" } }],
};

async function main() {
  const res = await fetch("../index.html");
  const doc = new DOMParser().parseFromString(await res.text(), "text/html");
  const stage = document.importNode(doc.getElementById("stage"), true);
  for (const el of stage.querySelectorAll("img[src]")) el.setAttribute("src", `../${el.getAttribute("src")}`);
  document.body.append(stage);

  // ?actual draws the stage at exactly 1024×600 CSS px (for side-by-side checks against Figma).
  const actual = new URLSearchParams(window.location.search).has("actual");
  const scale = () => document.documentElement.style.setProperty("--stage-scale",
    actual ? "1" : String(Math.min(window.innerWidth / 1024, window.innerHeight / 600)));
  scale();
  window.addEventListener("resize", scale);

  const name = new URLSearchParams(window.location.search).get("screen") || "greeting";
  const now = 1_000_000;
  let state = initialState();
  for (const input of FIXTURES[name] ?? FIXTURES.greeting) state = reduce(state, input, now);
  const at = name === "offline" ? now + 10_000 : now;
  render(document, view(state, at), await loadMascot(globalThis.fetch, "../assets/mira/"));
}

main();
