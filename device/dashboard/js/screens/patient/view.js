// Patient review view builders (Figma 206:202). Every function returns fresh elements except where noted;
// index.js owns which persistent region each lives in, and updates text/attributes on the refs it gets back
// rather than rebuilding (so the timeline's play button, an open note editor, etc. survive a data refresh).
import { h, clear } from "../../core/dom.js";
import { icon } from "../../ui/icons.js";
import { aiDraftChip } from "../../ui/aiDraftChip.js";
import { lineChart } from "../../ui/lineChart.js";
import { balanceBar } from "../../ui/balanceBar.js";
import { loading, errorState, empty } from "../../ui/states.js";
import { fmtDeg } from "../../core/format.js";
import { href } from "../../core/router.js";

// ---- Head ---------------------------------------------------------------------------------------------------

export function renderHead({ onModeChange, onPickSession, onMessage }) {
  // "Ask Mira Assistant about Maya" (browser bug 8): opens the Assistant scoped to this patient.
  const askLink = h("a", { class: "btn btn--secondary patient__ask", href: href("assistant") }, "Ask Mira Assistant");
  const metaEl = h("p", { class: "meta patient__meta" });
  const titleEl = h("h1", { class: "page-title" });
  const liveBtn = h("button", { type: "button", role: "tab", "aria-selected": "false",
    onclick: () => onModeChange("live") }, "Live");
  const replayBtn = h("button", { type: "button", role: "tab", "aria-selected": "true",
    onclick: () => onModeChange("replay") }, "Replay");
  const modeTabs = h("div", { class: "pill-tabs patient__mode", role: "tablist", "aria-label": "Live or replay" },
    liveBtn, replayBtn);
  const sessionBtn = h("button", { class: "btn btn--secondary patient__session-btn", type: "button",
    onclick: () => onPickSession(sessionBtn) }, icon("calendar"), h("span", { class: "patient__session-label" }, "—"));
  const messageBtn = h("button", { class: "btn btn--secondary", type: "button", hidden: true,
    onclick: onMessage }, "Message");
  const el = h("div", { class: "page-head patient__head" },
    h("div", {}, metaEl, titleEl),
    h("div", { class: "patient__head-actions" }, modeTabs, sessionBtn, askLink, messageBtn));
  return { el, metaEl, titleEl, liveBtn, replayBtn, sessionBtn, sessionLabelEl: sessionBtn.querySelector("span"),
    messageBtn, askLink };
}

// Session-picker popover: a plain list of buttons (not a native <select>, so each row can show date + reps).
export function renderSessionPicker(sessions, { selectedId, onPick }) {
  return h("div", { class: "card patient__session-picker", role: "menu", "aria-label": "Recent sessions" },
    sessions.length === 0
      ? h("p", { class: "meta" }, "No sessions yet")
      : sessions.map((s) => h("button", {
        type: "button", role: "menuitem", class: `patient__session-item${s.id === selectedId ? " is-active" : ""}`,
        onclick: () => onPick(s.id),
      }, h("span", {}, s.label), h("span", { class: "meta" }, s.sub))));
}

// ---- Room card ------------------------------------------------------------------------------------------------

// `interactive`: a real 3D renderer is registered, so the orbit/zoom hint is true; the placeholder can't orbit,
// so the hint is hidden then (browser bug 6).
export function renderRoomCard({ interactive = false } = {}) {
  const repPillEl = h("span", { class: "chip patient__replaying-pill", hidden: true });
  const avatarHost = h("div", { class: "patient__avatar-host" });
  const flagCalloutEl = h("div", { class: "patient__flag-callout", role: "status", hidden: true });
  const el = h("section", { class: "card patient__room", "aria-label": "3D replay" },
    h("div", { class: "patient__room-head" },
      interactive ? h("span", { class: "meta" }, "Drag to orbit · scroll to zoom") : h("span", {}),
      repPillEl),
    avatarHost,
    flagCalloutEl);
  return { el, avatarHost, repPillEl, flagCalloutEl };
}

export function setFlagCallout(flagCalloutEl, text) {
  if (!text) { flagCalloutEl.hidden = true; clear(flagCalloutEl); return; }
  flagCalloutEl.hidden = false;
  flagCalloutEl.replaceChildren(
    h("span", { class: "patient__flag-icon", "aria-hidden": "true" }, "⚠"),
    h("span", {}, text));
}

// ---- This session ---------------------------------------------------------------------------------------------

