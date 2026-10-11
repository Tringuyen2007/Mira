import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  kneeFlexion, hipFlexion, trunkLean, flagSegments, lerpJoints, isValidJoints, JOINT_COUNT, BONES,
} from "../js/three/joints.js";

const pose = JSON.parse(readFileSync(new URL("../../../server/samples/ws_pose.json", import.meta.url), "utf8"));
// ws_pose.json is a mid-squat live frame; rest_get_session_frames_full.json's first frame is the session's
// standing start (its own knee_l/knee_r are ~6.4 degrees), so it's the fixture for the "standing" assertion.
const standing = JSON.parse(
  readFileSync(new URL("../../../server/samples/rest_get_session_frames_full.json", import.meta.url), "utf8"));

test("isValidJoints: the sample pose is valid", () => {
  assert.equal(isValidJoints(pose.joints), true);
});

test("isValidJoints: rejects 32 joints (missing one) or NaN", () => {
  assert.equal(isValidJoints(pose.joints.slice(0, 32)), false);
  const bad = pose.joints.map((p) => p.slice());
  bad[5][1] = NaN;
  assert.equal(isValidJoints(bad), false);
  assert.equal(isValidJoints(null), false);
  assert.equal(isValidJoints("nope"), false);
});

test("kneeFlexion: a standing pose reads under 25 degrees", () => {
  const joints = standing.frames[0].joints;
  assert.ok(kneeFlexion(joints, "left") < 25);
  assert.ok(kneeFlexion(joints, "right") < 25);
});

test("kneeFlexion: a synthetic 90 degree bend reads 90 +/- 0.5", () => {
  const j = new Array(JOINT_COUNT).fill(0).map(() => [0, 0, 0]);
  j[24] = [0, 1, 0]; // R_HIP
  j[26] = [0, 0.5, 0]; // R_KNEE
  j[28] = [0, 0.5, 0.5]; // R_ANKLE
  const deg = kneeFlexion(j, "right");
  assert.ok(Math.abs(deg - 90) <= 0.5, `expected ~90, got ${deg}`);
});

test("hipFlexion and trunkLean run on the sample pose and return finite numbers", () => {
  assert.ok(Number.isFinite(hipFlexion(pose.joints, "left")));
  assert.ok(Number.isFinite(hipFlexion(pose.joints, "right")));
  assert.ok(Number.isFinite(trunkLean(pose.joints)));
});

test("flagSegments: knee_cave_r flags the right thigh and shin only", () => {
  const set = flagSegments(["knee_cave_r"], "right");
  assert.deepEqual([...set].sort(), ["r_shin", "r_thigh"]);
});

test("flagSegments: weight_shift flags the affected side's thigh, shin and foot", () => {
  const right = flagSegments(["weight_shift"], "right");
  assert.deepEqual([...right].sort(), ["r_foot", "r_shin", "r_thigh"]);
  const left = flagSegments(["weight_shift"], "left");
  assert.deepEqual([...left].sort(), ["l_foot", "l_shin", "l_thigh"]);
});

test("flagSegments: trunk_lean flags the trunk; no flags is empty", () => {
  assert.deepEqual([...flagSegments(["trunk_lean"], "right")], ["trunk"]);
  assert.deepEqual([...flagSegments([], "right")], []);
  assert.deepEqual([...flagSegments(undefined, "right")], []);
});

test("lerpJoints: midpoint of two frames", () => {
  const a = new Array(JOINT_COUNT).fill(0).map(() => [0, 0, 0]);
  const b = new Array(JOINT_COUNT).fill(0).map(() => [2, 4, 6]);
  const mid = lerpJoints(a, b, 0.5);
  assert.deepEqual(mid[0], [1, 2, 3]);
  assert.deepEqual(mid[10], [1, 2, 3]);
});

test("BONES covers every named segment", () => {
  const segments = new Set(BONES.map((b) => b[2]));
  for (const seg of ["trunk", "l_arm", "r_arm", "l_thigh", "r_thigh", "l_shin", "r_shin", "l_foot", "r_foot"]) {
    assert.ok(segments.has(seg), `missing segment ${seg}`);
  }
});
