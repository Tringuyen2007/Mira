import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();

const { createAvatarView, registerAvatarRenderer } = await import("../js/avatar/avatarView.js");
const { kneeFlexion, hipFlexion, trunkLean } = await import("../js/three/joints.js");

const pose = JSON.parse(readFileSync(new URL("../../../server/samples/ws_pose.json", import.meta.url), "utf8"));

function pillText(container, label) {
  const pills = container.querySelectorAll(".avatar-pill");
  const el = pills.find((p) => p.textContent.startsWith(label));
  return el ? el.textContent : null;
}

function pillEl(container, label) {
  return container.querySelectorAll(".avatar-pill").find((p) => p.textContent.startsWith(label));
}

test("placeholder renders angle pills from a ws_pose.json frame with correct values", () => {
  const container = document.createElement("div");
  const view = createAvatarView(container, { mode: "replay", affectedSide: "right" });
  view.setFrame({ joints: pose.joints, load_l_pct: pose.load_l_pct, flags: [], t: pose.t });

  const expectRKnee = `R knee ${Math.round(kneeFlexion(pose.joints, "right"))}°`;
  const expectLKnee = `L knee ${Math.round(kneeFlexion(pose.joints, "left"))}°`;
  const expectHips = `Hips ${Math.round(hipFlexion(pose.joints, "right"))}°`;
  const expectTrunk = `Trunk lean ${Math.round(trunkLean(pose.joints))}°`;

  assert.equal(pillText(container, "R knee"), expectRKnee);
  assert.equal(pillText(container, "L knee"), expectLKnee);
  assert.equal(pillText(container, "Hips"), expectHips);
  assert.equal(pillText(container, "Trunk lean"), expectTrunk);

  assert.match(container.textContent, /3D view coming soon/);
  assert.match(container.textContent, /Eric's 3D renderer plugs in here/);
  view.dispose();
});

test("placeholder: a flagged segment's pill gets the alert class", () => {
  const container = document.createElement("div");
  const view = createAvatarView(container, { mode: "replay", affectedSide: "right" });
  view.setFrame({ joints: pose.joints, load_l_pct: pose.load_l_pct, flags: ["knee_cave_r"], t: pose.t });

  assert.equal(pillEl(container, "R knee").classList.contains("avatar-pill--alert"), true);
  assert.equal(pillEl(container, "L knee").classList.contains("avatar-pill--alert"), false);
  view.dispose();
});

test("registerAvatarRenderer: createAvatarView delegates to a registered renderer", () => {
  let called = null;
  registerAvatarRenderer((container, opts) => {
    called = { container, opts };
    return { setFrame() {}, setView() {}, setLabelsVisible() {}, dispose() {} };
  });
  try {
    const container = document.createElement("div");
    const view = createAvatarView(container, { mode: "live", affectedSide: "left" });
    assert.ok(called);
    assert.equal(called.container, container);
    assert.equal(called.opts.mode, "live");
    assert.equal(typeof view.setFrame, "function");
  } finally {
    registerAvatarRenderer(null);
  }
});

test("dispose removes the placeholder's DOM from the container", () => {
  const container = document.createElement("div");
  const view = createAvatarView(container, { mode: "replay", affectedSide: "right" });
  assert.equal(container.children.length, 1);
  view.dispose();
  assert.equal(container.children.length, 0);
});
