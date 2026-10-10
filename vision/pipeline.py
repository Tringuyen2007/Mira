"""Per-camera pose results -> FrameResult in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vision.cleanup import Cleaner
from vision.config import CameraConfig
from vision.filters import OneEuroFilter
from vision.frames import world_to_mat
from vision.triangulate import Triangulation, triangulate_pair
from vision.types import LEFT_HIP, RIGHT_HIP, FrameResult, PoseResult, swap_left_right

MIN_3D_JOINTS = 8  # below this the calibration or detections disagree; use one camera instead
FLIP_MARGIN_M = 0.05  # a relabelling must beat the as-detected labels by this much to be used
MIRROR_PENALTY = 1.0  # cost added when the nose ends up behind the shoulders (a mirrored body)


@dataclass
class PipelineOutput:
    frame: FrameResult
    raw: dict[str, PoseResult | None]
    tri: Triangulation | None = None
    skew_s: float | None = None
    swapped: str | None = None  # "A", "B" or "AB": cameras whose left/right labels were flipped back


def _flipped(p: PoseResult) -> PoseResult:
    world = None if p.world is None else swap_left_right(p.world)
    return PoseResult(p.cam, p.t, swap_left_right(p.lm2d), swap_left_right(p.vis), world)


def _hip_mid(points: np.ndarray) -> np.ndarray | None:
    h = (points[LEFT_HIP] + points[RIGHT_HIP]) / 2
    return h if np.isfinite(h).all() else None


def _labelling_cost(tri: Triangulation, views: list[PoseResult], cams: dict[str, CameraConfig]) -> float:
    """How implausible a left/right labelling is, without using history.

    Each camera's own 3D estimate (world landmarks rotated into the mat frame) must agree with the
    triangulated body shape; a flip in one view breaks that. A flip in both views gives a consistent
    but mirrored body, caught by the nose sitting behind the shoulders (front = left x body-up).
    """
    pts = tri.points
    hip = _hip_mid(pts)
    body = tri.ok.copy()
    body[:11] = False
    if hip is None or body.sum() < 4:
        return np.inf
    rel = pts - hip
    mismatch = []
    for p in views:
        if p.world is not None:
            own = world_to_mat(p.world - _hip_mid(p.world), cams[p.cam].R)
            mismatch.append(float(np.median(np.linalg.norm(rel[body] - own[body], axis=1))))
    cost = float(np.mean(mismatch)) if mismatch else 0.0
    shoulders = (pts[11] + pts[12]) / 2
    front = np.cross(pts[LEFT_HIP] - pts[RIGHT_HIP], shoulders - hip)
    nose = pts[0] - shoulders
    if np.isfinite(front).all() and np.isfinite(nose).all() and nose @ front < 0:
        cost += MIRROR_PENALTY
    return cost


class Pipeline:
    def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
                 min_vis: float = 0.5, max_reproj_px: float = 15.0):
        self.cams = cams
        self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
        self._cleaner = Cleaner(cams)
        self._filter = OneEuroFilter() if smoothing else None

    def process(self, t: float, a: PoseResult | None, b: PoseResult | None, skew_s: float | None = None) -> PipelineOutput:
        """t is unix seconds (the FrameResult clock)."""
        raw = {"A": a, "B": b}
        tri, swapped = None, None
        ca, cb = self.cams.get("A"), self.cams.get("B")
        if (a is not None and b is not None and a.detected and b.detected
                and ca is not None and cb is not None and ca.calibrated and cb.calibrated):
            tri = triangulate_pair(a, b, ca, cb, self.min_vis, self.max_reproj_px)
            # Equal-height cameras can't see a left/right flip in reprojection: score each labelling.
            best = _labelling_cost(tri, [a, b], self.cams) - FLIP_MARGIN_M
            a0, b0 = a, b
            for name in ("B", "A", "AB"):
                a2 = _flipped(a0) if "A" in name else a0
                b2 = _flipped(b0) if "B" in name else b0
                tri2 = triangulate_pair(a2, b2, ca, cb, self.min_vis, self.max_reproj_px)
                cost = _labelling_cost(tri2, [a2, b2], self.cams)
                if cost < best:
                    best, swapped, a, b, tri = cost, name, a2, b2, tri2
        usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
        joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
        if self._filter is not None:
            joints = self._filter(joints, t)
        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
        return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped)
