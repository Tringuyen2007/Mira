"""Small OpenCV drawing helpers shared by the tools."""
from __future__ import annotations

import cv2
import numpy as np

from vision.types import BONES

GREEN = (80, 220, 80)
MAGENTA = (255, 0, 255)
RED = (60, 60, 255)
WHITE = (255, 255, 255)
YELLOW = (0, 220, 255)
LEFT_COLOR = (0, 165, 255)  # orange: the person's left
RIGHT_COLOR = (255, 200, 0)  # blue: the person's right
CENTER_COLOR = (200, 200, 200)


def bone_color(i: int, j: int):
    if i % 2 == 1 and j % 2 == 1:
        return LEFT_COLOR
    if i % 2 == 0 and j % 2 == 0 and i and j:
        return RIGHT_COLOR
    return CENTER_COLOR


def pt(p) -> tuple[int, int]:
    return int(np.clip(round(p[0]), -10000, 10000)), int(np.clip(round(p[1]), -10000, 10000))


def draw_skeleton(img, pts, ok=None, color=None, thickness: int = 2, radius: int = 3) -> None:
    pts = np.asarray(pts, float)
    good = np.isfinite(pts).all(axis=1)
    if ok is not None:
        good &= np.asarray(ok, bool)
    for i, j in BONES:
        if good[i] and good[j]:
            cv2.line(img, pt(pts[i]), pt(pts[j]), color or bone_color(i, j), thickness, cv2.LINE_AA)
    for k in np.flatnonzero(good):
        if k == 0 or k >= 11:
            cv2.circle(img, pt(pts[k]), radius, color or bone_color(k, k), -1, cv2.LINE_AA)


def draw_points(img, pts, color, radius: int = 3) -> None:
    for p in np.asarray(pts, float):
        if np.isfinite(p).all():
            cv2.circle(img, pt(p), radius, color, -1, cv2.LINE_AA)


def draw_text(img, text: str, org, color=WHITE, scale: float = 0.5) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def fit(img, size) -> np.ndarray:
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)
