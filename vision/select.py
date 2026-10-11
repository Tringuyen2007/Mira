"""Pick the one person to track: the person on or next to the mat.

Each camera may report several people. A person in A and a person in B are the same person when
their hip, shoulder and ankle midpoints triangulate consistently (midpoints don't care about
MediaPipe's left/right flips). Of the consistent pairs standing within `vicinity_m` of the mat, the
one closest to the mat centre wins, with stickiness so tracking doesn't flicker between people. If
no pair agrees (a hand over one lens, the wrong person in one view), each camera's people are placed
on the floor through their feet, and the camera that puts someone of human size at the mat is kept;
the other is dropped for that frame and the pipeline's single-camera fallback takes over. One camera
can't confirm a newcomer, so in that fallback the tracked person is never swapped for one nearer the
centre. With one camera only, an already tracked person can continue through a floor exercise by
their nearby 2D hips and visible torso, even when their projected head-to-foot span looks too short
for an upright person. New tracks still require the human-size check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import cam_center, mat_to_cam
from vision.triangulate import triangulate_points
from vision.types import PoseResult

FOOT_HEIGHTS = {27: 0.08, 28: 0.08, 29: 0.04, 30: 0.04}  # ankles, heels: metres above the mat surface
MIDPOINTS = ((23, 24), (11, 12), (27, 28))  # hips first: its triangulation is the person's position
MIN_VIS = 0.5
FEET_AGREE_M = 0.6  # two feet placed closer than this are taken as both planted
STICK_M = 0.5  # the tracked person is the candidate within this distance of where they were
HEIGHT_RANGE_M = (0.5, 2.4)  # implied upright feet-to-head size of a person seen by one camera
CONTINUITY_PX = 100.0  # maximum hip displacement between adjacent detections in one view


@dataclass
class Selection:
    a: PoseResult | None
    b: PoseResult | None
    ignored: dict[str, list[PoseResult]] = field(default_factory=dict)
    dropped: str | None = None  # camera ignored this frame because it disagreed
    position: np.ndarray | None = None  # (x, z) of the tracked person on the floor


def mat_distance(mat: MatConfig, xz) -> float:
    """0 on the mat, else the floor distance to its edge."""
    dx = max(abs(float(xz[0])) - mat.width_m / 2, 0.0)
    dz = max(abs(float(xz[1])) - mat.length_m / 2, 0.0)
    return float(np.hypot(dx, dz))


def floor_feet(person: PoseResult, cam: CameraConfig) -> tuple[np.ndarray, list[int]] | None:
    """(x, z) where the person stands, from one camera, and the foot joints that placed it: rays through
    the visible feet meet the floor."""
    if not person.detected:
        return None
    idx = [j for j in FOOT_HEIGHTS if person.vis[j] >= MIN_VIS]
    if not idx:
        return None
    uv = person.lm2d[idx].reshape(-1, 1, 2).astype(np.float64)
    n = cv2.undistortPoints(uv, cam.K, cam.dist).reshape(-1, 2)
    dirs = np.hstack([n, np.ones((len(n), 1))]) @ np.asarray(cam.R)  # camera rays in the mat frame
    c = cam_center(cam.R, cam.t)
    heights = np.array([FOOT_HEIGHTS[j] for j in idx])
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (heights - c[1]) / dirs[:, 1]
    hits = {j: (c + si * d)[[0, 2]] for j, si, d in zip(idx, s, dirs) if np.isfinite(si) and si > 0}
    if not hits:
        return None
    # each foot on its own: a lifted foot's ray meets the floor far behind it, so trust the foot nearer the
    # camera; average the two when they agree (both planted)
    feet = []
    for side in ((27, 29), (28, 30)):
        used = [j for j in side if j in hits]
        if used:
            feet.append((np.median([hits[j] for j in used], axis=0), used))
    if len(feet) == 2 and np.linalg.norm(feet[0][0] - feet[1][0]) < FEET_AGREE_M:
        return (feet[0][0] + feet[1][0]) / 2, feet[0][1] + feet[1][1]
    return min(feet, key=lambda f: np.linalg.norm(f[0] - c[[0, 2]]))


def floor_position(person: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """(x, z) where the person stands, from one camera (see floor_feet)."""
    r = floor_feet(person, cam)
    return None if r is None else r[0]


def plausible_height(person: PoseResult, cam: CameraConfig, xz) -> bool:
    """Is the feet-to-head span the size of a person standing at floor point xz? Rejects a skeleton
    fitted to a hand over the lens whose feet happen to land on the mat. Needs a visible head."""
    feet = [j for j in FOOT_HEIGHTS if person.vis[j] >= MIN_VIS]
    heads = []
    if person.vis[0] >= MIN_VIS:
        heads.append(person.lm2d[0])
    if person.vis[2] >= MIN_VIS and person.vis[5] >= MIN_VIS:
        heads.append((person.lm2d[2] + person.lm2d[5]) / 2)
    if not feet or not heads:
        return False
    head = min(heads, key=lambda h: h[1])  # the higher one in the image
    px = float(np.linalg.norm(head - person.lm2d[feet].mean(axis=0)))
    depth = float(mat_to_cam(np.array([xz[0], 0.0, xz[1]]), cam.R, cam.t)[2])
    if not np.isfinite(px) or depth <= 0:
        return False
    height = px * depth / float(cam.K[1, 1])
    return HEIGHT_RANGE_M[0] <= height <= HEIGHT_RANGE_M[1]


def _midpoints(p: PoseResult) -> dict[int, np.ndarray]:
    return {k: (p.lm2d[i] + p.lm2d[j]) / 2 for k, (i, j) in enumerate(MIDPOINTS)
            if p.vis[i] >= MIN_VIS and p.vis[j] >= MIN_VIS}


def _hip2d(p: PoseResult) -> np.ndarray | None:
    """2D hip midpoint (px) when both hips are visible."""
    if p.vis[23] < MIN_VIS or p.vis[24] < MIN_VIS:
        return None
    h = (p.lm2d[23] + p.lm2d[24]) / 2
    return h if np.isfinite(h).all() else None


def pair_position(pa: PoseResult, pb: PoseResult, ca: CameraConfig, cb: CameraConfig) -> tuple[float, np.ndarray] | None:
    """(median midpoint reprojection error px, hip (x, z)) for one person in A paired with one in B."""
    ma, mb = _midpoints(pa), _midpoints(pb)
    keys = sorted(set(ma) & set(mb))
    if 0 not in keys:
        return None
    X, err, _ = triangulate_points(np.array([ma[k] for k in keys]), np.array([mb[k] for k in keys]), ca, cb)
    if not np.isfinite(err).all() or not np.isfinite(X[0]).all():
        return None
    return float(np.median(err)), X[0][[0, 2]]


class PersonSelector:
    def __init__(self, cams: dict[str, CameraConfig], mat: MatConfig, vicinity_m: float = 1.0,
                 pair_px: float = 25.0, switch_m: float = 0.3, switch_s: float = 0.5, forget_s: float = 1.0):
        """forget_s: how long the tracked person's position is remembered through frames with nobody."""
        self.cams, self.mat = cams, mat
        self.vicinity_m, self.pair_px, self.switch_m, self.switch_s = vicinity_m, pair_px, switch_m, switch_s
        self.forget_s = forget_s
        self._pos: np.ndarray | None = None
        self._last_pick_t: float | None = None
        self._challenge_since: float | None = None
        self._hip2d: np.ndarray | None = None  # one camera: 2D hip midpoint of the last chosen person

    def _choose_single(self, cands: list[tuple[np.ndarray, object]]):
        """One camera only: keep the tracked person (the candidate nearest where they were); with no
        track, the one nearest the mat centre. Never switch to someone just for being nearer the centre."""
        ref = self._pos if self._pos is not None else np.zeros(2)
        pick = int(np.argmin([float(np.linalg.norm(xz - ref)) for xz, _ in cands]))
        self._challenge_since = None
        self._pos = np.asarray(cands[pick][0], float)
        return cands[pick]

    def _choose(self, t: float, cands: list[tuple[np.ndarray, object]]):
        """cands: (xz, key). Closest to the mat centre, but stick with the current person unless another
        is switch_m closer for switch_s."""
        centre = [float(np.linalg.norm(xz)) for xz, _ in cands]
        best = int(np.argmin(centre))
        current = None
        if self._pos is not None:
            d = [float(np.linalg.norm(xz - self._pos)) for xz, _ in cands]
            k = int(np.argmin(d))
            current = k if d[k] <= STICK_M else None
        if current is None or best == current:
            pick = best if current is None else current
            self._challenge_since = None
        elif centre[best] <= centre[current] - self.switch_m:
            if self._challenge_since is None:
                self._challenge_since = t
            if t - self._challenge_since >= self.switch_s:
                pick, self._challenge_since = best, None
            else:
                pick = current
        else:
            pick, self._challenge_since = current, None
        self._pos = np.asarray(cands[pick][0], float)
        return cands[pick]

    def _select_one(self, t: float, name: str, people: list[PoseResult]) -> Selection:
        cam = self.cams[name]
        if self._pos is not None and t - self._last_pick_t > self.forget_s:
            self._pos, self._challenge_since, self._hip2d = None, None, None
        cands = []
        for k, p in enumerate(people):
            xz = floor_position(p, cam)
            if xz is not None and mat_distance(self.mat, xz) <= self.vicinity_m and plausible_height(p, cam, xz):
                cands.append((xz, k))
        # A floor exercise shortens the projected head-to-foot span. Keep an already
        # tracked person by their hips and torso even when the upright height test fails.
        # New people must still pass plausible_height before they can start a track.
        tracked = None
        if self._pos is not None and self._hip2d is not None:
            hips = {k: _hip2d(p) for k, p in enumerate(people)}
            hips = {k: h for k, h in hips.items() if h is not None}
            if hips:
                tracked = min(hips, key=lambda k: float(np.linalg.norm(hips[k] - self._hip2d)))
                hip_step = float(np.linalg.norm(hips[tracked] - self._hip2d))
                p = people[tracked]
                torso_visible = all(p.vis[j] >= MIN_VIS for j in (11, 12, 23, 24))
                xz = floor_position(p, cam)
                on_mat = xz is None or mat_distance(self.mat, xz) <= self.vicinity_m
                if hip_step <= CONTINUITY_PX and torso_visible and on_mat:
                    self._pos = np.asarray(xz, float) if xz is not None else self._pos
                    self._last_pick_t = t
                    self._challenge_since = None
                    self._hip2d = hips[tracked]
                    return self._selection(name, people, tracked, self._pos.copy())
        sticky = self._pos is not None and any(np.linalg.norm(xz - self._pos) <= STICK_M for xz, _ in cands)
        if tracked is not None and not sticky and tracked not in [k for _, k in cands]:
            # tracked but not placeable: keep them (position unknown) rather than switch or lose the track;
            # _last_pick_t is not refreshed, so the track is still forgotten after forget_s of this
            self._challenge_since = None
            self._hip2d = _hip2d(people[tracked])
            return self._selection(name, people, tracked, None)
        if not cands:
            self._challenge_since = None
            return Selection(None, None, {"A": [], "B": [], name: people})
        xz, k = self._choose(t, cands)
        self._last_pick_t = t
        self._hip2d = _hip2d(people[k])
        return self._selection(name, people, k, np.asarray(xz, float))

    @staticmethod
    def _selection(name: str, people: list[PoseResult], k: int, position) -> Selection:
        chosen = people[k]
        sel = {"A": None, "B": None, name: chosen}
        ignored = {"A": [], "B": [], name: [p for p in people if p is not chosen]}
        return Selection(sel["A"], sel["B"], ignored, None, position)

    def select(self, t: float, a_people: list[PoseResult], b_people: list[PoseResult]) -> Selection:
        a_people = [p for p in a_people if p is not None and p.detected]
        b_people = [p for p in b_people if p is not None and p.detected]
        ca, cb = self.cams.get("A"), self.cams.get("B")
        calibrated = [n for n, c in (("A", ca), ("B", cb)) if c is not None and c.calibrated]
        if not calibrated:
            a = a_people[0] if a_people else None
            b = b_people[0] if b_people else None
            return Selection(a, b, {"A": a_people[1:], "B": b_people[1:]})
        if len(calibrated) == 1:  # one camera: people placed on the floor by their feet, closest to the centre
            return self._select_one(t, calibrated[0], a_people if calibrated[0] == "A" else b_people)
        if self._pos is not None and t - self._last_pick_t > self.forget_s:
            self._pos, self._challenge_since = None, None  # gone too long: start afresh

        pairs = []
        for i, pa in enumerate(a_people):
            for j, pb in enumerate(b_people):
                r = pair_position(pa, pb, ca, cb)
                if r is not None and r[0] <= self.pair_px and mat_distance(self.mat, r[1]) <= self.vicinity_m:
                    pairs.append((r[1], (i, j)))
        dropped = None
        if pairs:
            xz, (i, j) = self._choose(t, pairs)
            a, b = a_people[i], b_people[j]
        else:
            singles = []
            for name, people, cam in (("A", a_people, ca), ("B", b_people, cb)):
                for k, p in enumerate(people):
                    xz = floor_position(p, cam)
                    if (xz is not None and mat_distance(self.mat, xz) <= self.vicinity_m
                            and plausible_height(p, cam, xz)):
                        singles.append((xz, (name, k)))
            if not singles:
                self._challenge_since = None  # keep _pos for forget_s: one empty frame mustn't lose the track
                return Selection(None, None, {"A": a_people, "B": b_people})
            xz, (name, k) = self._choose_single(singles)
            a = a_people[k] if name == "A" else None
            b = b_people[k] if name == "B" else None
            other = b_people if name == "A" else a_people
            dropped = ("B" if name == "A" else "A") if other else None
        self._last_pick_t = t
        ignored = {"A": [p for p in a_people if p is not a], "B": [p for p in b_people if p is not b]}
        return Selection(a, b, ignored, dropped, np.asarray(xz, float))
