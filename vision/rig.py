"""A rigged body for the 3D view: bone lengths measured once, then only the joints rotate.

MediaPipe estimates every joint afresh each frame, so bone lengths wobble by centimetres. BodyRig
first measures the person over FRAMES_NEEDED frames of 3D tracking (median per bone, left and right
averaged), then poses a fixed-proportion skeleton each frame:

- hips follow the tracked hip midpoint and direction; the spine, shoulders and head take their
  direction from tracking at the measured lengths;
- arms and legs use two-bone IK: with fixed bone lengths, each limb reaches for the tracked wrist or
  ankle (so an anchored foot stays put), bending toward the tracked elbow or knee;
- a joint missing for a moment holds its last pose relative to its parent.

Only the 3D view uses this. FrameResult.joints, recordings and checks stay as measured.
"""
from __future__ import annotations

import numpy as np

from vision import constraints as C

FRAMES_NEEDED = 40
NECK_MAX_DEG = 10.0  # head direction from the shoulders, at most this far off the spine (body rules on)
HOLD_S = 1.0  # both hips missing for up to this long: keep showing the last pose
FORGET_S = 5.0  # nobody tracked for this long: assume someone new and measure again

# measured bone -> joint pairs (left and right pooled)
BONES = {
    "upper_arm": [(11, 13), (12, 14)], "forearm": [(13, 15), (14, 16)],
    "thigh": [(23, 25), (24, 26)], "shin": [(25, 27), (26, 28)],
    "heel": [(27, 29), (28, 30)], "foot": [(29, 31), (30, 32)],
    "shoulder_width": [(11, 12)], "hip_width": [(23, 24)],
}
SIDES = {"l": dict(shoulder=11, elbow=13, wrist=15, hand=(17, 19, 21), hip=23, knee=25, ankle=27, heel=29, toe=31),
         "r": dict(shoulder=12, elbow=14, wrist=16, hand=(18, 20, 22), hip=24, knee=26, ankle=28, heel=30, toe=32)}
HEAD = list(range(0, 11))
FORWARD = np.array([0.0, 0.0, 1.0])


def _ok(*pts) -> bool:
    return all(p is not None and np.isfinite(p).all() for p in pts)


def _unit(v, fallback=None):
    n = np.linalg.norm(v) if v is not None and np.isfinite(v).all() else 0.0
    return v / n if n > 1e-9 else fallback


def _within_angle(v, axis, max_deg: float) -> np.ndarray:
    """v turned toward axis so the angle between them is at most max_deg."""
    c = float(np.clip(v @ axis, -1.0, 1.0))
    if np.degrees(np.arccos(c)) <= max_deg:
        return v
    side = _unit(v - c * axis, None)
    if side is None:
        return axis
    a = np.radians(max_deg)
    return np.cos(a) * axis + np.sin(a) * side


def two_bone_ik(root, target, pole, l1: float, l2: float, fallback_bend=None):
    """(middle joint, end joint) of a limb with bone lengths l1, l2 reaching from root toward target,
    bending toward pole. Out of reach: straight toward the target."""
    root, target = np.asarray(root, float), np.asarray(target, float)
    d = _unit(target - root, np.array([0.0, -1.0, 0.0]))
    dist = float(np.clip(np.linalg.norm(target - root), abs(l1 - l2) + 1e-6, l1 + l2 - 1e-9))
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = float(np.sqrt(max(l1 * l1 - a * a, 0.0)))
    bend = None
    if _ok(pole):
        p = np.asarray(pole, float) - root
        bend = _unit(p - (p @ d) * d)
    if bend is None and fallback_bend is not None:
        bend = _unit(fallback_bend - (fallback_bend @ d) * d)
    if bend is None:
        bend = _unit(FORWARD - (FORWARD @ d) * d, _unit(np.cross(d, [1.0, 0.0, 0.0])))
    mid = root + d * a + bend * h
    return mid, root + d * dist


