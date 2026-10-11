// Boots the LCD page: fits the 1024×600 stage to the screen, connects /ws/live and /ws/events with the
// device token, and redraws from state.js on every message (and on a timer for time-based states).

import { readToken, wsUrl } from "./config.js";
import { ReconnectingSocket } from "./socket.js";
import { initialState, reduce, view } from "./state.js";
import { render } from "./view.js";
import { AudioPlayer } from "./audio.js";
import { loadMascot } from "./mascot.js";

const VERSION = "1";
const HEALTH_EVERY_MS = 5000;
const AUDIO_EVENTS = new Set(["greeting", "cue", "summary_ready"]);

// Uniform scale with letterboxing: the stage is always 1024×600 CSS px inside.
function fitStage() {
  const scale = Math.min(window.innerWidth / 1024, window.innerHeight / 600);
  document.documentElement.style.setProperty("--stage-scale", String(scale));
}

async function boot() {
  fitStage();
  window.addEventListener("resize", fitStage);
  const mascot = await loadMascot();
  let state = initialState();
  const draw = () => render(document, view(state, Date.now()), mascot);
  const dispatch = (input) => { state = reduce(state, input, Date.now()); draw(); };

  const token = readToken(window.location, window.localStorage, window.history);
  if (!token) {
    dispatch({ channel: "socket", msg: { status: "rejected" } });
    return;
  }
  const audio = new AudioPlayer();
  const status = (s) => dispatch({ channel: "socket", msg: { status: s } });
  const live = new ReconnectingSocket(wsUrl(window.location, "/ws/live", token), {
    onMessage: (msg) => dispatch({ channel: "live", msg }),
  });
  const health = () => events.send({ type: "device_health", app: "lcd", t: Date.now() / 1000,
    screen: `${window.innerWidth}x${window.innerHeight}`, version: VERSION });
  const events = new ReconnectingSocket(wsUrl(window.location, "/ws/events", token), {
    onStatus: (s) => { status(s); if (s === "open") health(); },
    onMessage: (msg) => {
      dispatch({ channel: "events", msg });
      if (AUDIO_EVENTS.has(msg.type)) audio.play(msg.audio_url);
    },
  });
  live.start();
  events.start();
  setInterval(health, HEALTH_EVERY_MS);
  setInterval(draw, 250); // time-based states (cheer, cue window, offline grace) expire without new messages
  draw();
}

boot();