export function renderSessionCard(m) {
  const good = m.good != null && m.total != null ? `${m.good}` : "—";
  const of = m.total != null ? `of ${m.total} good reps` : "good reps";
  return h("section", { class: "card patient__this-session", "aria-label": "This session" },
    h("h2", { class: "card__title" }, "This session"),
    h("p", { class: "patient__stat-big" }, good),
    h("p", { class: "meta" }, of),
    h("div", { class: "patient__stat-row" },
      h("span", {}, "Average depth"), h("strong", {}, fmtDeg(m.avgDepth))),
    h("p", { class: "meta patient__stat-label" }, "Time-weighted load"),
    // The shared bar: widths from the data, red only when flagged (final review B I2, browser bug 4).
    m.load.affected != null ? balanceBar(m.load) : h("p", { class: "meta" }, "No load data for this session."),
    h("div", { class: "patient__stat-row" },
      h("span", {}, "Flags"),
      h("strong", { class: m.flags.count ? "patient__flag-text" : "" },
        m.flags.count ? h("span", { "aria-hidden": "true" }, "⚠ ") : null, m.flags.label)),
    h("div", { class: "patient__stat-row" },
      h("span", {}, "Pain"), h("strong", {}, m.pain != null ? `${m.pain} / 10` : "—")),
    h("div", { class: "patient__stat-row" },
      h("span", {}, "Effort"), h("strong", {}, m.effort != null ? `${m.effort} / 10` : "—")));
}

// ---- Timeline -------------------------------------------------------------------------------------------------

export function renderTimelineCard({ onPlayPause, onRepClick }) {
  const playBtn = h("button", { class: "patient__play", type: "button", "aria-label": "Play", onclick: onPlayPause },
    icon("play", { label: "" }));
  const timeEl = h("span", { class: "patient__time" }, "0:00 / 0:00");
  const repsHost = h("div", { class: "patient__reps", role: "list", "aria-label": "Reps" });
  const el = h("section", { class: "card patient__timeline", "aria-label": "Rep timeline" },
    h("div", { class: "patient__timeline-controls" }, playBtn, timeEl),
    repsHost,
    h("div", { class: "patient__rep-legend meta" },
      h("span", { class: "patient__legend-swatch patient__legend-swatch--good" }), " Good rep ",
      h("span", { class: "patient__legend-swatch patient__legend-swatch--flag" }), " ⚠ Flagged rep ",
      "· Click a rep to jump to it"));
  function repBlock(r, isCurrent) {
    return h("button", {
      type: "button", role: "listitem", "data-rep": String(r.rep),
      class: `patient__rep${r.flagged ? " patient__rep--flagged" : " patient__rep--good"}${isCurrent ? " patient__rep--current" : ""}`,
      "aria-current": isCurrent ? "true" : null, "aria-label": r.label,
      onclick: () => onRepClick(r.rep),
    }, r.flagged ? h("span", { class: "patient__rep-warn", "aria-hidden": "true" }, "⚠") : null, String(r.rep));
  }
  return { el, playBtn, timeEl, repsHost, repBlock };
}

// Builds the rep buttons once per session (call again only when the reps change); every frame after that goes
// through setCurrentRep, which only moves a class and aria-current, so a focused rep button keeps its focus and
// nothing is rebuilt at frame rate (final review B, I4).
export function renderReps(repsHost, repBlock, reps, currentRep) {
  repsHost.replaceChildren(...reps.map((r) => repBlock(r, r.rep === currentRep)));
}

export function setCurrentRep(repsHost, currentRep) {
  for (const btn of repsHost.querySelectorAll("button")) {
    const isCurrent = btn.getAttribute("data-rep") === String(currentRep);
    btn.classList.toggle("patient__rep--current", isCurrent);
    if (isCurrent) btn.setAttribute("aria-current", "true"); else btn.removeAttribute("aria-current");
  }
}

// ---- 3-week trend ---------------------------------------------------------------------------------------------

export function renderTrendCard(m) {
  const chartHost = h("div", { class: "patient__trend-chart" });
  if (m.points.depth.length > 0 || m.points.load.length > 0) {
    const series = [];
    if (m.points.depth.length) {
      series.push({ name: "Depth", style: "solid", points: m.points.depth.map(([x, y]) => ({ x, y })) });
    }
    if (m.points.load.length) {
      series.push({ name: "Affected-side load", style: "dashed", points: m.points.load.map(([x, y]) => ({ x, y })) });
    }
    chartHost.append(lineChart({ title: "3-week trend", series }, { width: 420, height: 230 }));
  } else {
    chartHost.append(h("p", { class: "meta" }, "Not enough data yet."));
  }
  return h("section", { class: "card patient__trend", "aria-label": "3-week trend" },
    h("h2", { class: "card__title" }, "3-week trend"),
    chartHost,
    h("div", { class: "patient__trend-captions" },
      m.caption ? h("p", {}, h("strong", {}, m.caption)) : null,
      m.loadCaption ? h("p", {}, h("strong", {}, m.loadCaption)) : null),
    m.insight ? h("div", { class: "patient__insight", role: "status" },
      h("span", { "aria-hidden": "true" }, "⚠ "), m.insight) : null);
}

