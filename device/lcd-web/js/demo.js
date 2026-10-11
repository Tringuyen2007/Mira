// Draws the LCD stage (index.html) in one demo state at a time, through the same pure reducer and renderer
// as the real kiosk (state.js, view.js), with no server, sockets or token. Pick a screen with the buttons,
// the ←/→ keys, ?screen=<name> in the URL, or Play to cycle through them. Edit the copy in SCREENS below.

import { initialState, reduce, view } from "./state.js";
import { render } from "./view.js";
import { loadMascot } from "./mascot.js";

const NAV_PX = 56; // the switcher bar under the stage
const PLAY_EVERY_MS = 4000;

const base = { phase: "idle", rep: 0, reps_target: 12, set: 2, sets_target: 3, countdown: null, load_l_pct: null,
  hr: 96, paused: false, caption: null, first_name: "Maya", exercise_name: "Mini squats", incident: false };
const live = (over) => ({ channel: "live", msg: { ...base, ...over } });
const ev = (msg) => ({ channel: "events", msg });
const cap = (text) => ({ text });
const open = { channel: "socket", msg: { status: "open" } };

// Copy and numbers as in the Figma frames (same as the kiosk's dev preview).
const SCREENS = [
  { name: "greeting", label: "Greeting", inputs: [open, ev({ type: "arrived" }), live({ set: 1, hr: 88,
    caption: cap("Good morning, Maya. It's mini squats today, and I'll count every one with you.") })] },
  { name: "gesture", label: "Ready?", inputs: [open, ev({ type: "awaiting_gesture" }), live({ hr: null,
    caption: cap("Give me a thumbs up or raise your hand whenever you're ready.") })] },
  { name: "countdown", label: "Countdown", inputs: [open, live({ phase: "countdown", countdown: 3,
    caption: cap("Get ready. Three, two, one.") })] },
  { name: "cue", label: "Form cue", inputs: [open, live({ phase: "active", rep: 4, load_l_pct: 68, hr: 96,
    caption: cap("Put a little more weight on your right leg. Keep both feet flat.") })] },
  { name: "rep", label: "Rep", inputs: [open, live({ phase: "active", rep: 7, load_l_pct: 48, hr: 98,
    caption: cap("That's 7! Nice and even, keep it up.") }), ev({ type: "rep", quality: "good" })] },
  { name: "paused", label: "Paused", inputs: [open, live({ phase: "active", paused: true, rep: 7, hr: 84,
    caption: cap("Paused. Take your time, I'll wait right here.") })] },
  { name: "setDone", label: "Set done", inputs: [open, live({ phase: "rest", rep: 12, load_l_pct: 52, hr: 92,
    caption: cap("Set 2 done. Twelve steady reps. Rest for 60 seconds, you earned it.") })] },
  { name: "fall", label: "Fall alert", inputs: [open, live({ phase: "active", incident: true, rep: 7 })] },
  { name: "idle", label: "Idle", inputs: [open, live({ caption: null })] },
];

function fitStage() {
  const scale = Math.min(window.innerWidth / 1024, (window.innerHeight - NAV_PX) / 600);
  document.documentElement.style.setProperty("--stage-scale", String(scale));
}

async function main() {
  fitStage();
  window.addEventListener("resize", fitStage);
  const mascot = await loadMascot();
  const nav = document.getElementById("screen-nav");
  const buttons = new Map();
  let index = Math.max(0, SCREENS.findIndex((s) => s.name === new URLSearchParams(window.location.search).get("screen")));
  let timer = null;

  const show = (i) => {
    index = (i + SCREENS.length) % SCREENS.length;
    const screen = SCREENS[index];
    const now = Date.now();
    let state = initialState();
    for (const input of screen.inputs) state = reduce(state, input, now);
    render(document, view(state, now), mascot);
    for (const [name, button] of buttons) button.setAttribute("aria-pressed", String(name === screen.name));
    const url = new URL(window.location.href);
    url.searchParams.set("screen", screen.name);
    window.history.replaceState(null, "", url);
  };

  for (const [i, screen] of SCREENS.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = screen.label;
    button.addEventListener("click", () => { stop(); show(i); });
    buttons.set(screen.name, button);
    nav.append(button);
  }
  const play = document.createElement("button");
  play.type = "button";
  play.className = "play";
  play.textContent = "Play";
  const stop = () => { clearInterval(timer); timer = null; play.textContent = "Play"; };
  play.addEventListener("click", () => {
    if (timer) return stop();
    play.textContent = "Stop";
    timer = setInterval(() => show(index + 1), PLAY_EVERY_MS);
  });
  nav.append(play);

  window.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { stop(); show(index + 1); }
    if (e.key === "ArrowLeft") { stop(); show(index - 1); }
  });
  show(index);
}

main();
