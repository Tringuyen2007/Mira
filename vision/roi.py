"""The part of each camera's picture worth running pose on: the mat and the space around it.

Only pose detection is cropped; every view keeps showing the full picture with this area outlined.
"""
from __future__ import annotations

import math

import numpy as np

from vision.config import CameraConfig, MatConfig
from vision.frames import mat_to_cam, project

MIN_ROI_PX = 64


def mat_roi(cam: CameraConfig | None, mat: MatConfig, margin_m: float = 1.0, height_m: float = 2.1,
            pad: float = 0.10) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) full-frame pixels around the mat grown by margin_m, floor to height_m.

    None (= use the full frame) if the camera is uncalibrated or the box is not wholly in front of it.
    """
    if cam is None or not cam.calibrated:
        return None
    hw, hl = mat.width_m / 2 + margin_m, mat.length_m / 2 + margin_m
    base = np.array([[x, 0.0, z] for x in (-hw, hw) for z in (-hl, hl)])
    box = np.vstack([base, base + [0.0, height_m, 0.0]])
    if np.any(mat_to_cam(box, cam.R, cam.t)[:, 2] <= 0.05):
        return None
    uv = project(box, cam.K, cam.dist, cam.R, cam.t)
    (x0, y0), (x1, y1) = uv.min(axis=0), uv.max(axis=0)
    px, py = pad * (x1 - x0), pad * (y1 - y0)
    x0, y0 = max(0, math.floor(x0 - px)), max(0, math.floor(y0 - py))
    x1, y1 = min(cam.width, math.ceil(x1 + px)), min(cam.height, math.ceil(y1 + py))
    if x1 - x0 < MIN_ROI_PX or y1 - y0 < MIN_ROI_PX:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def crop(frame: np.ndarray, roi) -> tuple[np.ndarray, tuple[int, int]]:
    if roi is None:
        return frame, (0, 0)
    x0, y0, x1, y1 = roi
    return frame[y0:y1, x0:x1], (x0, y0)


def uncrop(lm2d: np.ndarray, offset) -> np.ndarray:
    return np.asarray(lm2d, float) + np.asarray(offset, float)
