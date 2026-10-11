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

FRAMES_NEEDED = 40
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
    def __init__(self, frames_needed: int = FRAMES_NEEDED, forget_s: float = FORGET_S):
        self.frames_needed, self.forget_s = frames_needed, forget_s
        self.reset()

    def reset(self) -> None:
        self.lengths: dict[str, float] | None = None
        self._samples: dict[str, list[float]] = {k: [] for k in [*BONES, "torso", "neck"]}
        self._frames = 0
        self._last_seen: float | None = None
        self._last_t: float | None = None
        self._last_out: np.ndarray | None = None
        self._held: dict[str, np.ndarray] = {}  # last good offsets for joints that go missing

    @property
    def progress(self) -> float:
        return 1.0 if self.lengths is not None else min(self._frames / self.frames_needed, 1.0)

    def update(self, t: float, joints, tracking: str) -> np.ndarray | None:
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
            self._last_out = J.copy()
            return self._last_out
        self._last_out = self._pose(J)
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

    def _pose(self, J: np.ndarray) -> np.ndarray | None:
        L = self.lengths
        if not _ok(J[23], J[24]):
            return None
        P = J.copy()
        hip_mid = (J[23] + J[24]) / 2
        across_h = self._hold("hip_dir", _unit(J[23] - J[24]))
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
            P[HEAD] = J[HEAD] + (sh_mid + neck * L["neck"] - ears)
        elif tracked_sh is not None:
            P[HEAD] = J[HEAD] + (sh_mid - tracked_sh)

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
