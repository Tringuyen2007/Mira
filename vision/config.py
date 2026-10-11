"""Load and save config/mat.yaml and config/cameras.yaml."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
MAT_PATH = CONFIG_DIR / "mat.yaml"
CAMERAS_PATH = CONFIG_DIR / "cameras.yaml"

# Marker id -> (sign of x, sign of z). +x is the patient's left, +z is toward Mira.
_MARKER_SIGNS = {0: (1, 1), 1: (-1, 1), 2: (1, -1), 3: (-1, -1)}


@dataclass
class MatConfig:
    length_m: float = 1.73
    width_m: float = 0.61
    marker_size_m: float = 0.17
    marker_inset_m: float = 0.02
    aruco_dictionary: str = "DICT_4X4_50"
    marker_rotation_deg: int = 0  # every sheet turned in place, clockwise seen from above with Mira at the top
    camera_targets: dict[str, np.ndarray] = field(default_factory=dict)
    aim_point: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.8, 0.0]))
    tol_position_m: float = 0.10
    tol_angle_deg: float = 5.0

    def marker_centers(self) -> dict[int, np.ndarray]:
        off = self.marker_inset_m + self.marker_size_m / 2
        cx, cz = self.width_m / 2 - off, self.length_m / 2 - off
        return {i: np.array([sx * cx, 0.0, sz * cz]) for i, (sx, sz) in _MARKER_SIGNS.items()}

    def marker_corners(self) -> dict[int, np.ndarray]:
        """(4, 3) corners per marker in ArUco order TL, TR, BR, BL.

        Seen from above with +z (Mira) at the top, +x points left, so an
        upright marker's top-left corner is at (+h, +h) in (x, z) from its centre.
        marker_rotation_deg turns every marker in place (clockwise as drawn that way).
        """
        h = self.marker_size_m / 2
        a = np.radians(self.marker_rotation_deg)
        c, s = np.cos(a), np.sin(a)
        offsets = np.array([[x * c - z * s, 0.0, x * s + z * c] for x, z in [(h, h), (-h, h), (-h, -h), (h, -h)]])
        return {i: c + offsets for i, c in self.marker_centers().items()}

    def mat_polygon(self) -> np.ndarray:
        """Mat outline as (x, z) corners."""
        w, l = self.width_m / 2, self.length_m / 2
        return np.array([[w, l], [-w, l], [-w, -l], [w, -l]])


def mat_raw(path: Path = MAT_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_mat(path: Path = MAT_PATH) -> MatConfig:
    raw = mat_raw(path)
    return MatConfig(
        length_m=raw["length_m"],
        width_m=raw["width_m"],
        marker_size_m=raw["marker_size_m"],
        marker_inset_m=raw["marker_inset_m"],
        aruco_dictionary=raw["aruco_dictionary"],
        marker_rotation_deg=int(raw.get("marker_rotation_deg", 0)),
        camera_targets={k: np.array(v["eye"], float) for k, v in raw["camera_targets"].items()},
        aim_point=np.array(raw["aim_point"], float),
        tol_position_m=raw["placement_tolerance"]["position_m"],
        tol_angle_deg=raw["placement_tolerance"]["angle_deg"],
    )


@dataclass
class CameraConfig:
    name: str
    index: int
    hint: str = ""
    width: int = 1280
    height: int = 720
    K: np.ndarray | None = None
    dist: np.ndarray | None = None
    zoom_std: float | None = None  # relative std of K's focal length; None = assumed, not measured
    R: np.ndarray | None = None
    t: np.ndarray | None = None
    extrinsics_source: str | None = None
    extrinsics_rms: float | None = None
    marker_obs: dict[int, np.ndarray] | None = None
    target_eye: np.ndarray | None = None  # locked placement target (mat frame), see vision.placement
    target_aim: np.ndarray | None = None  # point the camera looked at when locked

    @property
    def has_intrinsics(self) -> bool:
        return self.K is not None and self.dist is not None

    @property
    def calibrated(self) -> bool:
        return self.has_intrinsics and self.R is not None and self.t is not None

    def to_dict(self) -> dict:
        d: dict = {"index": int(self.index), "hint": self.hint, "width": int(self.width), "height": int(self.height)}
        if self.has_intrinsics:
            d["K"] = np.asarray(self.K, float).tolist()
            d["dist"] = np.asarray(self.dist, float).ravel().tolist()
            d["zoom_std"] = None if self.zoom_std is None else float(self.zoom_std)
        if self.R is not None and self.t is not None:
            d["R"] = np.asarray(self.R, float).tolist()
            d["t"] = np.asarray(self.t, float).ravel().tolist()
            d["extrinsics_source"] = self.extrinsics_source
            d["extrinsics_rms"] = None if self.extrinsics_rms is None else float(self.extrinsics_rms)
        if self.marker_obs:
            d["marker_obs"] = {int(k): np.asarray(v, float).tolist() for k, v in self.marker_obs.items()}
        if self.target_eye is not None:
            d["target_eye"] = np.asarray(self.target_eye, float).ravel().tolist()
        if self.target_aim is not None:
            d["target_aim"] = np.asarray(self.target_aim, float).ravel().tolist()
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "CameraConfig":
        def arr(key):
            return None if d.get(key) is None else np.array(d[key], float)

        obs = d.get("marker_obs")
        return cls(
            name=name,
            index=int(d["index"]),
            hint=d.get("hint", ""),
            width=int(d.get("width", 1280)),
            height=int(d.get("height", 720)),
            K=arr("K"),
            dist=arr("dist"),
            zoom_std=d.get("zoom_std"),
            R=arr("R"),
            t=arr("t"),
            extrinsics_source=d.get("extrinsics_source"),
            extrinsics_rms=d.get("extrinsics_rms"),
            marker_obs=None if not obs else {int(k): np.array(v, float) for k, v in obs.items()},
            target_eye=arr("target_eye"),
            target_aim=arr("target_aim"),
        )


def load_cameras(path: Path = CAMERAS_PATH) -> dict[str, CameraConfig]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return {name: CameraConfig.from_dict(name, d) for name, d in (raw.get("cameras") or {}).items()}


def save_cameras(cams: dict[str, CameraConfig], path: Path = CAMERAS_PATH) -> Path | None:
    """Write cameras.yaml, first copying any existing file into backups/. Returns the backup path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backups = path.parent / "backups"
        backups.mkdir(exist_ok=True)
        backup = backups / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S-%f}{path.suffix}"
        shutil.copy2(path, backup)
    data = {"cameras": {n: c.to_dict() for n, c in sorted(cams.items())}}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return backup
