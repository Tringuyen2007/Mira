"""Pick the one person to track: the person on or next to the mat.

Each camera may report several people. A person in A and a person in B are the same person when
their hip, shoulder and ankle midpoints triangulate consistently (midpoints don't care about
MediaPipe's left/right flips). Of the consistent pairs standing within `vicinity_m` of the mat, the
one closest to the mat centre wins, with stickiness so tracking doesn't flicker between people. If
no pair agrees (a hand over one lens, the wrong person in one view), each camera's people are placed
on the floor through their feet, and the camera that puts someone at the mat is kept; the other is
dropped for that frame and the pipeline's single-camera fallback takes over.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import cam_center
from vision.triangulate import triangulate_points
from vision.types import PoseResult

FOOT_HEIGHTS = {27: 0.08, 28: 0.08, 29: 0.04, 30: 0.04}  # ankles, heels: metres above the mat surface
MIDPOINTS = ((23, 24), (11, 12), (27, 28))  # hips first: its triangulation is the person's position
MIN_VIS = 0.5
STICK_M = 0.5  # the tracked person is the candidate within this distance of where they were


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


def floor_position(person: PoseResult, cam: CameraConfig) -> np.ndarray | None:
    """(x, z) where the person stands, from one camera: rays through the visible feet meet the floor."""
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
    hits = [c + si * d for si, d in zip(s, dirs) if np.isfinite(si) and si > 0]
    if not hits:
        return None
    return np.median(np.array(hits)[:, [0, 2]], axis=0)


def _midpoints(p: PoseResult) -> dict[int, np.ndarray]:
    return {k: (p.lm2d[i] + p.lm2d[j]) / 2 for k, (i, j) in enumerate(MIDPOINTS)
            if p.vis[i] >= MIN_VIS and p.vis[j] >= MIN_VIS}


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
                 pair_px: float = 25.0, switch_m: float = 0.3, switch_s: float = 0.5):
        self.cams, self.mat = cams, mat
        self.vicinity_m, self.pair_px, self.switch_m, self.switch_s = vicinity_m, pair_px, switch_m, switch_s
        self._pos: np.ndarray | None = None
        self._challenge_since: float | None = None

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

    def select(self, t: float, a_people: list[PoseResult], b_people: list[PoseResult]) -> Selection:
        a_people = [p for p in a_people if p is not None and p.detected]
        b_people = [p for p in b_people if p is not None and p.detected]
        ca, cb = self.cams.get("A"), self.cams.get("B")
        if not (ca is not None and cb is not None and ca.calibrated and cb.calibrated):
            a = a_people[0] if a_people else None
            b = b_people[0] if b_people else None
            return Selection(a, b, {"A": a_people[1:], "B": b_people[1:]})

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
                    if xz is not None and mat_distance(self.mat, xz) <= self.vicinity_m:
                        singles.append((xz, (name, k)))
            if not singles:
                self._pos, self._challenge_since = None, None
                return Selection(None, None, {"A": a_people, "B": b_people})
            xz, (name, k) = self._choose(t, singles)
            a = a_people[k] if name == "A" else None
            b = b_people[k] if name == "B" else None
            other = b_people if name == "A" else a_people
            dropped = ("B" if name == "A" else "A") if other else None
        ignored = {"A": [p for p in a_people if p is not a], "B": [p for p in b_people if p is not b]}
        return Selection(a, b, ignored, dropped, np.asarray(xz, float))
