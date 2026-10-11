"""Per-camera pose results -> FrameResult in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vision.anchor import FootAnchor
from vision.cleanup import Cleaner
from vision.config import CameraConfig, MatConfig
from vision.filters import OneEuroFilter
from vision.frames import world_to_mat
from vision.triangulate import Triangulation, triangulate_pair
from vision.select import PersonSelector
from vision.mono import mono_joints
from vision.tracker import ModelTracker
from vision.types import LEFT_HIP, RIGHT_HIP, CameraPeople, FrameResult, PoseResult, swap_left_right

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
    people: dict[str, list[PoseResult]] | None = None  # everyone found per camera (multi-person input)
    ignored: dict[str, list[PoseResult]] | None = None  # people not tracked, per camera
    dropped: str | None = None  # camera ignored this frame because it disagreed with the other
    feet_locked: dict[str, bool] | None = None  # "l"/"r" -> foot held by the FootAnchor this frame
    tracker: str = "triangulate"  # which tracker produced frame.joints ("model" or "triangulate")
    fit_ms: float | None = None  # model fit time this frame


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


def _as_people(x) -> list[PoseResult] | None:
    """A camera's input as a list of people, or None for a plain single PoseResult/None (legacy)."""
    if isinstance(x, CameraPeople):
        return list(x.people)
    if isinstance(x, list):
        return x
    return None


def _evidence(views: dict[str, PoseResult | None]) -> np.ndarray:
    """Per-joint confidence for model output: the best MediaPipe visibility across the cameras."""
    vis = [v.vis for v in views.values() if v is not None and v.detected]
    return np.max(vis, axis=0) if vis else np.zeros(33)


class Pipeline:
    def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
                 min_vis: float = 0.5, max_reproj_px: float = 15.0, mat: MatConfig | None = None,
                 anchor_feet: bool = True, tracker: str = "auto", height_m: float | None = None):
        """tracker: "model" fits a body to both cameras (falls back to triangulation while the body is
        being measured or when a fit fails); "triangulate" is the triangulate-and-clean pipeline."""
        self.cams = cams
        calibrated = [n for n, c in cams.items() if c is not None and c.calibrated]
        self._single = calibrated[0] if len(calibrated) == 1 else None  # one camera: 3D from floor + MediaPipe
        if tracker == "auto":
            tracker = "model" if self._single else "triangulate"
        self._model = ModelTracker(cams, height_m=height_m) if tracker == "model" else None
        self._last_xz = None
        self._anchor = FootAnchor() if anchor_feet else None
        self._selector = PersonSelector(cams, mat or MatConfig())
        self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
        self._cleaner = Cleaner(cams)
        self._filter = OneEuroFilter() if smoothing else None

    def process(self, t: float, a, b, skew_s: float | None = None) -> PipelineOutput:
        """t is unix seconds (the FrameResult clock).

        a, b: per camera a PoseResult (one person, as in old recordings), a CameraPeople or list of
        PoseResults (everyone found; the person at the mat is selected first), or None.
        """
        pa, pb = _as_people(a), _as_people(b)
        people = ignored = dropped = None
        if pa is not None or pb is not None:
            pa = pa if pa is not None else ([a] if isinstance(a, PoseResult) else [])
            pb = pb if pb is not None else ([b] if isinstance(b, PoseResult) else [])
            sel = self._selector.select(t, pa, pb)
            people, ignored, dropped, a, b = {"A": pa, "B": pb}, sel.ignored, sel.dropped, sel.a, sel.b
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
        if self._single is not None:  # one camera: shape from MediaPipe's 3D, position from the feet on the floor
            pose = {"A": a, "B": b}.get(self._single)
            scale = self._model.world_scale if self._model is not None else 1.0
            upright = self._model.upright if self._model is not None else None
            mono = (mono_joints(pose, self.cams[self._single], scale, self._last_xz, upright)
                    if pose is not None else None)
            if mono is None:
                joints, conf, tracking = np.full((33, 3), np.nan), np.zeros(33), "lost"
                self._last_xz = None  # lost: a new person or session must not be held at the old spot
            else:
                joints, conf, tracking = mono, pose.vis.copy(), f"single_cam_{self._single}"
                self._last_xz = ((mono[27] + mono[28]) / 2)[[0, 2]]  # what mono_joints holds when feet hide
        else:
            usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
            joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
        feet_locked = None
        if self._anchor is not None:
            if tracking == "lost":
                self._anchor.reset()
            else:
                joints, feet_locked = self._anchor.update(t, joints)
        used, fit_ms = "triangulate", None
        fitted = None
        if self._model is not None:
            anchors = {s: joints[a] for s, a in (("l", 27), ("r", 28)) if (feet_locked or {}).get(s)}
            fitted = self._model.update(t, joints, tracking, {"A": a, "B": b}, anchors)
            fit_ms = self._model.last_ms if fitted is not None else None
        if fitted is not None:
            joints, used = fitted, "model"
            conf = np.maximum(conf, _evidence({"A": a, "B": b}))
        if used != "model" and self._filter is not None:  # the model smooths its own pose numbers
            joints = self._filter(joints, t)
        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
        return PipelineOutput(frame=frame, raw=raw, tri=tri, skew_s=skew_s, swapped=swapped,
                              people=people, ignored=ignored, dropped=dropped, feet_locked=feet_locked,
                              tracker=used, fit_ms=fit_ms)
