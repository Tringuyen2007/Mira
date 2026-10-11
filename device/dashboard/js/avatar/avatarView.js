// Stable plug-in interface for the 3D avatar render (Eric builds the real renderer separately; this file is
// also today's shipped implementation: a calm placeholder card with live angle pills and the balance bar, no
// canvas/WebGL). See README.md in this folder for the coordinate contract Eric's module must honour.
//
//   createAvatarView(container, { mode, affectedSide, ghost }) -> { setFrame, setView, setLabelsVisible, dispose }
//   registerAvatarRenderer(factory)  // factory has createAvatarView's signature; called once by Eric's module.
//
// If a renderer has been registered, createAvatarView delegates every call to it; otherwise it builds the
// placeholder below. Either way the returned object has the same four methods, so screens/patient never know
// which one they got.
import { h } from "../core/dom.js";
import { kneeFlexion, hipFlexion, trunkLean, flagSegments, isValidJoints } from "../three/joints.js";
import { balanceBar } from "../ui/balanceBar.js";

const THROTTLE_MS = 100; // ~10 Hz, per the live-mode DOM-update budget

let rendererFactory = null;

export function registerAvatarRenderer(factory) {
  rendererFactory = factory;
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
  const { affectedSide, ghost } = opts || {};
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
    h("p", { class: "avatar-placeholder__sub" }, "Eric's 3D renderer plugs in here"),
    pillsEl, ghostRow ? ghostRow.el : null);

  const balanceHost = h("div", { class: "avatar-placeholder__balance" });

  const el = h("div", { class: "avatar-placeholder" }, segmentedEl, floorEl, balanceHost);
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
    const loadL = Number(frame && frame.load_l_pct);
    const affected = affectedSide === "left" ? loadL : 100 - loadL;
    const other = 100 - affected;
    balanceHost.replaceChildren(balanceBar({
      affected: Number.isFinite(loadL) ? affected : null,
      other: Number.isFinite(loadL) ? other : null,
      affectedSide,
      flagged: flagged.has(`${affectedSide === "left" ? "l" : "r"}_thigh`),
    }));
  }

  return {
    setFrame(frame, ghostFrame) {
      if (disposed) return;
      const now = Date.now();
      if (now - lastUpdateAt < THROTTLE_MS) return;
      lastUpdateAt = now;
      applyFrame(frame);
      if (ghostRow && ghostFrame) applyPills(ghostRow, ghostFrame);
    },
    setView,
    setLabelsVisible,
    dispose() {
      if (disposed) return;
      disposed = true;
      el.remove();
    },
  };
}

export function createAvatarView(container, opts) {
  if (rendererFactory) return rendererFactory(container, opts);
  return createPlaceholder(container, opts);
}