// ---- Therapist Memory -----------------------------------------------------------------------------------------

export function renderMemoryCard(m) {
  return h("section", { class: "card patient__memory", "aria-label": "Therapist Memory" },
    h("h2", { class: "card__title" }, "Therapist Memory"),
    h("p", { class: "meta" }, m.subtitle),
    m.items.length === 0
      ? h("p", { class: "meta" }, "Nothing new.")
      : h("ul", { class: "patient__memory-list" },
        m.items.map((it) => h("li", {}, it.text, " ", it.aiDraft ? aiDraftChip() : null))));
}

// ---- Progress note ---------------------------------------------------------------------------------------------

// The chip follows the model's label: "AI draft — review before use" for the AI's draft, "From time-lapse —
// review before approving" for the therapist's own carried-over text (not AI), "Approved by you" once approved.
function noteChip(m) {
  if (m.source === "approved") return null;
  if (m.source === "therapist") return h("span", { class: "chip chip--source" }, m.label);
  if (!m.text && !m.canApprove) return aiDraftChip();
  return aiDraftChip(m.label);
}

export function renderNoteCard(m, { onApprove }) {
  const bodyHost = h("div", { class: "patient__note-body" });
  const chipHost = h("div", { class: "patient__note-chip" }, noteChip(m));
  const actionsHost = h("div", { class: "patient__note-actions" });

  function renderReadonly() {
    const approvedLine = m.source === "approved"
      ? h("p", { class: "meta" }, m.approvedAt ? `Approved by you · ${fmtApproved(m.approvedAt)}` : "Approved by you")
      : null;
    bodyHost.replaceChildren(...[approvedLine, h("p", { class: "patient__note-text" }, m.text || "No note yet.")]
      .filter(Boolean));
    const actions = [];
    if (m.canApprove) {
      actions.push(h("button", { class: "btn btn--secondary", type: "button", onclick: startEdit }, "Edit"));
      actions.push(h("button", { class: "btn btn--primary", type: "button",
        onclick: () => approve(m.text) }, "Approve"));
    }
    actionsHost.replaceChildren(...actions);
  }

  function startEdit() {
    const textarea = h("textarea", { class: "patient__note-textarea", rows: 6, "aria-label": "Progress note" });
    textarea.value = m.text;
    const errorEl = h("p", { class: "patient__note-error", role: "alert" });
    bodyHost.replaceChildren(textarea, errorEl);
    actionsHost.replaceChildren(
      h("button", { class: "btn btn--ghost", type: "button", onclick: renderReadonly }, "Cancel"),
      h("button", { class: "btn btn--primary", type: "button",
        onclick: () => approve(textarea.value, errorEl) }, "Approve"));
    textarea.focus?.();
  }

  async function approve(text, errorEl) {
    const value = String(text || "").trim();
    if (!value) { if (errorEl) errorEl.textContent = "Write something before approving."; return; }
    for (const b of actionsHost.querySelectorAll("button")) b.setAttribute("disabled", "");
    try {
      await onApprove(value);
    } catch {
      for (const b of actionsHost.querySelectorAll("button")) b.removeAttribute("disabled");
      if (errorEl) errorEl.textContent = "Couldn't save the note. Try again.";
      else {
        const err = h("p", { class: "patient__note-error", role: "alert" }, "Couldn't save the note. Try again.");
        bodyHost.append(err);
      }
    }
  }

  renderReadonly();
  const el = h("section", { class: "card patient__note", "aria-label": "Progress note" },
    h("div", { class: "patient__note-head" },
      h("h2", { class: "card__title" }, "Progress note"), chipHost),
    bodyHost,
    actionsHost);
  return { el, showApproved(name, when) {
    chipHost.replaceChildren();
    actionsHost.replaceChildren();
    bodyHost.replaceChildren(
      h("p", { class: "meta" }, when ? `Approved by you · ${when}` : "Approved by you"),
      h("p", { class: "patient__note-text" }, name));
  } };
}

function fmtApproved(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    .format(d);
}

// ---- Shared loading/empty helpers reused by index.js ------------------------------------------------------------

export function cardLoading(title) {
  return h("section", { class: "card" }, h("h2", { class: "card__title" }, title), loading());
}

export function cardError(title, err, retry) {
  return h("section", { class: "card" }, h("h2", { class: "card__title" }, title), errorState(err, retry));
}

export function noSessions() {
  return h("div", { class: "card" },
    empty("No sessions yet", "This patient hasn't run a session yet."));
}
