"""Per-camera pose results -> FrameResult in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vision.cleanup import Cleaner
from vision.config import CameraConfig
from vision.filters import OneEuroFilter
from vision.triangulate import Triangulation, triangulate_pair
from vision.types import FrameResult, PoseResult, swap_left_right

MIN_3D_JOINTS = 8  # below this the calibration or detections disagree; use one camera instead
SWAP_HOLD_S = 0.5  # how recent the previous 3D frame must be to judge a left/right flip
SWAP_MARGIN = 0.5  # a flipped labelling must be at least 2x closer to the previous frame


@dataclass
class PipelineOutput:
    frame: FrameResult
    raw: dict[str, PoseResult | None]
    tri: Triangulation | None = None
    skew_s: float | None = None
    swapped: str | None = None  # camera whose left/right labels were flipped back, if any


def _flipped(p: PoseResult) -> PoseResult:
    world = None if p.world is None else swap_left_right(p.world)
    return PoseResult(p.cam, p.t, swap_left_right(p.lm2d), swap_left_right(p.vis), world)


def _distance_to(tri: Triangulation, prev: np.ndarray) -> float:
    """Median distance of left/right body joints to the previous frame (inf if too few to compare)."""
    m = tri.ok & np.isfinite(prev).all(axis=1)
    m[:11] = False
    return float(np.median(np.linalg.norm(tri.points[m] - prev[m], axis=1))) if m.sum() >= 4 else np.inf


class Pipeline:
    def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
                 min_vis: float = 0.5, max_reproj_px: float = 15.0):
        self.cams = cams
        self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
        self._cleaner = Cleaner(cams)
        self._filter = OneEuroFilter() if smoothing else None
        self._prev: np.ndarray | None = None
        self._prev_t = -np.inf

    def process(self, t: float, a: PoseResult | None, b: PoseResult | None, skew_s: float | None = None) -> PipelineOutput:
        """t is unix seconds (the FrameResult clock)."""
        raw = {"A": a, "B": b}
        tri, swapped = None, None
        ca, cb = self.cams.get("A"), self.cams.get("B")
        if (a is not None and b is not None and a.detected and b.detected
                and ca is not None and cb is not None and ca.calibrated and cb.calibrated):
            tri = triangulate_pair(a, b, ca, cb, self.min_vis, self.max_reproj_px)
            if self._prev is not None and t - self._prev_t <= SWAP_HOLD_S:
                # Equal-height cameras can't see a left/right flip in reprojection; use continuity.
                best = _distance_to(tri, self._prev)
                for name in ("B", "A"):
                    a2, b2 = (a, _flipped(b)) if name == "B" else (_flipped(a), b)
                    tri2 = triangulate_pair(a2, b2, ca, cb, self.min_vis, self.max_reproj_px)
                    d = _distance_to(tri2, self._prev)
                    if d < SWAP_MARGIN * best:
                        best, swapped, a, b, tri = d, name, a2, b2, tri2
        usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
        joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
        if self._filter is not None:
            joints = self._filter(joints, t)
        if tracking == "3d":
            self._prev, self._prev_t = joints.copy(), t
        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
        return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped)