class BodyRig:
    def __init__(self, frames_needed: int = FRAMES_NEEDED, forget_s: float = FORGET_S, constraints: bool = True):
        self.frames_needed, self.forget_s, self.constraints = frames_needed, forget_s, constraints
        self.reset()

    def reset(self) -> None:
        self.lengths: dict[str, float] | None = None
        self._samples: dict[str, list[float]] = {k: [] for k in [*BONES, "torso", "neck"]}
        self._frames = 0
        self._last_seen: float | None = None
        self._last_t: float | None = None
        self._last_out: np.ndarray | None = None
        self._held: dict[str, np.ndarray] = {}  # last good offsets for joints that go missing
        self._foot_yaw: dict[str, float] = {}
        self._locked_yaw: dict[str, float | None] = {"l": None, "r": None}
        self._prev_pose_t: float | None = None
        self._good: tuple[float, np.ndarray] | None = None  # (time, pose) of the last successful pose
        self._body_yaw: float | None = None
        self._push_face: dict[str, str] = {}  # torso face each arm point was last pushed through

    @property
    def progress(self) -> float:
        return 1.0 if self.lengths is not None else min(self._frames / self.frames_needed, 1.0)

    def update(self, t: float, joints, tracking: str, conf=None, feet_locked: dict[str, bool] | None = None
               ) -> np.ndarray | None:
        """Rigged joints (33, 3) for this frame; the raw joints while measuring; None without a body."""
        if self._last_t is not None and t == self._last_t:  # the same frame drawn again
            return self._last_out
        self._last_t = t
        if self._last_seen is not None and t - self._last_seen > self.forget_s:
            self.reset()
            self._last_t = t
        if joints is None or tracking == "lost":
            self._last_out = None
            return None
        J = np.asarray(joints, float)
        self._last_seen = t
        if self.lengths is None:  # measure only from two-camera frames; pose in single-camera mode too
            if tracking == "3d":
                self._measure(J)
            if self.lengths is None:
                self._last_out = J.copy()
                return self._last_out
        posed = self._pose_constrained(J, conf, t, feet_locked or {}) if self.constraints else self._pose(J)
        if posed is None and self._good is not None and t - self._good[0] <= HOLD_S:
            return self._good[1]  # both hips briefly gone: keep the last body on screen
        self._last_out = posed
        if posed is not None:
            self._good = (t, posed)
        self._prev_pose_t = t
        return self._last_out

    def _measure(self, J: np.ndarray) -> None:
        if not _ok(J[11], J[12], J[23], J[24]):
            return
        for name, pairs in BONES.items():
            self._samples[name] += [float(np.linalg.norm(J[a] - J[b])) for a, b in pairs if _ok(J[a], J[b])]
        hip_mid, sh_mid = (J[23] + J[24]) / 2, (J[11] + J[12]) / 2
        self._samples["torso"].append(float(np.linalg.norm(sh_mid - hip_mid)))
        if _ok(J[7], J[8]):
            self._samples["neck"].append(float(np.linalg.norm((J[7] + J[8]) / 2 - sh_mid)))
        self._frames += 1
        if self._frames >= self.frames_needed:
            self.lengths = {k: float(np.median(v)) for k, v in self._samples.items() if v}

    def _hold(self, key: str, value):
        """Remember a good offset, or return the remembered one when value is missing."""
        if _ok(value):
            self._held[key] = np.asarray(value, float)
            return self._held[key]
        return self._held.get(key)

    def _pose_trunk(self, J: np.ndarray) -> np.ndarray | None:
        """Hips, spine, shoulders and head at the measured lengths (shared by both posing paths)."""
        L = self.lengths
        if _ok(J[23], J[24]):
            hip_mid = (J[23] + J[24]) / 2
            across_h = self._hold("hip_dir", _unit(J[23] - J[24]))
        elif "hip_dir" in self._held and (_ok(J[23]) or _ok(J[24])):
            # one hip hidden (e.g. by the thigh in a squat): rebuild it from the other and the last direction
            across_h = self._held["hip_dir"]
            hip_mid = (J[23] - across_h * L["hip_width"] / 2 if _ok(J[23])
                       else J[24] + across_h * L["hip_width"] / 2)
        else:
            return None
        P = J.copy()
        P[23], P[24] = hip_mid + across_h * L["hip_width"] / 2, hip_mid - across_h * L["hip_width"] / 2
        tracked_sh = (J[11] + J[12]) / 2 if _ok(J[11], J[12]) else None
        spine = self._hold("spine", _unit(tracked_sh - hip_mid) if tracked_sh is not None else None)
        sh_mid = hip_mid + spine * L["torso"]
        across_s = _unit(J[11] - J[12]) if _ok(J[11], J[12]) else None
        if across_s is not None:
            across_s = _unit(across_s - (across_s @ spine) * spine)
        across_s = self._hold("shoulder_dir", across_s)
        P[11], P[12] = sh_mid + across_s * L["shoulder_width"] / 2, sh_mid - across_s * L["shoulder_width"] / 2

        ears = (J[7] + J[8]) / 2 if _ok(J[7], J[8]) else None
        if ears is not None and tracked_sh is not None and "neck" in L:
            neck = self._hold("neck_dir", _unit(ears - tracked_sh))
            if self.constraints:  # MediaPipe's ears sit well in front of its shoulders: keep the head over the spine
                neck = _within_angle(neck, spine, NECK_MAX_DEG)
            P[HEAD] = J[HEAD] + (sh_mid + neck * L["neck"] - ears)
        elif tracked_sh is not None:
            P[HEAD] = J[HEAD] + (sh_mid - tracked_sh)
        return P

    def _pose(self, J: np.ndarray) -> np.ndarray | None:
        """Fixed lengths, limbs by plain two-bone IK, no anatomical rules (constraints=False)."""
        P = self._pose_trunk(J)
        if P is None:
            return None
        L = self.lengths
        for side, s in SIDES.items():
            for root, mid, end, l1, l2 in ((s["shoulder"], s["elbow"], s["wrist"], "upper_arm", "forearm"),
                                           (s["hip"], s["knee"], s["ankle"], "thigh", "shin")):
                target = J[end] if _ok(J[end]) else None
                if target is None:  # hold the last pose; a limb never seen yet hangs straight down
                    held = self._held.get(f"{side}{end}")
                    target = P[root] + (held if held is not None else np.array([0.0, -(L[l1] + L[l2]), 0.0]))
                m, e = two_bone_ik(P[root], target, J[mid], L[l1], L[l2], self._held.get(f"{side}{mid}bend"))
                self._held[f"{side}{end}"] = e - P[root]
                self._held[f"{side}{mid}bend"] = m - P[root]
                P[mid], P[end] = m, e
            for k in s["hand"]:  # hands move rigidly with the wrist
                off = self._hold(f"{k}", J[k] - J[s["wrist"]] if _ok(J[k], J[s["wrist"]]) else None)
                if off is not None:
                    P[k] = P[s["wrist"]] + off
            if "heel" in L and "foot" in L:
                to_heel = self._hold(f"{side}heel", _unit(J[s["heel"]] - J[s["ankle"]])
                                     if _ok(J[s["heel"]], J[s["ankle"]]) else None)
                to_toe = self._hold(f"{side}toe", _unit(J[s["toe"]] - J[s["heel"]])
                                    if _ok(J[s["toe"]], J[s["heel"]]) else None)
                if to_heel is not None and to_toe is not None:
                    P[s["heel"]] = P[s["ankle"]] + to_heel * L["heel"]
                    P[s["toe"]] = P[s["heel"]] + to_toe * L["foot"]
        return P

    def _target(self, J, conf, key: str, end: int, root_pos, hang) -> np.ndarray:
        """Where a limb end should go: the detection, blended toward the predicted position (last
        pose carried with its parent) as confidence drops; the prediction when the detection is missing."""
        held = self._held.get(key)
        pred = root_pos + (held if held is not None else hang)
        if not _ok(J[end]):
            return pred
        c = 1.0 if conf is None or not np.isfinite(conf[end]) else float(conf[end])
        w = float(np.clip((c - 0.3) / 0.5, 0.0, 1.0)) if held is not None else 1.0
        return w * J[end] + (1 - w) * pred

    def _pose_constrained(self, J: np.ndarray, conf, t: float, feet_locked: dict) -> np.ndarray | None:
        """_pose plus the anatomical rules in vision.constraints."""
        P = self._pose_trunk(J)
        if P is None:
            return None
        L = self.lengths
        hip_mid = (P[23] + P[24]) / 2
        spine = self._held["spine"]
        pelvis = C.body_frame(hip_mid, P[23] - P[24], spine)
        chest = C.body_frame(hip_mid, P[11] - P[12], spine)
        if pelvis is None or chest is None:
            return self._pose(J)
        dt = (t - self._prev_pose_t) if self._prev_pose_t is not None else 1 / 30
        raw_yaw = float(np.arctan2(pelvis.fwd[0], pelvis.fwd[2]))
        self._body_yaw = C.smooth_angle(self._body_yaw, raw_yaw, C.BODY_YAW_SMOOTH_DEG, C.BODY_YAW_MIN_GAIN)
        floor_fwd = np.array([np.sin(self._body_yaw), 0.0, np.cos(self._body_yaw)])  # smoothed body facing
        for side, s in SIDES.items():
            sign = 1.0 if side == "l" else -1.0
            # legs: knee bends forward only, at most FLEX_MAX_DEG; thigh not far behind the body
            hip = P[s["hip"]]
            l1, l2 = L["thigh"], L["shin"]
            target = self._target(J, conf, f"{side}{s['ankle']}", s["ankle"], hip, np.array([0.0, -(l1 + l2), 0.0]))
            target = self._min_reach(hip, target, l1, l2)
            pole = C.hinge_pole(hip, target, J[s["knee"]] if _ok(J[s["knee"]]) else None, pelvis.fwd)
            knee, ankle = two_bone_ik(hip, target, pole, l1, l2)
            thigh = C.limit_thigh(C.unit(knee - hip), pelvis)
            if not np.allclose(thigh, C.unit(knee - hip)):
                knee = hip + thigh * l1
                ankle = knee + C.unit(target - knee) * l2
            self._held[f"{side}{s['ankle']}"] = ankle - hip
            P[s["knee"]], P[s["ankle"]] = knee, ankle
            # arms: elbow flexes at most FLEX_MAX_DEG, bending toward the tracked elbow (or the last bend);
            # never inside the torso
            sh = P[s["shoulder"]]
            l1, l2 = L["upper_arm"], L["forearm"]
            target = self._target(J, conf, f"{side}{s['wrist']}", s["wrist"], sh, np.array([0.0, -(l1 + l2), 0.0]))
            target = self._min_reach(sh, target, l1, l2)
            bend_key = f"{side}{s['elbow']}bend"
            elbow, wrist = two_bone_ik(sh, target, J[s["elbow"]] if _ok(J[s["elbow"]]) else None, l1, l2,
                                       self._held.get(bend_key))
            length = float(np.linalg.norm((P[11] + P[12]) / 2 - hip_mid))
            hw = L["shoulder_width"] / 2

            def push(point, name):  # out of the torso, keeping the face this point used last time
                key = f"{side}_{name}"
                out, moved, face = C.push_out_of_torso(point, chest, hw, length, sign, prefer=self._push_face.get(key))
                if moved:
                    self._push_face[key] = face
                return out, moved

            for _ in range(3):
                e2, moved_e = push(elbow, "elbow")
                w2, moved_w = push(wrist, "wrist")
                if not (moved_e or moved_w):
                    break
                elbow, wrist = two_bone_ik(sh, w2 if moved_w else wrist, e2 if moved_e else elbow, l1, l2)
            if push(elbow, "elbow")[1]:  # still in: swing the elbow out sideways
                side_pole = sh + sign * chest.left - 0.3 * chest.fwd
                elbow, wrist = two_bone_ik(sh, wrist, side_pole, l1, l2)
                w2, moved_w = push(wrist, "wrist")
                if moved_w:
                    elbow, wrist = two_bone_ik(sh, w2, side_pole, l1, l2)
                q = chest.local(elbow)
                if C.push_out_of_torso(elbow, chest, hw, length, sign)[1]:  # still in: out through its own side
                    q[0] = sign * (hw + C.LIMB_MARGIN_M)
                    elbow, wrist = two_bone_ik(sh, wrist, chest.world(q), l1, l2)
            self._held[f"{side}{s['wrist']}"] = wrist - sh
            self._held[bend_key] = elbow - sh
            P[s["elbow"]], P[s["wrist"]] = elbow, wrist
            for k in s["hand"]:
                off = self._hold(f"{k}", J[k] - J[s["wrist"]] if _ok(J[k], J[s["wrist"]]) else None)
                if off is not None:
                    P[k] = wrist + off
            # feet: point within FOOT_YAW_LIMIT_DEG of the body's forward, turn at a capped rate,
            # lie flat near the floor, keep their yaw while anchored
            if "heel" in L and "foot" in L:
                heel_j, toe_j = s["heel"], s["toe"]
                tracked = C.unit(J[toe_j] - J[heel_j]) if _ok(J[toe_j], J[heel_j]) else None
                confident = conf is None or (np.isfinite(conf[[heel_j, toe_j]]).all() and min(conf[heel_j], conf[toe_j]) >= 0.5)
                locked = bool(feet_locked.get(side))
                if locked and self._locked_yaw[side] is None:
                    self._locked_yaw[side] = self._foot_yaw.get(side)
                elif not locked:
                    self._locked_yaw[side] = None
                yaw = C.foot_yaw(tracked, floor_fwd, self._foot_yaw.get(side), dt, self._locked_yaw[side], confident)
                self._foot_yaw[side] = yaw
                flat = locked or P[s["ankle"]][1] < 0.08 + C.FLAT_HEIGHT_M
                pitch = C.foot_pitch(tracked, flat)
                fdir = np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), np.cos(yaw) * np.cos(pitch)])
                flat_dir = np.array([np.sin(yaw), 0.0, np.cos(yaw)])
                heel = ankle + C.unit(-0.7 * flat_dir + np.array([0.0, -0.7, 0.0])) * L["heel"]
                P[heel_j], P[toe_j] = heel, heel + fdir * L["foot"]
        return P

    @staticmethod
    def _min_reach(root, target, l1, l2) -> np.ndarray:
        d = target - root
        n = float(np.linalg.norm(d))
        mn = C.min_reach(l1, l2)
        if n < mn:
            u = C.unit(d) if n > 1e-9 else np.array([0.0, -1.0, 0.0])
            return root + u * mn
        return target
