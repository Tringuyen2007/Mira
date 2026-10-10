"""Floor-marker detection and camera pose from markers."""
from __future__ import annotations

import cv2
import numpy as np

from vision.config import MatConfig


def aruco_dictionary(name: str = "DICT_4X4_50"):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


class MarkerDetector:
    def __init__(self, mat: MatConfig):
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(aruco_dictionary(mat.aruco_dictionary), params)
        self._ids = set(mat.marker_corners())

    def detect(self, image: np.ndarray) -> dict[int, np.ndarray]:
        """Mat marker id -> (4, 2) pixel corners in ArUco order TL, TR, BR, BL."""
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2).astype(float) for c, i in zip(corners, ids.ravel()) if int(i) in self._ids}
