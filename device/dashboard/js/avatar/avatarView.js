// Stable plug-in interface for the 3D avatar render (Eric builds the real renderer separately; this file is
// also today's shipped implementation: a calm placeholder card with live angle pills and the balance bar, no
// canvas/WebGL). See README.md in this folder for the coordinate contract Eric's module must honour.
//
//   createAvatarView(container, { mode, affectedSide, ghost, viewControls }) -> { setFrame, setView, setLabelsVisible, dispose }
//   registerAvatarRenderer(factory)  // factory has createAvatarView's signature; called once by Eric's module.
//
// If a renderer has been registered, createAvatarView delegates every call to it; otherwise it builds the
// placeholder below. Either way the returned object has the same four methods, so screens/patient never know
// which one they got.
import { h } from "../core/dom.js";
import { kneeFlexion, hipFlexion, trunkLean, flagSegments, isValidJoints } from "../three/joints.js";
import { balanceBar } from "../ui/balanceBar.js";
import { loadSplit } from "../core/format.js";

const THROTTLE_MS = 100; // ~10 Hz, per the live-mode DOM-update budget

let rendererFactory = null;

export function registerAvatarRenderer(factory) {
  rendererFactory = factory;
}

// True once a real renderer has registered (screens show orbit/zoom hints only then).
export function hasRegisteredRenderer() {
  return rendererFactory !== null;
}

function fmtDeg(n) {
  return Number.isFinite(n) ? `${Math.round(n)}°` : "—";
}

function pill(label, key) {
  const valueEl = h("span", { class: "avatar-pill__value" }, "—");
  const el = h("span", { class: `avatar-pill avatar-pill--${key}` }, `${label} `, valueEl);
  return { el, valueEl };
}

function pillRow(extraClass) {
  const rKnee = pill("R knee", "rknee");
  const lKnee = pill("L knee", "lknee");
  const hips = pill("Hips", "hips");
  const trunk = pill("Trunk lean", "trunk");
  const el = h("div", { class: `avatar-placeholder__pills${extraClass ? ` ${extraClass}` : ""}` },
    rKnee.el, lKnee.el, hips.el, trunk.el);
  return { el, rKnee, lKnee, hips, trunk };
}

function createPlaceholder(container, opts) {
  // viewControls: false when the screen has its own single View control (the time-lapse), so there's no
  // duplicate Front/Side/Top toggle per card.
  const { affectedSide, ghost, viewControls = true } = opts || {};
  let disposed = false;
  let view = "front";
  let labelsVisible = true;
  let lastUpdateAt = 0;

  const { el: pillsEl, rKnee, lKnee, hips, trunk } = pillRow();
  // Overlay mode (decisions: "one view with { ghost: true }"): a second, muted pill row for the earlier date's
  // frame, built only when the screen asked for it — setFrame's optional second argument is otherwise ignored.
  const ghostRow = ghost ? pillRow("avatar-placeholder__pills--ghost") : null;

  const segmentedEl = h("div", { class: "avatar-placeholder__segmented pill-tabs", role: "tablist",
    "aria-label": "View" },
    ["front", "side", "top"].map((key) => h("button", {
      type: "button", role: "tab", "aria-selected": key === view ? "true" : "false",
      onclick: () => setView(key),
    }, key[0].toUpperCase() + key.slice(1))));

  const floorEl = h("div", { class: "avatar-placeholder__floor" },
    h("p", { class: "avatar-placeholder__title" }, "3D view coming soon"),
    h("p", { class: "avatar-placeholder__sub" }, "Live angles and balance are shown below"),
    pillsEl, ghostRow ? ghostRow.el : null);

  const balanceHost = h("div", { class: "avatar-placeholder__balance" });

  const el = h("div", { class: "avatar-placeholder" }, viewControls ? segmentedEl : null, floorEl, balanceHost);
  container.append(el);

  function setView(next) {
    if (!["front", "side", "top"].includes(next)) return;
    view = next;
    for (const btn of segmentedEl.querySelectorAll("button")) {
      btn.setAttribute("aria-selected", btn.textContent.toLowerCase() === view ? "true" : "false");
    }
  }

  function setLabelsVisible(visible) {
    labelsVisible = Boolean(visible);
    pillsEl.hidden = !labelsVisible;
    if (ghostRow) ghostRow.el.hidden = !labelsVisible;
  }

  function applyPills(pills, frame) {
    const joints = frame && frame.joints;
    const valid = isValidJoints(joints);
    const flagged = flagSegments((frame && frame.flags) || [], affectedSide);

    if (valid) {
      pills.rKnee.valueEl.textContent = fmtDeg(kneeFlexion(joints, "right"));
      pills.lKnee.valueEl.textContent = fmtDeg(kneeFlexion(joints, "left"));
      const hipDeg = affectedSide === "left" ? hipFlexion(joints, "left") : hipFlexion(joints, "right");
      pills.hips.valueEl.textContent = fmtDeg(hipDeg);
      pills.trunk.valueEl.textContent = fmtDeg(trunkLean(joints));
    } else {
      pills.rKnee.valueEl.textContent = "—";
      pills.lKnee.valueEl.textContent = "—";
      pills.hips.valueEl.textContent = "—";
      pills.trunk.valueEl.textContent = "—";
    }
    pills.rKnee.el.classList.toggle("avatar-pill--alert", flagged.has("r_thigh") || flagged.has("r_shin"));
    pills.lKnee.el.classList.toggle("avatar-pill--alert", flagged.has("l_thigh") || flagged.has("l_shin"));
    pills.hips.el.classList.toggle("avatar-pill--alert",
      flagged.has("l_thigh") || flagged.has("r_thigh"));
    pills.trunk.el.classList.toggle("avatar-pill--alert", flagged.has("trunk"));
    return flagged;
  }

  function applyFrame(frame) {
    const flagged = applyPills({ rKnee, lKnee, hips, trunk }, frame);
    // One value, two labels that add to 100 (browser bug 11).
    const split = loadSplit(frame && frame.load_l_pct);
    const affected = split ? (affectedSide === "left" ? split.left : split.right) : null;
    balanceHost.replaceChildren(balanceBar({
      affected,
      other: split ? 100 - affected : null,
      affectedSide,
      flagged: flagged.has(`${affectedSide === "left" ? "l" : "r"}_thigh`),
    }));
  }

  let pending = null;
  let trailingTimer = null;
  function flush() {
    if (disposed || !pending) return;
    const [frame, ghostFrame] = pending;
    pending = null;
    lastUpdateAt = Date.now();
    applyFrame(frame);
    if (ghostRow && ghostFrame) applyPills(ghostRow, ghostFrame);
  }

  return {
    // Throttled to ~10 Hz, with a trailing update so the last frame before a pause/seek always lands
    // (final review B, Minor 3).
    setFrame(frame, ghostFrame) {
      if (disposed) return;
      pending = [frame, ghostFrame];
      const wait = THROTTLE_MS - (Date.now() - lastUpdateAt);
      if (wait <= 0) { flush(); return; }
      if (!trailingTimer) trailingTimer = setTimeout(() => { trailingTimer = null; flush(); }, wait);
    },
    setView,
    setLabelsVisible,
    dispose() {
      if (disposed) return;
      disposed = true;
      clearTimeout(trailingTimer);
      trailingTimer = null;
      el.remove();
    },
  };
}

export function createAvatarView(container, opts) {
  if (rendererFactory) return rendererFactory(container, opts);
  return createPlaceholder(container, opts);
}
