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

function pill(label) {
  const valueEl = h("span", { class: "avatar-pill__value" }, "—");
  const el = h("span", { class: "avatar-pill" }, `${label} `, valueEl);
  return { el, valueEl };
}

function createPlaceholder(container, opts) {
  const { affectedSide } = opts || {};
  let disposed = false;
  let view = "front";
  let labelsVisible = true;
  let lastUpdateAt = 0;

  const rKnee = pill("R knee");
  const lKnee = pill("L knee");
  const hips = pill("Hips");
  const trunk = pill("Trunk lean");
  const pillsEl = h("div", { class: "avatar-placeholder__pills" }, rKnee.el, lKnee.el, hips.el, trunk.el);

  const segmentedEl = h("div", { class: "avatar-placeholder__segmented pill-tabs", role: "tablist",
    "aria-label": "View" },
    ["front", "side", "top"].map((key) => h("button", {
      type: "button", role: "tab", "aria-selected": key === view ? "true" : "false",
      onclick: () => setView(key),
    }, key[0].toUpperCase() + key.slice(1))));

  const floorEl = h("div", { class: "avatar-placeholder__floor" },
    h("p", { class: "avatar-placeholder__title" }, "3D view coming soon"),
    h("p", { class: "avatar-placeholder__sub" }, "Eric's 3D renderer plugs in here"),
    pillsEl);

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
  }

  function applyFrame(frame) {
    const joints = frame && frame.joints;
    const valid = isValidJoints(joints);
    const flagged = flagSegments((frame && frame.flags) || [], affectedSide);

    if (valid) {
      rKnee.valueEl.textContent = fmtDeg(kneeFlexion(joints, "right"));
      lKnee.valueEl.textContent = fmtDeg(kneeFlexion(joints, "left"));
      const hipDeg = affectedSide === "left" ? hipFlexion(joints, "left") : hipFlexion(joints, "right");
      hips.valueEl.textContent = fmtDeg(hipDeg);
      trunk.valueEl.textContent = fmtDeg(trunkLean(joints));
    } else {
      rKnee.valueEl.textContent = "—";
      lKnee.valueEl.textContent = "—";
      hips.valueEl.textContent = "—";
      trunk.valueEl.textContent = "—";
    }
    rKnee.el.classList.toggle("avatar-pill--alert", flagged.has("r_thigh") || flagged.has("r_shin"));
    lKnee.el.classList.toggle("avatar-pill--alert", flagged.has("l_thigh") || flagged.has("l_shin"));
    hips.el.classList.toggle("avatar-pill--alert",
      flagged.has("l_thigh") || flagged.has("r_thigh"));
    trunk.el.classList.toggle("avatar-pill--alert", flagged.has("trunk"));

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
    setFrame(frame) {
      if (disposed) return;
      const now = Date.now();
      if (now - lastUpdateAt < THROTTLE_MS) return;
      lastUpdateAt = now;
      applyFrame(frame);
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
