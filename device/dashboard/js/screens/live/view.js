// Live session view builders (Figma frame 210:820). Persistent regions, same pattern as screens/patient/view.js:
// index.js updates text/attributes on the refs it gets back rather than rebuilding.
import { h, clear } from "../../core/dom.js";
import { empty } from "../../ui/states.js";

const QUICK_CUES = ["Even out your weight", "Knees over your toes", "Take a rest", "Great work"];

export function renderHead({ onReplay, onLeave }) {
  const metaEl = h("p", { class: "meta patient__meta" });
  const titleEl = h("h1", { class: "page-title" }, "Live session");
  const subtitleEl = h("p", { class: "meta live__subtitle" });
  const liveBtn = h("button", { type: "button", role: "tab", "aria-selected": "true" }, "Live");
  const replayBtn = h("button", { type: "button", role: "tab", "aria-selected": "false",
    onclick: onReplay }, "Replay");
  const modeTabs = h("div", { class: "pill-tabs live__mode", role: "tablist", "aria-label": "Live or replay" },
    liveBtn, replayBtn);
  const leaveBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: onLeave }, "Leave session");
  const el = h("div", { class: "page-head live__head" },
    h("div", {}, metaEl, titleEl, subtitleEl),
    h("div", { class: "live__head-actions" }, modeTabs, leaveBtn));
  return { el, metaEl, titleEl, subtitleEl };
}

export function renderRoomCard() {
  const liveBadgeEl = h("span", { class: "chip live__badge" },
    h("span", { class: "live__badge-dot", "aria-hidden": "true" }), "LIVE");
  const latencyEl = h("span", { class: "meta live__latency" });
  const poseStatusEl = h("p", { class: "meta", role: "status" }, "Waiting for Mira's private pose stream…");
  const avatarHost = h("div", { class: "live__avatar-host" });
  const videoEl = h("video", { class: "live__camera", autoplay: "", playsinline: "", muted: "",
    "aria-label": "Live camera video" });
  const videoStatusEl = h("p", { class: "meta live__video-status", role: "status" }, "Connecting camera video…");
  const mediaHost = h("div", { class: "live__media-host" }, videoEl, videoStatusEl);
  const repCardEl = h("div", { class: "card live__rep-card", hidden: true },
    h("p", { class: "live__rep-big" }), h("p", { class: "meta live__rep-line" }));
  const hrChipEl = h("span", { class: "chip live__hr-chip", hidden: true });
  const pausedEl = h("div", { class: "live__paused-banner", role: "status", hidden: true }, "Paused");
  const el = h("section", { class: "card live__room", "aria-label": "Live 3D twin" },
    h("div", { class: "live__room-head" }, liveBadgeEl, latencyEl), poseStatusEl,
    h("div", { class: "live__room-body" },
      h("div", { class: "live__views" }, mediaHost, avatarHost), repCardEl, hrChipEl, pausedEl));
  const repBigEl = repCardEl.querySelector(".live__rep-big");
  const repLineEl = repCardEl.querySelector(".live__rep-line");
  return { el, avatarHost, videoEl, videoStatusEl, liveBadgeEl, latencyEl, poseStatusEl, repCardEl, repBigEl, repLineEl, hrChipEl, pausedEl };
}

// "Send a cue" panel when the device-bridge endpoint is live (caps.has("/live/say")).
export function renderCuePanel({ onSend }) {
  const textarea = h("textarea", { class: "live__cue-textarea", rows: 3,
    placeholder: "Slow down on the way up" });
  const errorEl = h("p", { class: "patient__note-error", role: "alert" });
  const sendBtn = h("button", { class: "btn btn--primary live__cue-send", type: "button", onclick: send },
    "Say it through Mira");
  const chipsEl = h("div", { class: "live__quick-cues" },
    QUICK_CUES.map((text) => h("button", { class: "chip live__quick-cue", type: "button",
      onclick: () => { textarea.value = text; send(); } }, text)));
  const el = h("section", { class: "card live__cue-panel", "aria-label": "Send a cue" },
    h("h2", { class: "card__title" }, "Send a cue"),
    h("p", { class: "meta" }, "Mira speaks it in her voice and shows it as a caption."),
    textarea, errorEl, sendBtn,
    h("p", { class: "meta live__quick-cues-label" }, "Quick cues"), chipsEl);

  async function send() {
    const text = textarea.value.trim();
    if (!text) { errorEl.textContent = "Write a cue first."; return; }
    sendBtn.setAttribute("disabled", "");
    try {
      await onSend(text);
      textarea.value = "";
      clear(errorEl);
    } catch {
      errorEl.textContent = "Couldn't send that cue. Try again.";
    } finally {
      sendBtn.removeAttribute("disabled");
    }
  }

  return { el };
}

export function renderCuePanelDisabled() {
  return h("section", { class: "card live__cue-panel", "aria-label": "Send a cue" },
    h("h2", { class: "card__title" }, "Send a cue"),
    h("p", { class: "meta" }, "Cues from the dashboard arrive with the device bridge."));
}

export function renderCueHistory() {
  const listHost = h("div", { class: "live__cue-history-list" });
  const el = h("section", { class: "card live__cue-history", "aria-label": "Said this session" },
    h("h2", { class: "card__title" }, "Said this session"), listHost);
  return { el, listHost };
}

export function renderCueHistoryList(listHost, rows) {
  if (rows.length === 0) {
    listHost.replaceChildren(h("p", { class: "meta" }, "Nothing said yet."));
    return;
  }
  listHost.replaceChildren(...rows.map((r) => h("p", { class: "live__cue-row" },
    h("span", { class: "meta live__cue-time" }, r.time), h("span", {}, `"${r.text}"`))));
}

export function emptyNoLiveSession(name, href) {
  return h("div", { class: "card" },
    empty(name ? `${name} isn't in a session right now` : "No session is live right now",
      "They'll show up here the moment a session starts."),
    h("a", { class: "btn btn--secondary", href }, "Go to the replay"));
}

export function emptyNoOneLive(href) {
  return h("div", { class: "card" },
    empty("No patient is in a live session right now", "Check back once someone starts a session."),
    h("a", { class: "btn btn--secondary", href }, "Go to caseload"));
}
