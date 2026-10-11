# Vision: anatomical constraints for the 3D avatar (design)

Date: 2026-10-11 · Owner: Eric · Branch: `vision/cameras-to-3d`
Builds on: the rigged 3D person (`vision/rig.py`, `vision/avatar.py`) from the person-selection work.
Scope: **the 3D avatar only.** `FrameResult.joints`, recordings and `checks` are unchanged.

## Why

The rig fixed bone lengths but trusts MediaPipe's directions completely, so the avatar does things a
body can't. Measured on Eric's three recordings (raw joints → current rig):

| Recording | Arm inside torso | Knee backwards | Foot yaw change per frame (median / p95 / max) |
|---|---|---|---|
| refine-20261010-230320 | 17% → 29% | 3% → 0% | 3° / 21° / 129° |
| refine-20261011-002323 | 1% → 8% | 4% → 0% | 4° / 23° / 91° |
| 20261011-005530 (22 s, moves chosen to break it) | 2% → 8% | 3% → 0% | 3° / 20° / 141° |

The rig makes arms-in-torso worse (it doesn't know the torso is solid) and does nothing for the feet,
whose direction comes straight from MediaPipe's heel/toe points.

## Goal

The avatar moves like a body: joints bend only the ways joints bend, limbs don't pass through the
torso, feet don't spin, and the motion is steady — while confidently tracked hands and feet stay
where tracking puts them. Tracking points are evidence, weighed against anatomy and the previous
frame, not commands.

## Success criteria (evaluated on all three recordings; `vision/tests/` gets an evaluation helper)

- Arm (elbow, wrist, hand) inside the torso: **< 1%** of 3D frames.
- Knee or elbow bending backwards / sideways beyond limits: **0%**.
- Foot yaw change per frame: **p95 < 8°**, max < 25°.
- Bone lengths fixed (std < 1 mm), as now.
- Truthfulness: for wrists and ankles with visibility ≥ 0.8, mean distance from the tracked position
  **< 5 cm** (p95 < 10 cm), so the avatar doesn't drift into a pose the person isn't in.
- Cost: < 2 ms per frame for the constraint layer.

## Design

A new `vision/constraints.py` runs inside `BodyRig._pose` after the bone lengths are known, in this
order. Each step is a small pure function with its own tests.

1. **Body frames.** Pelvis frame from the hips and the spine direction (left, up, forward =
   left × up); chest frame from the shoulders and spine. Smoothed over time; all limits below are
   expressed in these frames.
2. **Confidence weighting.** Each tracked joint's target is blended with its previous rigged position
   by its confidence (`FrameResult.conf`, falling back to MediaPipe visibility): below 0.5 the target
   is mostly the prediction (previous pose carried with the parent), above 0.8 mostly the detection.
3. **Hinge limits (elbows, knees).** Flexion 0–150°, one bending direction only: knees bend so the
   shin goes backward relative to the pelvis forward; elbows bend so the forearm moves toward the
   front of the upper arm's plane. Implemented by choosing the IK pole within the allowed half-plane
   (an elbow/knee detected on the wrong side is mirrored into it) and clamping the IK reach so the
   angle stays ≤ 150° flexion and ≥ 0° (no hyperextension).
4. **Ball-joint limits (shoulders, hips).** The upper-arm and thigh directions are kept inside a
   cone per joint, expressed in the chest/pelvis frame (e.g. thigh: up to 120° forward, 30° back,
   45° out, 30° across; upper arm: wide, but not behind the back beyond 60° or across the chest
   beyond 40°). A direction outside its cone is moved to the nearest point on the cone, and the limb
   re-solved.
5. **Solid torso.** The torso is a box in the chest/pelvis frame (shoulder width × torso length ×
   ~18 cm deep, plus the limb radius as margin). An elbow, wrist or hand inside it is pushed out to
   the nearest side face (prefer outward/left-right); the arm is re-solved with fixed lengths so the
   hand reaches the pushed point.
6. **Feet.** The foot direction is built, not copied:
   - yaw: the tracked heel→toe yaw, limited to ±35° from the knee-facing direction (the thigh/shin
     plane's forward); low-confidence heel/toe → the knee-facing direction itself;
   - pitch: within ankle range (−30° to +40° relative to perpendicular to the shin); flat (pitch 0)
     when the foot is anchored or within 3 cm of the floor;
   - while anchored, the foot's yaw is locked too;
   - rate-limited to 360°/s of yaw (12° per frame at 30 fps).
7. **Motion smoothing.** Each bone's direction is smoothed with an adaptive filter (One-Euro on the
   direction, then renormalised): steady when still, responsive when moving. Angular speed is capped
   per bone (limbs 900°/s, torso 360°/s, feet as above).

Steps 3–5 can conflict (e.g. pushing an arm out of the torso then hitting a shoulder limit); they run
in the order above, once per frame, with the torso step last for arms so the visible result never
penetrates.

## Evaluation tooling

`vision/avatar_eval.py` (`python -m vision.avatar_eval FILE...`) replays recordings through the rig
and prints the success-criteria table (arm-in-torso %, hinge violations %, foot yaw p50/p95/max, bone
std, truthfulness, ms/frame), so each step can be measured as it lands and Eric can run it on new
clips. Tests run the same metrics on synthetic motion; the three recordings are used for the
acceptance numbers (they stay local under `data/recordings/`, not in git).

## Debug view

The avatar uses the constrained rig. Keys unchanged, plus **c** toggles constraints on/off (to compare
with the plain rig); the panel says "constraints off" when off.

## Out of scope

Constraining `FrameResult.joints` for the team; finger/hand articulation; full-body physics or
self-collision beyond arms vs torso; head/neck limits beyond the spine direction.
