// Pure joint math shared by the avatar placeholder and (later) Eric's 3D renderer: MediaPipe 33-landmark
// indices, bone list, joint-angle math matching server/engine_bridge.py (README "Frame contract: joint angles"),
// and small helpers (lerp, validation). No DOM, no Three.js: this module is plain arithmetic so it can be
// unit-tested without a browser and reused by both the placeholder and the real renderer.

export const NOSE = 0;
export const L_SHOULDER = 11;
export const R_SHOULDER = 12;
export const L_ELBOW = 13;
export const R_ELBOW = 14;
export const L_WRIST = 15;
export const R_WRIST = 16;
export const L_HIP = 23;
export const R_HIP = 24;
export const L_KNEE = 25;
export const R_KNEE = 26;
export const L_ANKLE = 27;
export const R_ANKLE = 28;
export const L_HEEL = 29;
export const R_HEEL = 30;
export const L_FOOT = 31;
export const R_FOOT = 32;

export const JOINT_COUNT = 33;

// [a, b, segment]: pairs of landmark indices a cylinder/bone is drawn between, and the body segment that bone
// belongs to (used by flagSegments to highlight the right limb in red).
export const BONES = [
  [L_SHOULDER, L_ELBOW, "l_arm"],
  [L_ELBOW, L_WRIST, "l_arm"],
  [R_SHOULDER, R_ELBOW, "r_arm"],
  [R_ELBOW, R_WRIST, "r_arm"],
  [L_SHOULDER, L_HIP, "trunk"],
  [R_SHOULDER, R_HIP, "trunk"],
  [L_SHOULDER, R_SHOULDER, "trunk"],
  [L_HIP, R_HIP, "trunk"],
  [L_HIP, L_KNEE, "l_thigh"],
  [R_HIP, R_KNEE, "r_thigh"],
  [L_KNEE, L_ANKLE, "l_shin"],
  [R_KNEE, R_ANKLE, "r_shin"],
  [L_ANKLE, L_HEEL, "l_foot"],
  [L_ANKLE, L_FOOT, "l_foot"],
  [R_ANKLE, R_HEEL, "r_foot"],
  [R_ANKLE, R_FOOT, "r_foot"],
];

function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function dist(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

// Interior angle at `vertex` between the rays to `a` and `b`, in degrees (server engine_bridge._angle_deg).
function angleDeg(a, vertex, b) {
  const u = sub(a, vertex);
  const v = sub(b, vertex);
  const dot = u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const norm = dist(a, vertex) * dist(b, vertex);
  if (norm === 0) return 0;
  const cos = Math.max(-1, Math.min(1, dot / norm));
  return (Math.acos(cos) * 180) / Math.PI;
}

// Flexion: 180 minus the interior angle, so a straight joint reads 0 and a 90 degree bend reads 90
// (server engine_bridge._flexion_deg; README "Frame contract: joint angles").
function flexionDeg(a, vertex, b) {
  return 180 - angleDeg(a, vertex, b);
}

export function kneeFlexion(joints, side) {
  const [hip, knee, ankle] = side === "left"
    ? [joints[L_HIP], joints[L_KNEE], joints[L_ANKLE]]
    : [joints[R_HIP], joints[R_KNEE], joints[R_ANKLE]];
  return flexionDeg(hip, knee, ankle);
}

export function hipFlexion(joints, side) {
  const [shoulder, hip, knee] = side === "left"
    ? [joints[L_SHOULDER], joints[L_HIP], joints[L_KNEE]]
    : [joints[R_SHOULDER], joints[R_HIP], joints[R_KNEE]];
  return flexionDeg(shoulder, hip, knee);
}

function mid(a, b) {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
}

// Trunk lean: angle of the chest-to-pelvis vector off vertical, in degrees (server engine_bridge "trunk_lean").
export function trunkLean(joints) {
  const chest = mid(joints[L_SHOULDER], joints[R_SHOULDER]);
  const pelvis = mid(joints[L_HIP], joints[R_HIP]);
  const trunk = sub(chest, pelvis);
  return (Math.atan2(-trunk[2], trunk[1]) * 180) / Math.PI;
}

// flags: the rep's `flags` array (server/api/sessions.py); affectedSide: "left" | "right". Returns the set of
// BONES `segment` names that should render red for this rep.
export function flagSegments(flags, affectedSide) {
  const out = new Set();
  const side = affectedSide === "left" ? "l" : "r";
  for (const flag of flags || []) {
    if (flag === "knee_cave_r") { out.add("r_thigh"); out.add("r_shin"); }
    else if (flag === "knee_cave_l") { out.add("l_thigh"); out.add("l_shin"); }
    else if (flag === "weight_shift") { out.add(`${side}_thigh`); out.add(`${side}_shin`); out.add(`${side}_foot`); }
    else if (flag === "trunk_lean") { out.add("trunk"); }
  }
  return out;
}

// Linear interpolation between two 33-joint frames (used for the smoothed live pose). Returns `b` unchanged
// when either frame is missing/invalid, so a dropped frame never crashes the caller.
export function lerpJoints(a, b, t) {
  if (!isValidJoints(a) || !isValidJoints(b)) return isValidJoints(b) ? b : (isValidJoints(a) ? a : null);
  const k = Math.max(0, Math.min(1, t));
  return a.map((p, i) => [
    p[0] + (b[i][0] - p[0]) * k,
    p[1] + (b[i][1] - p[1]) * k,
    p[2] + (b[i][2] - p[2]) * k,
  ]);
}

export function isValidJoints(j) {
  if (!Array.isArray(j) || j.length !== JOINT_COUNT) return false;
  return j.every((p) => Array.isArray(p) && p.length === 3 && p.every((n) => typeof n === "number" && Number.isFinite(n)));
}
