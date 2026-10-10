"""Shared data types. FrameResult is the contract with the rest of the team."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

N_JOINTS = 33
LEFT_HIP, RIGHT_HIP = 23, 24

# MediaPipe pose landmark pairs drawn as bones. Odd indices are the person's left.
BONES = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
    (7, 0), (0, 8),
]


# person's-left landmark -> matching right landmark
LR_PAIRS = {1: 4, 2: 5, 3: 6, 7: 8, 9: 10, **{k: k + 1 for k in range(11, 32, 2)}}


def swap_left_right(arr: np.ndarray) -> np.ndarray:
    """Exchange left/right landmark rows (undoes a MediaPipe left/right flip)."""
    out = np.array(arr, copy=True)
    for left, right in LR_PAIRS.items():
        out[left], out[right] = arr[right], arr[left]
    return out


def to_json_array(a) -> list | None:
    """Array -> nested lists with NaN/inf as None (JSON has no NaN)."""
    if a is None:
        return None
    arr = np.asarray(a, dtype=float)
    return np.where(np.isfinite(arr), arr, None).tolist()


def from_json_array(x) -> np.ndarray | None:
    if x is None:
        return None
    return np.array(x, dtype=float)


@dataclass
class PoseResult:
    """One camera's MediaPipe output for one frame. lm2d is None when no person was found."""

    cam: str
    t: float  # time.monotonic() at frame grab
    lm2d: np.ndarray | None = None  # (33, 2) pixels
    vis: np.ndarray | None = None  # (33,) 0..1
    world: np.ndarray | None = None  # (33, 3) metres, hip-centred, camera-aligned axes

    @property
    def detected(self) -> bool:
        return self.lm2d is not None

    def to_dict(self) -> dict:
        return {
            "cam": self.cam,
            "t": self.t,
            "lm2d": to_json_array(self.lm2d),
            "vis": to_json_array(self.vis),
            "world": to_json_array(self.world),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PoseResult":
        return cls(d["cam"], d["t"], from_json_array(d.get("lm2d")), from_json_array(d.get("vis")), from_json_array(d.get("world")))


@dataclass
class FrameResult:
    t: float  # unix seconds, laptop clock
    joints: np.ndarray  # (33, 3) metres, mat frame, NaN where unknown
    conf: np.ndarray  # (33,) 0..1
    angles: dict[str, float] = field(default_factory=dict)
    com: tuple[float, float, float] | None = None
    cop: tuple[float, float] | None = None
    load_l_pct: float | None = None
    heel_l_pct: float | None = None
    heel_r_pct: float | None = None
    load_conf: float = 0.0
    on_mat: bool = False
    phase: str = "idle"
    rep: int = 0
    flags: list[str] = field(default_factory=list)
    tracking: str = "lost"  # "3d" | "single_cam_A" | "single_cam_B" | "lost"

    def to_dict(self) -> dict:
        return {
            "t": self.t,
            "joints": to_json_array(self.joints),
            "conf": to_json_array(self.conf),
            "angles": dict(self.angles),
            "com": None if self.com is None else list(self.com),
            "cop": None if self.cop is None else list(self.cop),
            "load_l_pct": self.load_l_pct,
            "heel_l_pct": self.heel_l_pct,
            "heel_r_pct": self.heel_r_pct,
            "load_conf": self.load_conf,
            "on_mat": self.on_mat,
            "phase": self.phase,
            "rep": self.rep,
            "flags": list(self.flags),
            "tracking": self.tracking,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FrameResult":
        return cls(
            t=d["t"],
            joints=from_json_array(d["joints"]),
            conf=from_json_array(d["conf"]),
            angles=d.get("angles") or {},
            com=None if d.get("com") is None else tuple(d["com"]),
            cop=None if d.get("cop") is None else tuple(d["cop"]),
            load_l_pct=d.get("load_l_pct"),
            heel_l_pct=d.get("heel_l_pct"),
            heel_r_pct=d.get("heel_r_pct"),
            load_conf=d.get("load_conf", 0.0),
            on_mat=d.get("on_mat", False),
            phase=d.get("phase", "idle"),
            rep=d.get("rep", 0),
            flags=d.get("flags") or [],
            tracking=d.get("tracking", "lost"),
        )
