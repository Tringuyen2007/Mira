# Vision: cameras → 3D skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two USB webcams → calibrated live 3D skeleton in the mat frame, with a debug viewer, sanity checks, and record/replay so Eric can judge tracking quality.

**Architecture:** A `vision/` Python package. Pure, unit-tested core (conventions, triangulation, cleanup, pairing, filtering, marker PnP, bundle adjustment, checks, record/replay) composed by a `Pipeline` that takes per-camera `PoseResult`s from either live threads (capture → MediaPipe) or a recording. Thin CLI tools (`python -m vision.<tool>`) drive the hardware.

**Tech Stack:** Python 3.11, OpenCV (contrib, `cv2.aruco`), MediaPipe Tasks Pose Landmarker, NumPy, SciPy (`least_squares`), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-vision-cameras-to-3d-design.md`

## Global Constraints

- Python 3.11 venv at `.venv` (`/opt/homebrew/bin/python3.11 -m venv .venv`); system Python is 3.14, which MediaPipe does not support.
- Mat frame: right-handed, origin mat centre on the mat surface, +y up, +z toward Mira, +x toward the patient's left (= Mira's right), the ID 0/2 side.
- Extrinsics map mat → camera: `X_cam = R @ X_mat + t`, OpenCV camera axes (x right, y down, z forward).
- Mat 1.73 m × 0.61 m; markers `DICT_4X4_50`, 0.17 m, inset 0.02 m; ID0 (+0.20, +0.76), ID1 (−0.20, +0.76), ID2 (+0.20, −0.76), ID3 (−0.20, −0.76); ArUco corner order TL, TR, BR, BL = (+h,+h), (−h,+h), (−h,−h), (+h,−h) relative to centre in (x, z).
- Target cameras: A (+1.98, 0.85, +1.98), B (−1.98, 0.85, +1.98), aimed at (0, 0.8, 0); OK within ±10 cm and ±5°.
- Capture request: 1280×720, MJPG, 30 fps; timestamps `time.monotonic()` at grab; keep only the latest frame.
- Triangulation: skip joints with vis < 0.5 in either view; reject reprojection error > 15 px; pair skew limit 33 ms; hold last good value ≤ 0.3 s.
- One Euro defaults: min_cutoff 1.0, beta 0.007, d_cutoff 1.0.
- Bundle adjustment: `soft_l1`, `f_scale=2.0` px, marker residual weight 5, body joints with vis ≥ 0.8 in both views, ≤ 400 frames, drop frames with median initial reprojection > 40 px; refuse to save if body RMS doesn't improve, a camera moves > 0.5 m or rotates > 15°, or marker RMS after > max(2 × before, 1.0 px).
- Never save images or video. Config writes back up the previous `config/cameras.yaml` to `config/backups/`.
- All commands run from the repo root with the venv active (`source .venv/bin/activate`). Tests: `python -m pytest`.

## Review Focus

1. The camera silently delivers a different resolution than it was calibrated at (AVFoundation ignoring 1280×720) → every 3D point is wrong with no error. Expect a visible warning in the viewer HUD. Test: `resolution_warning` in Task 6.
2. MediaPipe swaps left/right in one camera → garbage 3D for limbs. Expect those joints rejected by reprojection, not triangulated into the wrong place. Test in Task 3.
3. A recording contains frames with no person (undetected raw results, NaN joints) → replay/retriangulate should not crash. Test in Task 5.
4. The person is barely visible during `calib_refine` (too few good observations) → expect a clear error and no config change. Test in Task 10.
5. Duplicate or out-of-order timestamps reach the One Euro filter (dt ≤ 0) → expect the previous output, not inf/NaN. Test in Task 2.

---

### Task 1: Environment, package, config, coordinate frames

**Files:**
- Create: `vision/__init__.py`, `vision/requirements.txt`, `pytest.ini`, `config/mat.yaml`, `vision/config.py`, `vision/frames.py`, `vision/tests/__init__.py`, `vision/tests/test_config.py`, `vision/tests/test_frames.py`
- Modify: `.gitignore`
- Delete: `vision/.gitkeep`

**Interfaces:**
- Produces:
  - `vision.config`: `REPO_ROOT`, `CONFIG_DIR`, `MAT_PATH`, `CAMERAS_PATH`; `MatConfig` (fields `length_m, width_m, marker_size_m, marker_inset_m, aruco_dictionary, camera_targets: dict[str, ndarray(3)], aim_point: ndarray(3), tol_position_m, tol_angle_deg`; methods `marker_centers() -> dict[int, ndarray(3)]`, `marker_corners() -> dict[int, ndarray(4,3)]`, `mat_polygon() -> ndarray(4,2)` of (x, z)); `load_mat(path=MAT_PATH) -> MatConfig`; `mat_raw(path=MAT_PATH) -> dict`; `CameraConfig` (fields `name, index, hint, width, height, K, dist, intrinsics_rms, R, t, extrinsics_source, extrinsics_rms, marker_obs: dict[int, ndarray(4,2)] | None`; properties `has_intrinsics`, `calibrated`; `to_dict()`, `from_dict(name, d)`); `load_cameras(path=CAMERAS_PATH) -> dict[str, CameraConfig]`; `save_cameras(cams, path=CAMERAS_PATH) -> Path | None` (returns backup path).
  - `vision.frames`: `mat_to_cam(X, R, t)`, `cam_center(R, t)`, `project(X, K, dist, R, t) -> (N,2)` (NaN rows stay NaN), `look_at_pose(eye, target, up=(0,1,0)) -> (R, t)`, `world_to_mat(world, R)`, `uncalibrated_world_to_mat(world)`, `rotation_angle_deg(R1, R2)`, `angle_between_deg(u, v)`.

- [ ] **Step 1: Create the venv and install dependencies**

`vision/requirements.txt`:
```
opencv-contrib-python>=4.9
mediapipe>=0.10.14
numpy
scipy>=1.11
pyyaml>=6
pytest>=8
```

Run:
```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r vision/requirements.txt
python -c "import cv2, mediapipe as mp; from mediapipe.tasks.python import vision; print(cv2.__version__, mp.__version__, vision.PoseLandmarker)"
```
Expected: versions print and `PoseLandmarker` resolves. If the import fails (MediaPipe 1.x API change) or pip reports a numpy conflict, pin `mediapipe==0.10.21` and `opencv-contrib-python>=4.9,<4.12` in `requirements.txt`, reinstall, and re-run the check.

- [ ] **Step 2: Scaffold files**

`vision/__init__.py`:
```python
"""Mira vision: cameras, calibration, 3D pose in the mat frame."""
```
`vision/tests/__init__.py`: empty file.

`pytest.ini`:
```ini
[pytest]
testpaths = vision/tests
pythonpath = .
```

Append to `.gitignore`:
```
# Python
.venv/
__pycache__/
*.pyc
.pytest_cache/

# Vision
vision/models/*.task
config/backups/
```

`config/mat.yaml`:
```yaml
# Mat frame: origin mat centre on the mat surface, +y up, +z toward Mira,
# +x toward the patient's left (Mira's right). Right-handed.
length_m: 1.73
width_m: 0.61
marker_size_m: 0.17
marker_inset_m: 0.02
aruco_dictionary: DICT_4X4_50
camera_targets:
  A: {eye: [1.98, 0.85, 1.98]}
  B: {eye: [-1.98, 0.85, 1.98]}
aim_point: [0.0, 0.8, 0.0]
placement_tolerance: {position_m: 0.10, angle_deg: 5.0}
```

Run: `git rm -q vision/.gitkeep`

- [ ] **Step 3: Write failing tests**

`vision/tests/test_config.py`:
```python
import numpy as np

from vision.config import CameraConfig, MatConfig, load_cameras, load_mat, save_cameras


def test_marker_centers_match_layout():
    c = MatConfig().marker_centers()
    np.testing.assert_allclose(c[0], [0.20, 0.0, 0.76], atol=1e-9)
    np.testing.assert_allclose(c[1], [-0.20, 0.0, 0.76], atol=1e-9)
    np.testing.assert_allclose(c[2], [0.20, 0.0, -0.76], atol=1e-9)
    np.testing.assert_allclose(c[3], [-0.20, 0.0, -0.76], atol=1e-9)


def test_marker_corners_order_and_size():
    corners = MatConfig().marker_corners()
    tl, tr, br, bl = corners[0]
    # TL is toward Mira (+z) and the patient's left (+x)
    assert tl[0] > tr[0] and tl[2] > bl[2]
    np.testing.assert_allclose(np.linalg.norm(tr - tl), 0.17)
    np.testing.assert_allclose(np.linalg.norm(bl - tl), 0.17)
    assert np.all(corners[0][:, 1] == 0.0)


def test_load_mat_reads_repo_file():
    mat = load_mat()
    assert mat.length_m == 1.73
    np.testing.assert_allclose(mat.camera_targets["A"], [1.98, 0.85, 1.98])
    np.testing.assert_allclose(mat.aim_point, [0.0, 0.8, 0.0])


def test_cameras_round_trip_and_backup(tmp_path):
    path = tmp_path / "cameras.yaml"
    assert load_cameras(path) == {}
    cam = CameraConfig(
        name="A", index=1, hint="1280x720",
        K=np.eye(3), dist=np.zeros(5), intrinsics_rms=0.3,
        R=np.eye(3), t=np.array([0.0, 0.0, 3.0]), extrinsics_source="markers", extrinsics_rms=0.8,
        marker_obs={0: np.arange(8.0).reshape(4, 2)},
    )
    assert save_cameras({"A": cam}, path) is None
    loaded = load_cameras(path)["A"]
    assert loaded.index == 1 and loaded.calibrated
    np.testing.assert_allclose(loaded.t, [0.0, 0.0, 3.0])
    np.testing.assert_allclose(loaded.marker_obs[0], cam.marker_obs[0])
    backup = save_cameras({"A": cam}, path)
    assert backup is not None and backup.exists()


def test_uncalibrated_camera_flags():
    cam = CameraConfig(name="B", index=0)
    assert not cam.has_intrinsics and not cam.calibrated
    assert CameraConfig.from_dict("B", cam.to_dict()).index == 0
```

`vision/tests/test_frames.py`:
```python
import numpy as np

from vision.frames import (
    cam_center, look_at_pose, mat_to_cam, project, rotation_angle_deg,
    uncalibrated_world_to_mat, world_to_mat,
)

K = np.array([[900.0, 0, 640], [0, 900, 360], [0, 0, 1]])


def test_look_at_pose_centre_and_axes():
    eye, target = np.array([1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0])
    R, t = look_at_pose(eye, target)
    np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(R), 1.0)
    np.testing.assert_allclose(cam_center(R, t), eye, atol=1e-12)
    c = mat_to_cam(target[None], R, t)[0]
    assert abs(c[0]) < 1e-9 and abs(c[1]) < 1e-9 and c[2] > 0
    # a point above the target is "up" in the image (negative camera y)
    assert mat_to_cam((target + [0, 0.5, 0])[None], R, t)[0, 1] < 0


def test_plus_x_is_miras_right():
    # Mira's viewpoint: in front of the mat, looking back along -z
    R, t = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([0.0, 0.8, 0.0]))
    assert mat_to_cam(np.array([[0.5, 0.8, 0.0]]), R, t)[0, 0] > 0


def test_project_target_hits_principal_point_and_nan_passthrough():
    R, t = look_at_pose(np.array([1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0]))
    px = project(np.array([[0.0, 0.8, 0.0], [np.nan, 0, 0]]), K, np.zeros(5), R, t)
    np.testing.assert_allclose(px[0], [640, 360], atol=1e-6)
    assert np.isnan(px[1]).all()


def test_world_to_mat_inverts_camera_rotation():
    R, _ = look_at_pose(np.array([-1.98, 0.85, 1.98]), np.array([0.0, 0.8, 0.0]))
    offsets = np.array([[0.1, 0.5, -0.2], [0.0, -0.9, 0.05]])
    world = offsets @ R.T  # what MediaPipe reports (camera-aligned axes)
    np.testing.assert_allclose(world_to_mat(world, R), offsets, atol=1e-12)


def test_uncalibrated_mapping_puts_head_up():
    # MediaPipe world: y down, so the head has negative y
    out = uncalibrated_world_to_mat(np.array([[0.0, -0.6, 0.0]]))
    assert out[0, 1] > 0


def test_rotation_angle():
    R1, _ = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([0.0, 0.8, 0.0]))
    R2, _ = look_at_pose(np.array([0.0, 0.8, 3.0]), np.array([np.tan(np.radians(10)) * 3, 0.8, 0.0]))
    assert abs(rotation_angle_deg(R1, R2) - 10.0) < 1e-6
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_config.py vision/tests/test_frames.py -q`
Expected: FAIL / errors with `ModuleNotFoundError: No module named 'vision.config'`.

- [ ] **Step 5: Implement `vision/config.py`**

```python
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

        Seen from above with +z (Mira) at the top, +x points left, so a
        marker's top-left corner is at (+h, +h) in (x, z) from its centre.
        """
        h = self.marker_size_m / 2
        offsets = np.array([[h, 0, h], [-h, 0, h], [-h, 0, -h], [h, 0, -h]])
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
    intrinsics_rms: float | None = None
    R: np.ndarray | None = None
    t: np.ndarray | None = None
    extrinsics_source: str | None = None
    extrinsics_rms: float | None = None
    marker_obs: dict[int, np.ndarray] | None = None

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
            d["intrinsics_rms"] = None if self.intrinsics_rms is None else float(self.intrinsics_rms)
        if self.R is not None and self.t is not None:
            d["R"] = np.asarray(self.R, float).tolist()
            d["t"] = np.asarray(self.t, float).ravel().tolist()
            d["extrinsics_source"] = self.extrinsics_source
            d["extrinsics_rms"] = None if self.extrinsics_rms is None else float(self.extrinsics_rms)
        if self.marker_obs:
            d["marker_obs"] = {int(k): np.asarray(v, float).tolist() for k, v in self.marker_obs.items()}
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
            intrinsics_rms=d.get("intrinsics_rms"),
            R=arr("R"),
            t=arr("t"),
            extrinsics_source=d.get("extrinsics_source"),
            extrinsics_rms=d.get("extrinsics_rms"),
            marker_obs=None if not obs else {int(k): np.array(v, float) for k, v in obs.items()},
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
```

- [ ] **Step 6: Implement `vision/frames.py`**

```python
"""Coordinate conventions.

Mat frame: right-handed, origin mat centre on the mat surface, +y up, +z toward
Mira, +x toward the patient's left (Mira's right). Extrinsics map mat -> camera:
X_cam = R @ X_mat + t, with OpenCV camera axes (x right, y down, z forward).
MediaPipe world landmarks use camera-aligned axes too, so R.T maps them into the
mat frame.
"""
from __future__ import annotations

import cv2
import numpy as np


def mat_to_cam(X, R, t) -> np.ndarray:
    return np.asarray(X, float) @ np.asarray(R, float).T + np.ravel(t)


def cam_center(R, t) -> np.ndarray:
    return -np.asarray(R, float).T @ np.ravel(t)


def project(X, K, dist, R, t) -> np.ndarray:
    """Project mat-frame points to distorted pixels. Non-finite rows stay NaN."""
    X = np.asarray(X, float).reshape(-1, 3)
    out = np.full((len(X), 2), np.nan)
    ok = np.isfinite(X).all(axis=1)
    if ok.any():
        rvec, _ = cv2.Rodrigues(np.asarray(R, float))
        d = np.zeros(5) if dist is None else np.asarray(dist, float)
        px, _ = cv2.projectPoints(X[ok], rvec, np.asarray(t, float).reshape(3, 1), np.asarray(K, float), d)
        out[ok] = px.reshape(-1, 2)
    return out


def look_at_pose(eye, target, up=(0.0, 1.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """Extrinsics (R, t) of a camera at `eye` looking at `target`, image up = world up."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, np.asarray(up, float))
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return R, -R @ eye


def world_to_mat(world, R) -> np.ndarray:
    """Rotate camera-aligned offsets (MediaPipe world landmarks) into the mat frame."""
    return np.asarray(world, float) @ np.asarray(R, float)


def uncalibrated_world_to_mat(world) -> np.ndarray:
    """Without extrinsics: flip MediaPipe's y-down/z-away axes to y-up (a 180° turn about x)."""
    return np.asarray(world, float) * np.array([1.0, -1.0, -1.0])


def rotation_angle_deg(R1, R2) -> float:
    c = (np.trace(np.asarray(R1).T @ np.asarray(R2)) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def angle_between_deg(u, v) -> float:
    u, v = np.asarray(u, float), np.asarray(v, float)
    c = u @ v / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_config.py vision/tests/test_frames.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add .gitignore pytest.ini config/mat.yaml vision/
git commit -m "vision: venv, config loading, mat-frame conventions"
```

---

### Task 2: Data types, JSON helpers, One Euro filter

**Files:**
- Create: `vision/types.py`, `vision/filters.py`, `vision/tests/test_types.py`, `vision/tests/test_filters.py`

**Interfaces:**
- Produces:
  - `vision.types`: `N_JOINTS = 33`, `LEFT_HIP = 23`, `RIGHT_HIP = 24`, `BONES: list[tuple[int,int]]`, `to_json_array(a) -> list | None` (NaN → None), `from_json_array(x) -> ndarray | None`, `PoseResult(cam: str, t: float, lm2d: ndarray(33,2)|None, vis: ndarray(33)|None, world: ndarray(33,3)|None)` with `.detected`, `to_dict()`, `from_dict(d)`; `FrameResult` (contract fields; `joints (33,3)`, `conf (33,)`, `angles`, `com`, `cop`, `load_l_pct`, `heel_l_pct`, `heel_r_pct`, `load_conf`, `on_mat`, `phase`, `rep`, `flags`, `tracking`) with `to_dict()`, `from_dict(d)`.
  - `vision.filters`: `OneEuroFilter(min_cutoff=1.0, beta=0.007, d_cutoff=1.0)`; `__call__(x: ndarray, t: float) -> ndarray`; `reset()`.

- [ ] **Step 1: Write failing tests**

`vision/tests/test_types.py`:
```python
import json

import numpy as np

from vision.types import FrameResult, PoseResult, from_json_array, to_json_array


def test_json_array_nan_round_trip():
    a = np.array([[1.0, np.nan], [3.0, 4.0]])
    j = json.loads(json.dumps(to_json_array(a)))
    assert j[0][1] is None
    np.testing.assert_array_equal(from_json_array(j), a)
    assert to_json_array(None) is None and from_json_array(None) is None


def test_pose_result_round_trip():
    p = PoseResult("A", 12.5, np.ones((33, 2)), np.full(33, 0.9), np.zeros((33, 3)))
    q = PoseResult.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.cam == "A" and q.t == 12.5 and q.detected
    np.testing.assert_array_equal(q.lm2d, p.lm2d)
    empty = PoseResult.from_dict(json.loads(json.dumps(PoseResult("B", 1.0).to_dict())))
    assert not empty.detected and empty.world is None


def test_frame_result_round_trip_with_nan():
    joints = np.zeros((33, 3))
    joints[5] = np.nan
    f = FrameResult(t=100.0, joints=joints, conf=np.full(33, 0.5), tracking="3d", flags=["x"])
    g = FrameResult.from_dict(json.loads(json.dumps(f.to_dict(), allow_nan=False)))
    np.testing.assert_array_equal(g.joints, joints)
    assert g.tracking == "3d" and g.flags == ["x"] and g.com is None and g.phase == "idle"
```

`vision/tests/test_filters.py`:
```python
import numpy as np

from vision.filters import OneEuroFilter


def test_constant_input_stays_constant():
    f = OneEuroFilter()
    for k in range(30):
        out = f(np.array([1.5, -2.0]), k / 30)
    np.testing.assert_allclose(out, [1.5, -2.0])


def test_step_is_smoothed_then_converges():
    f = OneEuroFilter()
    f(np.array([0.0]), 0.0)
    first = f(np.array([1.0]), 1 / 30)[0]
    assert 0.0 < first < 1.0
    for k in range(2, 60):
        out = f(np.array([1.0]), k / 30)[0]
    assert out > 0.99


def test_nan_does_not_poison_other_coords_and_restarts():
    f = OneEuroFilter()
    f(np.array([0.0, 0.0]), 0.0)
    out = f(np.array([np.nan, 1.0]), 1 / 30)
    assert np.isnan(out[0]) and np.isfinite(out[1])
    out = f(np.array([5.0, 1.0]), 2 / 30)
    assert out[0] == 5.0


def test_non_increasing_time_returns_previous_output():
    f = OneEuroFilter()
    f(np.array([1.0]), 1.0)
    prev = f(np.array([2.0]), 1.1)
    same = f(np.array([9.0]), 1.1)
    np.testing.assert_array_equal(same, prev)
    assert np.isfinite(f(np.array([9.0]), 1.05)).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_types.py vision/tests/test_filters.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/types.py`**

```python
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
```

- [ ] **Step 4: Implement `vision/filters.py`**

```python
"""One Euro filter (Casiez et al. 2012), vectorised and NaN-aware."""
from __future__ import annotations

import math

import numpy as np


class OneEuroFilter:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x = None
        self._dx = None
        self._t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self._x is None:
            self._x, self._dx, self._t = x.copy(), np.zeros_like(x), t
            return x.copy()
        dt = t - self._t
        if dt <= 0:
            return self._x.copy()
        self._t = t
        prev = self._x
        dx_hat = self._alpha(self.d_cutoff, dt) * (x - prev) / dt + (1 - self._alpha(self.d_cutoff, dt)) * self._dx
        a = self._alpha(self.min_cutoff + self.beta * np.abs(dx_hat), dt)
        x_hat = a * x + (1 - a) * prev
        # a coordinate that was missing restarts at the raw value
        x_hat = np.where(np.isnan(prev) & ~np.isnan(x), x, x_hat)
        self._x = x_hat
        self._dx = np.where(np.isnan(dx_hat), 0.0, dx_hat)
        return x_hat.copy()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_types.py vision/tests/test_filters.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add vision/types.py vision/filters.py vision/tests/test_types.py vision/tests/test_filters.py
git commit -m "vision: FrameResult/PoseResult types and One Euro filter"
```

---

### Task 3: Triangulation (+ synthetic test helpers)

**Files:**
- Create: `vision/triangulate.py`, `vision/tests/synth.py`, `vision/tests/test_triangulate.py`

**Interfaces:**
- Consumes: `CameraConfig`, `project`, `cam_center`, `mat_to_cam`, `PoseResult`.
- Produces:
  - `vision.triangulate`: `Triangulation(points (N,3), reproj_err (N,), angle_deg (N,), ok (N,) bool)`; `triangulate_points(uv_a, uv_b, cam_a, cam_b) -> (X (N,3), err (N,), angle_deg (N,))`; `triangulate_pair(a: PoseResult, b: PoseResult, cam_a, cam_b, min_vis=0.5, max_reproj_px=15.0) -> Triangulation`.
  - `vision.tests.synth`: `K_DEFAULT`, `make_cam(name, eye, target=(0,0.8,0), dist=None, K=None) -> CameraConfig`, `target_cams(dist=None) -> {"A","B"}`, `standing_skeleton() -> (33,3)`, `pose_from_points(cam, X, t=0.0, vis=1.0, noise_px=0.0, rng=None) -> PoseResult`, `swap_left_right(arr) -> arr`.

- [ ] **Step 1: Write the synthetic helpers `vision/tests/synth.py`**

```python
"""Synthetic cameras and skeletons for tests."""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import look_at_pose, project
from vision.types import PoseResult

K_DEFAULT = np.array([[900.0, 0, 640], [0, 900, 360], [0, 0, 1]])

# Person standing at the mat centre facing Mira (+z); +x is their left.
_LEFT = {
    1: (0.02, 1.64, 0.08), 2: (0.035, 1.645, 0.075), 3: (0.05, 1.64, 0.07),
    7: (0.075, 1.62, 0.0), 9: (0.02, 1.55, 0.08),
    11: (0.18, 1.42, 0.0), 13: (0.22, 1.14, 0.0), 15: (0.24, 0.88, 0.02),
    17: (0.25, 0.80, 0.03), 19: (0.24, 0.79, 0.05), 21: (0.23, 0.82, 0.05),
    23: (0.10, 0.95, 0.0), 25: (0.11, 0.52, 0.02), 27: (0.11, 0.09, 0.0),
    29: (0.11, 0.04, -0.05), 31: (0.12, 0.02, 0.15),
}
_MIRROR = {1: 4, 2: 5, 3: 6, 7: 8, 9: 10, **{k: k + 1 for k in range(11, 32, 2)}}


def standing_skeleton() -> np.ndarray:
    X = np.zeros((33, 3))
    X[0] = (0.0, 1.60, 0.10)
    for left, p in _LEFT.items():
        X[left] = p
        X[_MIRROR[left]] = (-p[0], p[1], p[2])
    return X


def swap_left_right(arr: np.ndarray) -> np.ndarray:
    out = np.array(arr, copy=True)
    for left, right in _MIRROR.items():
        out[left], out[right] = arr[right], arr[left]
    return out


def make_cam(name, eye, target=(0.0, 0.8, 0.0), dist=None, K=None) -> CameraConfig:
    R, t = look_at_pose(np.asarray(eye, float), np.asarray(target, float))
    return CameraConfig(
        name=name, index=0,
        K=(K_DEFAULT if K is None else np.asarray(K, float)).copy(),
        dist=np.zeros(5) if dist is None else np.asarray(dist, float),
        R=R, t=t,
    )


def target_cams(dist=None) -> dict[str, CameraConfig]:
    return {"A": make_cam("A", (1.98, 0.85, 1.98), dist=dist), "B": make_cam("B", (-1.98, 0.85, 1.98), dist=dist)}


def pose_from_points(cam, X, t=0.0, vis=1.0, noise_px=0.0, rng=None) -> PoseResult:
    X = np.asarray(X, float)
    uv = project(X, cam.K, cam.dist, cam.R, cam.t)
    if noise_px:
        uv = uv + (rng or np.random.default_rng(0)).normal(0, noise_px, uv.shape)
    hip = (X[23] + X[24]) / 2
    world = (X - hip) @ cam.R.T
    v = np.full(len(X), float(vis)) if np.isscalar(vis) else np.asarray(vis, float)
    return PoseResult(cam=cam.name, t=t, lm2d=uv, vis=v, world=world)
```

- [ ] **Step 2: Write failing tests `vision/tests/test_triangulate.py`**

```python
import numpy as np

from vision.tests.synth import pose_from_points, standing_skeleton, swap_left_right, target_cams
from vision.triangulate import triangulate_pair, triangulate_points


def test_exact_recovery_without_distortion():
    cams, X = target_cams(), standing_skeleton()
    tri = triangulate_pair(pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert tri.ok.all()
    np.testing.assert_allclose(tri.points, X, atol=1e-6)
    assert np.nanmax(tri.reproj_err) < 1e-4


def test_recovery_with_lens_distortion():
    cams, X = target_cams(dist=[-0.12, 0.05, 0.0, 0.0, 0.0]), standing_skeleton()
    tri = triangulate_pair(pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    np.testing.assert_allclose(tri.points, X, atol=1e-3)


def test_pixel_noise_gives_bounded_error():
    cams, X = target_cams(), standing_skeleton()
    rng = np.random.default_rng(1)
    a = pose_from_points(cams["A"], X, noise_px=1.0, rng=rng)
    b = pose_from_points(cams["B"], X, noise_px=1.0, rng=rng)
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    assert np.nanmax(np.linalg.norm(tri.points - X, axis=1)) < 0.01


def test_low_visibility_is_skipped():
    cams, X = target_cams(), standing_skeleton()
    vis = np.ones(33)
    vis[15] = 0.2
    tri = triangulate_pair(pose_from_points(cams["A"], X, vis=vis), pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert not tri.ok[15] and np.isnan(tri.points[15]).all() and tri.ok[16]


def test_outlier_rejected_by_reprojection():
    cams, X = target_cams(), standing_skeleton()
    a = pose_from_points(cams["A"], X)
    a.lm2d[13] += [50.0, 0.0]
    tri = triangulate_pair(a, pose_from_points(cams["B"], X), cams["A"], cams["B"])
    assert not tri.ok[13] and tri.reproj_err[13] > 15


def test_left_right_swap_in_one_view_is_rejected():
    cams, X = target_cams(), standing_skeleton()
    b = pose_from_points(cams["B"], X)
    b.lm2d = swap_left_right(b.lm2d)
    tri = triangulate_pair(pose_from_points(cams["A"], X), b, cams["A"], cams["B"])
    assert tri.ok[0]  # nose is not swapped
    assert not tri.ok[[11, 12, 15, 16, 27, 28]].any()


def test_triangulation_angle_near_ninety_at_centre():
    cams = target_cams()
    X = np.array([[0.0, 0.8, 0.0]])
    uv_a = pose_from_points(cams["A"], np.repeat(X, 33, 0)).lm2d[:1]
    uv_b = pose_from_points(cams["B"], np.repeat(X, 33, 0)).lm2d[:1]
    _, _, angle = triangulate_points(uv_a, uv_b, cams["A"], cams["B"])
    assert 85 < angle[0] < 95
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_triangulate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'vision.triangulate'`.

- [ ] **Step 4: Implement `vision/triangulate.py`**

```python
"""Two-view triangulation in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vision.config import CameraConfig
from vision.frames import cam_center, mat_to_cam, project
from vision.types import PoseResult


@dataclass
class Triangulation:
    points: np.ndarray  # (N, 3) mat frame, NaN where not accepted
    reproj_err: np.ndarray  # (N,) worst of the two views in px, NaN where not attempted
    angle_deg: np.ndarray  # (N,) angle between the two viewing rays
    ok: np.ndarray  # (N,) bool


def _undistort(uv: np.ndarray, cam: CameraConfig) -> np.ndarray:
    return cv2.undistortPoints(uv.reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K).reshape(-1, 2)


def _projection_matrix(cam: CameraConfig) -> np.ndarray:
    return cam.K @ np.hstack([cam.R, np.asarray(cam.t, float).reshape(3, 1)])


def triangulate_points(uv_a, uv_b, cam_a: CameraConfig, cam_b: CameraConfig):
    """Triangulate matching pixel observations. Returns (X, reprojection error, ray angle)."""
    uv_a = np.asarray(uv_a, float).reshape(-1, 2)
    uv_b = np.asarray(uv_b, float).reshape(-1, 2)
    n = len(uv_a)
    if n == 0:
        return np.empty((0, 3)), np.empty(0), np.empty(0)
    ua, ub = _undistort(uv_a, cam_a), _undistort(uv_b, cam_b)
    Xh = cv2.triangulatePoints(_projection_matrix(cam_a), _projection_matrix(cam_b), ua.T, ub.T)
    with np.errstate(invalid="ignore", divide="ignore"):
        X = (Xh[:3] / Xh[3]).T
        err_a = np.linalg.norm(project(X, cam_a.K, cam_a.dist, cam_a.R, cam_a.t) - uv_a, axis=1)
        err_b = np.linalg.norm(project(X, cam_b.K, cam_b.dist, cam_b.R, cam_b.t) - uv_b, axis=1)
        ra = X - cam_center(cam_a.R, cam_a.t)
        rb = X - cam_center(cam_b.R, cam_b.t)
        cos = np.sum(ra * rb, axis=1) / (np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1))
    return X, np.maximum(err_a, err_b), np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def triangulate_pair(
    a: PoseResult, b: PoseResult, cam_a: CameraConfig, cam_b: CameraConfig,
    min_vis: float = 0.5, max_reproj_px: float = 15.0,
) -> Triangulation:
    X, err, angle = triangulate_points(a.lm2d, b.lm2d, cam_a, cam_b)
    vis_ok = (a.vis >= min_vis) & (b.vis >= min_vis)
    with np.errstate(invalid="ignore"):
        in_front = (mat_to_cam(X, cam_a.R, cam_a.t)[:, 2] > 0) & (mat_to_cam(X, cam_b.R, cam_b.t)[:, 2] > 0)
        ok = vis_ok & in_front & np.isfinite(err) & (err <= max_reproj_px)
    return Triangulation(
        points=np.where(ok[:, None], X, np.nan),
        reproj_err=np.where(vis_ok, err, np.nan),
        angle_deg=np.where(vis_ok, angle, np.nan),
        ok=ok,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_triangulate.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add vision/triangulate.py vision/tests/synth.py vision/tests/test_triangulate.py
git commit -m "vision: two-view triangulation with reprojection rejection"
```

---

### Task 4: Cleanup, pairing, pipeline

**Files:**
- Create: `vision/cleanup.py`, `vision/pairing.py`, `vision/pipeline.py`, `vision/tests/test_cleanup.py`, `vision/tests/test_pairing.py`, `vision/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Triangulation`, `triangulate_pair`, `OneEuroFilter`, `world_to_mat`, `uncalibrated_world_to_mat`, `PoseResult`, `FrameResult`, `CameraConfig`.
- Produces:
  - `vision.cleanup`: `HELD_CONF = 0.3`; `Cleaner(cams, hold_s=0.3)`; `update(t, tri: Triangulation|None, poses: dict[str, PoseResult|None]) -> (joints (33,3), conf (33,), tracking: str)`.
  - `vision.pairing`: `Pair(t, a: PoseResult|None, b: PoseResult|None, skew_s: float|None)` (a is always camera "A"'s result); `Pairer(anchor="A", other="B"|None, max_skew_s=0.033, wait_s=0.1, stale_s=0.5)` with `add(r) -> list[Pair]`; `SkewStats(n=300)` with `add(pair)`, `summary() -> {"median_ms","p95_ms","unpaired_pct"}`.
  - `vision.pipeline`: `MIN_3D_JOINTS = 8`; `PipelineOutput(frame: FrameResult, raw: dict[str, PoseResult|None], tri: Triangulation|None = None, skew_s: float|None = None)`; `Pipeline(cams, smoothing=True, min_vis=0.5, max_reproj_px=15.0)` with `process(t_unix, a, b, skew_s=None) -> PipelineOutput`.

- [ ] **Step 1: Write failing tests**

`vision/tests/test_cleanup.py`:
```python
import numpy as np

from vision.cleanup import HELD_CONF, Cleaner
from vision.config import CameraConfig
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.triangulate import Triangulation, triangulate_pair


def _setup():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    return cams, X, a, b


def test_all_triangulated_is_3d():
    cams, X, a, b = _setup()
    a.vis[:] = 0.9
    b.vis[:] = 0.7
    joints, conf, tracking = Cleaner(cams).update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    assert tracking == "3d"
    np.testing.assert_allclose(joints, X, atol=1e-6)
    np.testing.assert_allclose(conf, 0.7)


def test_failed_joint_falls_back_to_world_landmarks():
    cams, X, a, b = _setup()
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    tri.ok[15] = False
    tri.points[15] = np.nan
    joints, conf, _ = Cleaner(cams).update(0.0, tri, {"A": a, "B": b})
    np.testing.assert_allclose(joints[15], X[15], atol=1e-6)
    assert conf[15] == 1.0


def test_hold_last_good_then_expire():
    cams, X, a, b = _setup()
    c = Cleaner(cams)
    c.update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    a.world = b.world = None  # no single-view fallback available
    tri = triangulate_pair(a, b, cams["A"], cams["B"])
    tri.ok[15] = False
    tri.points[15] = np.nan
    joints, conf, _ = c.update(0.2, tri, {"A": a, "B": b})
    np.testing.assert_allclose(joints[15], X[15], atol=1e-6)
    assert conf[15] == HELD_CONF
    joints, conf, _ = c.update(0.6, tri, {"A": a, "B": b})
    assert np.isnan(joints[15]).all() and conf[15] == 0.0


def test_single_camera_anchored_at_last_hip_with_feet_on_floor():
    cams, X, a, b = _setup()
    X = X + [0.1, 0.0, -0.2]
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    c = Cleaner(cams)
    c.update(0.0, triangulate_pair(a, b, cams["A"], cams["B"]), {"A": a, "B": b})
    joints, _, tracking = c.update(0.5, None, {"A": None, "B": b})
    assert tracking == "single_cam_B"
    np.testing.assert_allclose(joints[:, [0, 2]], X[:, [0, 2]], atol=1e-6)
    feet = [27, 28, 29, 30, 31, 32]
    assert abs(np.min(joints[feet, 1])) < 1e-9
    np.testing.assert_allclose(joints[:, 1], X[:, 1] - X[feet, 1].min(), atol=1e-6)


def test_uncalibrated_single_camera_has_head_up():
    cams, X, a, _ = _setup()
    raw = {"A": CameraConfig(name="A", index=0)}
    joints, _, tracking = Cleaner(raw).update(0.0, None, {"A": a, "B": None})
    assert tracking == "single_cam_A"
    assert joints[0, 1] > joints[27, 1]


def test_nobody_is_lost():
    cams, *_ = _setup()
    joints, conf, tracking = Cleaner(cams).update(0.0, None, {"A": None, "B": None})
    assert tracking == "lost" and np.isnan(joints).all() and not conf.any()
```

`vision/tests/test_pairing.py`:
```python
from vision.pairing import Pairer, SkewStats
from vision.types import PoseResult


def r(cam, t):
    return PoseResult(cam, t)


def test_pairs_nearest_within_skew():
    p = Pairer()
    assert p.add(r("A", 0.0)) == []
    out = p.add(r("B", 0.010))
    assert len(out) == 1 and out[0].a.t == 0.0 and out[0].b.t == 0.010
    assert abs(out[0].skew_s - 0.010) < 1e-12


def test_uses_earlier_partner_when_nearest():
    p = Pairer()
    p.add(r("B", -0.005))
    assert p.add(r("A", 0.0)) == []
    out = p.add(r("B", 0.028))
    assert out[0].b.t == -0.005


def test_skew_too_large_emits_anchor_alone_once():
    p = Pairer()
    p.add(r("A", 0.0))
    out = p.add(r("B", 0.050))
    assert len(out) == 1 and out[0].b is None and out[0].a.t == 0.0
    assert p.add(r("B", 0.060)) == []


def test_anchor_waits_then_gives_up_when_other_is_silent():
    p = Pairer()
    p.add(r("A", 0.0))
    out = p.add(r("A", 0.2))
    assert [x.a.t for x in out] == [0.0] and out[0].b is None


def test_other_passes_through_when_anchor_dead():
    p = Pairer()
    out = p.add(r("B", 1.0))
    assert len(out) == 1 and out[0].a is None and out[0].b.t == 1.0


def test_single_camera_mode_emits_immediately():
    p = Pairer(anchor="B", other=None)
    out = p.add(r("B", 0.0))
    assert len(out) == 1 and out[0].b.t == 0.0 and out[0].a is None


def test_skew_stats():
    s = SkewStats()
    p = Pairer()
    p.add(r("A", 0.0))
    for pair in p.add(r("B", 0.02)):
        s.add(pair)
    summary = s.summary()
    assert abs(summary["median_ms"] - 20) < 1e-6 and summary["unpaired_pct"] == 0
```

`vision/tests/test_pipeline.py`:
```python
import numpy as np

from vision.config import CameraConfig
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_calibrated_pair_gives_3d():
    cams, X = target_cams(), standing_skeleton()
    out = Pipeline(cams).process(1000.0, pose_from_points(cams["A"], X), pose_from_points(cams["B"], X), 0.004)
    assert out.frame.tracking == "3d" and out.frame.t == 1000.0
    np.testing.assert_allclose(out.frame.joints, X, atol=1e-6)
    assert out.tri is not None and out.skew_s == 0.004 and out.raw["A"].cam == "A"


def test_uncalibrated_runs_single_camera():
    cams, X = target_cams(), standing_skeleton()
    a = pose_from_points(cams["A"], X)
    out = Pipeline({"A": CameraConfig(name="A", index=0)}).process(1.0, a, None)
    assert out.frame.tracking == "single_cam_A" and out.tri is None


def test_too_few_triangulated_joints_falls_back_to_single():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    b.lm2d = b.lm2d + 200.0  # wildly inconsistent second view
    out = Pipeline(cams).process(1.0, a, b)
    assert out.frame.tracking.startswith("single_cam") and out.tri is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_cleanup.py vision/tests/test_pairing.py vision/tests/test_pipeline.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/cleanup.py`**

```python
"""Per-joint fallbacks after triangulation and the `tracking` label."""
from __future__ import annotations

import numpy as np

from vision.config import CameraConfig
from vision.frames import uncalibrated_world_to_mat, world_to_mat
from vision.triangulate import Triangulation
from vision.types import LEFT_HIP, N_JOINTS, RIGHT_HIP, PoseResult

HELD_CONF = 0.3  # held joints: below the usual 0.5 gate so consumers can treat them as unreliable
FALLBACK_MIN_VIS = 0.5
ANCHOR_HOLD_S = 2.0
FOOT_JOINTS = [27, 28, 29, 30, 31, 32]


def _hip_mid(points: np.ndarray) -> np.ndarray | None:
    h = (points[LEFT_HIP] + points[RIGHT_HIP]) / 2
    return h if np.isfinite(h).all() else None


class Cleaner:
    def __init__(self, cams: dict[str, CameraConfig], hold_s: float = 0.3):
        self.cams = cams
        self.hold_s = hold_s
        self._last = np.full((N_JOINTS, 3), np.nan)
        self._last_t = np.full(N_JOINTS, -np.inf)
        self._hip: np.ndarray | None = None
        self._hip_t = -np.inf

    def update(self, t: float, tri: Triangulation | None, poses: dict[str, PoseResult | None]):
        seen = {n: p for n, p in poses.items() if p is not None and p.detected}
        if tri is not None and len(seen) >= 2:
            joints, conf = self._measure_3d(tri, seen)
            tracking = "3d"
            hip = _hip_mid(joints)
            if hip is not None:
                self._hip, self._hip_t = hip, t
        elif seen:
            name = max(seen, key=lambda n: float(np.mean(seen[n].vis)))
            joints, conf = self._measure_single(t, seen[name])
            tracking = f"single_cam_{name}"
        else:
            return np.full((N_JOINTS, 3), np.nan), np.zeros(N_JOINTS), "lost"

        measured = np.isfinite(joints).all(axis=1)
        self._last[measured] = joints[measured]
        self._last_t[measured] = t
        hold = ~measured & (t - self._last_t <= self.hold_s)
        joints[hold] = self._last[hold]
        conf[hold] = HELD_CONF
        return joints, conf, tracking

    def _measure_3d(self, tri: Triangulation, seen: dict[str, PoseResult]):
        a, b = seen["A"], seen["B"]
        joints = tri.points.copy()
        conf = np.where(tri.ok, np.minimum(a.vis, b.vis), 0.0)
        hip = _hip_mid(joints)
        if hip is None:
            return joints, conf
        views = [p for p in (a, b) if p.world is not None]
        for j in np.flatnonzero(~tri.ok):
            if not views:
                break
            best = max(views, key=lambda p: p.vis[j])
            if best.vis[j] < FALLBACK_MIN_VIS:
                continue
            offset = world_to_mat(best.world[j] - _hip_mid(best.world), self.cams[best.cam].R)
            joints[j] = hip + offset
            conf[j] = best.vis[j]
        return joints, conf

    def _measure_single(self, t: float, p: PoseResult):
        if p.world is None:
            return np.full((N_JOINTS, 3), np.nan), np.zeros(N_JOINTS)
        rel = p.world - _hip_mid(p.world)
        cam = self.cams.get(p.cam)
        body = world_to_mat(rel, cam.R) if cam is not None and cam.calibrated else uncalibrated_world_to_mat(rel)
        if self._hip is not None and t - self._hip_t <= ANCHOR_HOLD_S:
            ax, az = self._hip[0], self._hip[2]
        else:
            ax, az = 0.0, 0.0
        feet_y = np.min(body[FOOT_JOINTS, 1])
        return body + np.array([ax, -feet_y, az]), p.vis.copy()
```

- [ ] **Step 4: Implement `vision/pairing.py`**

```python
"""Pair frames from two unsynchronised cameras by nearest timestamp."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from vision.types import PoseResult


@dataclass
class Pair:
    t: float  # monotonic seconds of the anchor frame
    a: PoseResult | None  # camera "A"
    b: PoseResult | None  # camera "B"
    skew_s: float | None  # b.t - a.t when both present


class Pairer:
    """Each anchor result is emitted exactly once, paired with the nearest other-camera result
    within max_skew_s, or alone. It waits until a newer other-camera result has arrived (so the
    nearest is known) or wait_s has passed."""

    def __init__(self, anchor: str = "A", other: str | None = "B", max_skew_s: float = 0.033,
                 wait_s: float = 0.1, stale_s: float = 0.5):
        self.anchor, self.other = anchor, other
        self.max_skew_s, self.wait_s, self.stale_s = max_skew_s, wait_s, stale_s
        self._pending: deque[PoseResult] = deque()
        self._others: deque[PoseResult] = deque(maxlen=16)
        self._last_anchor_t = -np.inf
        self._latest_t = -np.inf

    @staticmethod
    def _pair(t, x: PoseResult | None, y: PoseResult | None, skew) -> Pair:
        by_cam = {p.cam: p for p in (x, y) if p is not None}
        return Pair(t, by_cam.get("A"), by_cam.get("B"), skew)

    def add(self, r: PoseResult) -> list[Pair]:
        self._latest_t = max(self._latest_t, r.t)
        if self.other is None:
            return [self._pair(r.t, r, None, None)] if r.cam == self.anchor else []
        out: list[Pair] = []
        if r.cam == self.anchor:
            self._pending.append(r)
            self._last_anchor_t = r.t
        elif r.cam == self.other:
            self._others.append(r)
            if not self._pending and r.t - self._last_anchor_t > self.stale_s:
                out.append(self._pair(r.t, None, r, None))
        out.extend(self._flush())
        return out

    def _flush(self) -> list[Pair]:
        out = []
        while self._pending:
            a = self._pending[0]
            newer = any(o.t >= a.t for o in self._others)
            if not newer and self._latest_t - a.t < self.wait_s:
                break
            self._pending.popleft()
            b = min(self._others, key=lambda o: abs(o.t - a.t), default=None)
            if b is not None and abs(b.t - a.t) <= self.max_skew_s:
                out.append(self._pair(a.t, a, b, b.t - a.t))
            else:
                out.append(self._pair(a.t, a, None, None))
        return out


class SkewStats:
    def __init__(self, n: int = 300):
        self._skews: deque[float] = deque(maxlen=n)
        self._paired: deque[bool] = deque(maxlen=n)

    def add(self, pair: Pair) -> None:
        self._paired.append(pair.a is not None and pair.b is not None)
        if pair.skew_s is not None:
            self._skews.append(abs(pair.skew_s))

    def summary(self) -> dict[str, float]:
        s = np.array(self._skews) * 1000
        return {
            "median_ms": float(np.median(s)) if s.size else float("nan"),
            "p95_ms": float(np.percentile(s, 95)) if s.size else float("nan"),
            "unpaired_pct": 100.0 * (1 - np.mean(self._paired)) if self._paired else float("nan"),
        }
```

- [ ] **Step 5: Implement `vision/pipeline.py`**

```python
"""Per-camera pose results -> FrameResult in the mat frame."""
from __future__ import annotations

from dataclasses import dataclass

from vision.cleanup import Cleaner
from vision.config import CameraConfig
from vision.filters import OneEuroFilter
from vision.triangulate import Triangulation, triangulate_pair
from vision.types import FrameResult, PoseResult

MIN_3D_JOINTS = 8  # below this the calibration or detections disagree; use one camera instead


@dataclass
class PipelineOutput:
    frame: FrameResult
    raw: dict[str, PoseResult | None]
    tri: Triangulation | None = None
    skew_s: float | None = None


class Pipeline:
    def __init__(self, cams: dict[str, CameraConfig], smoothing: bool = True,
                 min_vis: float = 0.5, max_reproj_px: float = 15.0):
        self.cams = cams
        self.min_vis, self.max_reproj_px = min_vis, max_reproj_px
        self._cleaner = Cleaner(cams)
        self._filter = OneEuroFilter() if smoothing else None

    def process(self, t: float, a: PoseResult | None, b: PoseResult | None, skew_s: float | None = None) -> PipelineOutput:
        """t is unix seconds (the FrameResult clock)."""
        tri = None
        ca, cb = self.cams.get("A"), self.cams.get("B")
        if (a is not None and b is not None and a.detected and b.detected
                and ca is not None and cb is not None and ca.calibrated and cb.calibrated):
            tri = triangulate_pair(a, b, ca, cb, self.min_vis, self.max_reproj_px)
        usable = tri if tri is not None and int(tri.ok.sum()) >= MIN_3D_JOINTS else None
        joints, conf, tracking = self._cleaner.update(t, usable, {"A": a, "B": b})
        if self._filter is not None:
            joints = self._filter(joints, t)
        frame = FrameResult(t=t, joints=joints, conf=conf, tracking=tracking)
        return PipelineOutput(frame=frame, raw={"A": a, "B": b}, tri=tri, skew_s=skew_s)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_cleanup.py vision/tests/test_pairing.py vision/tests/test_pipeline.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add vision/cleanup.py vision/pairing.py vision/pipeline.py vision/tests/test_cleanup.py vision/tests/test_pairing.py vision/tests/test_pipeline.py
git commit -m "vision: joint fallbacks, frame pairing, pipeline"
```

---

### Task 5: Recorder and replay

**Files:**
- Create: `vision/record.py`, `vision/replay.py`, `vision/tests/test_record_replay.py`

**Interfaces:**
- Consumes: `PipelineOutput`, `Pipeline`, `CameraConfig`, `load_cameras`, `REPO_ROOT`, `FrameResult`, `PoseResult`.
- Produces:
  - `vision.record`: `RECORDINGS_DIR`; `default_path(prefix="") -> Path`; `Recorder(path, cams, mat_raw_dict)` with `.path`, `.count`, `write(out: PipelineOutput)`, `close()`, context manager.
  - `vision.replay`: `read_header(path) -> dict`; `cams_from_header(header) -> dict[str, CameraConfig]`; `iter_frames(path) -> Iterator[(FrameResult, dict[str, PoseResult|None], skew_s)]`; `replay_outputs(path, retriangulate=False, cams=None, realtime=True, speed=1.0) -> Iterator[PipelineOutput]`; `replay(path, retriangulate=False, speed=1.0, realtime=True) -> Iterator[FrameResult]`; CLI `python -m vision.replay FILE [--retriangulate] [--speed S] [--fast]`.

- [ ] **Step 1: Write failing tests `vision/tests/test_record_replay.py`**

```python
import numpy as np

from vision.config import MatConfig
from vision.pipeline import Pipeline
from vision.record import Recorder
from vision.replay import cams_from_header, iter_frames, read_header, replay, replay_outputs
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams
from vision.types import PoseResult


def _record(path):
    cams, X = target_cams(), standing_skeleton()
    pipe = Pipeline(cams)
    outs = []
    with Recorder(path, cams, {"length_m": MatConfig().length_m}) as rec:
        for k in range(3):
            a, b = pose_from_points(cams["A"], X, t=k / 30), pose_from_points(cams["B"], X, t=k / 30 + 0.004)
            outs.append(pipe.process(1000 + k / 30, a, b, 0.004))
            rec.write(outs[-1])
        # nobody in view: undetected raw results and NaN joints
        outs.append(pipe.process(1000.2, PoseResult("A", 0.2), PoseResult("B", 0.2), 0.0))
        rec.write(outs[-1])
    return cams, outs


def test_round_trip(tmp_path):
    path = tmp_path / "rec.jsonl"
    cams, outs = _record(path)
    header = read_header(path)
    assert header["type"] == "header" and header["version"] == 1
    assert cams_from_header(header)["A"].calibrated
    frames = list(iter_frames(path))
    assert len(frames) == 4
    for (frame, raw, skew), out in zip(frames, outs):
        np.testing.assert_array_equal(frame.joints, out.frame.joints)
        assert frame.tracking == out.frame.tracking
    assert frames[3][0].tracking == "lost" and not frames[3][1]["A"].detected


def test_replay_and_retriangulate(tmp_path):
    path = tmp_path / "rec.jsonl"
    cams, outs = _record(path)
    plain = list(replay(path, realtime=False))
    assert [f.tracking for f in plain] == ["3d", "3d", "3d", "lost"]
    again = list(replay_outputs(path, retriangulate=True, cams=cams, realtime=False))
    assert again[0].tri is not None and again[0].frame.tracking == "3d"
    np.testing.assert_allclose(again[0].frame.joints, standing_skeleton(), atol=1e-6)
    assert again[3].frame.tracking == "lost"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_record_replay.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/record.py`**

```python
"""Write pipeline output to JSONL. Skeleton numbers only: never images or video."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vision.config import REPO_ROOT, CameraConfig
from vision.pipeline import PipelineOutput

RECORDINGS_DIR = REPO_ROOT / "data" / "recordings"


def default_path(prefix: str = "") -> Path:
    return RECORDINGS_DIR / f"{prefix}{datetime.now():%Y%m%d-%H%M%S}.jsonl"


class Recorder:
    def __init__(self, path: Path, cams: dict[str, CameraConfig], mat: dict):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self._f = open(self.path, "w")
        self._write({
            "type": "header",
            "version": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "cameras": {n: c.to_dict() for n, c in cams.items()},
            "mat": mat,
        })

    def write(self, out: PipelineOutput) -> None:
        raw = {k: (v.to_dict() if v is not None else None) for k, v in out.raw.items()}
        self._write({"type": "frame", **out.frame.to_dict(), "raw": raw, "skew_s": out.skew_s})
        self.count += 1

    def _write(self, obj: dict) -> None:
        self._f.write(json.dumps(obj, allow_nan=False) + "\n")

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 4: Implement `vision/replay.py`**

```python
"""Replay a JSONL recording as FrameResults, optionally re-running 3D with the current calibration.

    python -m vision.replay data/recordings/<file>.jsonl [--retriangulate] [--speed 1.0] [--fast]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterator

import numpy as np

from vision.config import CameraConfig, load_cameras
from vision.pipeline import Pipeline, PipelineOutput
from vision.types import FrameResult, PoseResult


def read_header(path) -> dict:
    with open(path) as f:
        header = json.loads(f.readline())
    if header.get("type") != "header":
        raise ValueError(f"{path} does not start with a recording header")
    return header


def cams_from_header(header: dict) -> dict[str, CameraConfig]:
    return {n: CameraConfig.from_dict(n, d) for n, d in (header.get("cameras") or {}).items()}


def iter_frames(path) -> Iterator[tuple[FrameResult, dict[str, PoseResult | None], float | None]]:
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") != "frame":
                continue
            raw = {k: (PoseResult.from_dict(v) if v else None) for k, v in (d.pop("raw") or {}).items()}
            skew = d.pop("skew_s", None)
            d.pop("type")
            yield FrameResult.from_dict(d), raw, skew


def replay_outputs(path, retriangulate: bool = False, cams: dict[str, CameraConfig] | None = None,
                   realtime: bool = True, speed: float = 1.0) -> Iterator[PipelineOutput]:
    pipeline = Pipeline(cams if cams is not None else load_cameras()) if retriangulate else None
    start_t = start_wall = None
    for frame, raw, skew in iter_frames(path):
        if realtime:
            if start_t is None:
                start_t, start_wall = frame.t, time.monotonic()
            else:
                delay = (frame.t - start_t) / speed - (time.monotonic() - start_wall)
                if delay > 0:
                    time.sleep(delay)
        if pipeline is not None:
            yield pipeline.process(frame.t, raw.get("A"), raw.get("B"), skew)
        else:
            yield PipelineOutput(frame=frame, raw=raw, tri=None, skew_s=skew)


def replay(path, retriangulate: bool = False, speed: float = 1.0, realtime: bool = True) -> Iterator[FrameResult]:
    for out in replay_outputs(path, retriangulate=retriangulate, realtime=realtime, speed=speed):
        yield out.frame


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", type=Path)
    p.add_argument("--retriangulate", action="store_true", help="re-run 3D from the raw 2D with the current config")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--fast", action="store_true", help="no real-time pacing")
    args = p.parse_args()
    for out in replay_outputs(args.path, args.retriangulate, realtime=not args.fast, speed=args.speed):
        f = out.frame
        n = int(np.isfinite(f.joints).all(axis=1).sum())
        print(f"{f.t:.3f}  {f.tracking:13s}  joints={n:2d}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_record_replay.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add vision/record.py vision/replay.py vision/tests/test_record_replay.py
git commit -m "vision: JSONL recorder and replay with re-triangulation"
```

---

### Task 6: Capture, MediaPipe pose, live source, model download

**Files:**
- Create: `vision/capture.py`, `vision/pose.py`, `vision/live.py`, `vision/models/__init__.py`, `vision/models/download.py`, `vision/tests/test_capture.py`, `vision/tests/test_pose.py`

**Interfaces:**
- Consumes: `CameraConfig`, `PoseResult`, `Pairer`, `Pair`, `SkewStats`.
- Produces:
  - `vision.capture`: `BACKEND`; `mono_to_unix(t) -> float`; `FpsMeter(window_s=1.0)` with `tick(t)`, `.value`; `Camera(name, index, width=1280, height=720, fps=30)` thread with `latest() -> (seq, t, frame) | None`, `.fps`, `.opened`, `.error`, `.actual_size`, `stop()`; `resolution_warning(cfg, actual_size) -> str | None`.
  - `vision.pose`: `MODEL_DIR`; `model_path(kind="full") -> Path`; `PoseEstimator(kind="full")` with `detect(frame_bgr, t_mono, cam) -> PoseResult`, `close()`; `PoseWorker(camera, out_queue, model="full")` thread with `.latest -> (frame, PoseResult) | None`, `.ms`, `.error`, `stop()`.
  - `vision.live`: `LiveSource(cams, model="full")` with `start()`, `stop()`, context manager, `poll(timeout=0.01) -> list[Pair]`, `latest_frames() -> dict[str, (frame, PoseResult) | None]`, `stats() -> dict[str, dict]` (keys `fps`, `pose_ms`, `error`, `warning`), `.skew: SkewStats`.
  - CLI `python -m vision.models.download`.

- [ ] **Step 1: Write the model downloader and fetch the models**

`vision/models/__init__.py`: empty file.

`vision/models/download.py`:
```python
"""Download MediaPipe model files into vision/models/.

    python -m vision.models.download
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent
BASE = "https://storage.googleapis.com/mediapipe-models"
MODELS = {
    "pose_landmarker_full.task": f"{BASE}/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
    "pose_landmarker_heavy.task": f"{BASE}/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task",
    "hand_landmarker.task": f"{BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
}


def main() -> None:
    for name, url in MODELS.items():
        dest = MODEL_DIR / name
        if dest.exists():
            print(f"have {name} ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        tmp = dest.with_suffix(".part")
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
        print(f"  saved {dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
```

Run: `python -m vision.models.download`
Expected: three `saved ... MB` lines. If urllib fails with an SSL certificate error, run `curl -L -o vision/models/<name> <url>` for each URL instead.

- [ ] **Step 2: Write failing tests**

`vision/tests/test_capture.py`:
```python
from vision.capture import FpsMeter, resolution_warning
from vision.config import CameraConfig


def test_fps_meter():
    m = FpsMeter()
    for k in range(61):
        m.tick(k / 30)
    assert abs(m.value - 30.0) < 0.5
    assert FpsMeter().value == 0.0


def test_resolution_warning():
    cfg = CameraConfig(name="A", index=0, width=1280, height=720)
    assert resolution_warning(cfg, (1280, 720)) is None
    assert resolution_warning(cfg, None) is None
    msg = resolution_warning(cfg, (640, 480))
    assert "640x480" in msg and "1280x720" in msg
```

`vision/tests/test_pose.py`:
```python
import numpy as np
import pytest

from vision.pose import model_path


def test_blank_frame_has_no_person():
    try:
        model_path("full")
    except FileNotFoundError:
        pytest.skip("model not downloaded")
    from vision.pose import PoseEstimator

    est = PoseEstimator("full")
    try:
        r = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")
        r2 = est.detect(np.zeros((720, 1280, 3), np.uint8), 1.0, "A")  # same timestamp must not raise
    finally:
        est.close()
    assert r.cam == "A" and not r.detected and not r2.detected
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_capture.py vision/tests/test_pose.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `vision/capture.py`**

```python
"""Webcam capture threads that keep only the latest frame."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

from vision.config import CameraConfig

BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
_MONO_TO_UNIX = time.time() - time.monotonic()


def mono_to_unix(t: float) -> float:
    return t + _MONO_TO_UNIX


class FpsMeter:
    def __init__(self, window_s: float = 1.0):
        self.window_s = window_s
        self._times: deque[float] = deque()

    def tick(self, t: float) -> None:
        self._times.append(t)
        while self._times and t - self._times[0] > self.window_s:
            self._times.popleft()

    @property
    def value(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0


def resolution_warning(cfg: CameraConfig, actual_size) -> str | None:
    if actual_size is None or tuple(actual_size) == (cfg.width, cfg.height):
        return None
    w, h = actual_size
    return f"camera {cfg.name} delivers {w}x{h} but is configured for {cfg.width}x{cfg.height}"


class Camera(threading.Thread):
    def __init__(self, name: str, index: int, width: int = 1280, height: int = 720, fps: int = 30):
        super().__init__(daemon=True, name=f"camera-{name}")
        self.name_, self.index, self.width, self.height, self.req_fps = name, index, width, height, fps
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._seq = 0
        self._stop = threading.Event()
        self.meter = FpsMeter()
        self.opened = False
        self.error: str | None = None
        self.actual_size: tuple[int, int] | None = None

    @property
    def cam_name(self) -> str:
        return self.name_

    def run(self) -> None:
        cap = cv2.VideoCapture(self.index, BACKEND)
        if not cap.isOpened():
            self.error = f"camera {self.name_} (index {self.index}) failed to open"
            return
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.req_fps)
        self.opened = True
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                t = time.monotonic()
                if not ok:
                    self.error = f"camera {self.name_}: read failed"
                    time.sleep(0.01)
                    continue
                self.error = None
                with self._lock:
                    self._frame, self._t = frame, t
                    self._seq += 1
                self.meter.tick(t)
                self.actual_size = (frame.shape[1], frame.shape[0])
        finally:
            cap.release()
            self.opened = False

    def latest(self) -> tuple[int, float, np.ndarray] | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._seq, self._t, self._frame

    @property
    def fps(self) -> float:
        return self.meter.value

    def stop(self) -> None:
        self._stop.set()
```

Note: `threading.Thread` already has a `name` attribute, which is why the camera label is stored as `name_` and exposed as `cam_name`.

- [ ] **Step 5: Implement `vision/pose.py`**

```python
"""MediaPipe Pose Landmarker per camera."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from vision.types import PoseResult

MODEL_DIR = Path(__file__).resolve().parent / "models"


def model_path(kind: str = "full") -> Path:
    p = MODEL_DIR / f"pose_landmarker_{kind}.task"
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing: run python -m vision.models.download")
    return p


class PoseEstimator:
    """VIDEO-mode landmarker for one camera (timestamps must increase)."""

    def __init__(self, kind: str = "full"):
        options = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path(kind))),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
        self._last_ms = -1

    def detect(self, frame_bgr: np.ndarray, t_mono: float, cam: str) -> PoseResult:
        ts = max(int(t_mono * 1000), self._last_ms + 1)
        self._last_ms = ts
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self._landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        if not res.pose_landmarks:
            return PoseResult(cam=cam, t=t_mono)
        h, w = frame_bgr.shape[:2]
        lms = res.pose_landmarks[0]
        lm2d = np.array([[p.x * w, p.y * h] for p in lms])
        vis = np.array([p.visibility for p in lms], dtype=float)
        world = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks[0]]) if res.pose_world_landmarks else None
        return PoseResult(cam=cam, t=t_mono, lm2d=lm2d, vis=vis, world=world)

    def close(self) -> None:
        self._landmarker.close()


class PoseWorker(threading.Thread):
    """Runs pose on each new frame from one Camera and puts PoseResults on a shared queue."""

    def __init__(self, camera, out: queue.Queue, model: str = "full"):
        super().__init__(daemon=True, name=f"pose-{camera.cam_name}")
        self.camera, self.out, self.model = camera, out, model
        self._stop = threading.Event()
        self.latest: tuple[np.ndarray, PoseResult] | None = None
        self.ms = 0.0
        self.error: str | None = None

    def run(self) -> None:
        try:
            est = PoseEstimator(self.model)
        except Exception as e:  # surfaced in the viewer HUD
            self.error = f"pose {self.camera.cam_name}: {e}"
            return
        last_seq = -1
        try:
            while not self._stop.is_set():
                item = self.camera.latest()
                if item is None or item[0] == last_seq:
                    time.sleep(0.002)
                    continue
                last_seq, t, frame = item
                t0 = time.perf_counter()
                r = est.detect(frame, t, self.camera.cam_name)
                dt = (time.perf_counter() - t0) * 1000
                self.ms = dt if self.ms == 0 else 0.9 * self.ms + 0.1 * dt
                self.latest = (frame, r)
                self.out.put(r)
        finally:
            est.close()

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 6: Implement `vision/live.py`**

```python
"""Live cameras -> paired PoseResults."""
from __future__ import annotations

import queue

import numpy as np

from vision.capture import Camera, resolution_warning
from vision.config import CameraConfig
from vision.pairing import Pair, Pairer, SkewStats
from vision.pose import PoseWorker, model_path
from vision.types import PoseResult


class LiveSource:
    def __init__(self, cams: dict[str, CameraConfig], model: str = "full"):
        if not cams:
            raise SystemExit("No cameras configured: run python -m vision.cams first")
        model_path(model)  # fail fast if the model is missing
        self.cams = cams
        self._queue: queue.Queue = queue.Queue()
        self.cameras = {n: Camera(n, c.index, c.width, c.height) for n, c in cams.items()}
        self.workers = {n: PoseWorker(cam, self._queue, model) for n, cam in self.cameras.items()}
        names = sorted(cams)
        self.pairer = Pairer(anchor=names[0], other=names[1] if len(names) > 1 else None)
        self.skew = SkewStats()

    def start(self) -> "LiveSource":
        for c in self.cameras.values():
            c.start()
        for w in self.workers.values():
            w.start()
        return self

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()
        for c in self.cameras.values():
            c.stop()
        for t in [*self.workers.values(), *self.cameras.values()]:
            if t.is_alive():
                t.join(timeout=1.0)

    def __enter__(self) -> "LiveSource":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def poll(self, timeout: float = 0.01) -> list[Pair]:
        out: list[Pair] = []
        try:
            r = self._queue.get(timeout=timeout)
        except queue.Empty:
            return out
        while True:
            for pair in self.pairer.add(r):
                self.skew.add(pair)
                out.append(pair)
            try:
                r = self._queue.get_nowait()
            except queue.Empty:
                return out

    def latest_frames(self) -> dict[str, tuple[np.ndarray, PoseResult] | None]:
        return {n: w.latest for n, w in self.workers.items()}

    def stats(self) -> dict[str, dict]:
        return {
            n: {
                "fps": cam.fps,
                "pose_ms": self.workers[n].ms,
                "error": cam.error or self.workers[n].error,
                "warning": resolution_warning(self.cams[n], cam.actual_size),
            }
            for n, cam in self.cameras.items()
        }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_capture.py vision/tests/test_pose.py -q`
Expected: all PASS (test_pose runs because the model was downloaded in Step 1).

- [ ] **Step 8: Commit**

```bash
git add vision/capture.py vision/pose.py vision/live.py vision/models/ vision/tests/test_capture.py vision/tests/test_pose.py
git commit -m "vision: capture threads, MediaPipe pose workers, live source"
```

---

### Task 7: Camera picker, drawing helpers, debug viewer (first hardware milestone)

**Files:**
- Create: `vision/draw.py`, `vision/cams.py`, `vision/debug_view.py`, `vision/markers.py` (detector only; PnP added in Task 9), `vision/tests/test_debug_view.py`

**Interfaces:**
- Consumes: `LiveSource`, `Pipeline`, `PipelineOutput`, `Recorder`, `default_path`, `replay_outputs`, `read_header`, `cams_from_header`, `project`, `cam_center`, `load_cameras`, `save_cameras`, `load_mat`, `mat_raw`, `BONES`, `BACKEND`, `mono_to_unix`.
- Produces:
  - `vision.draw`: colours `GREEN, MAGENTA, RED, WHITE, YELLOW, LEFT_COLOR, RIGHT_COLOR, CENTER_COLOR`; `bone_color(i, j)`, `pt(p) -> (int,int)`, `draw_skeleton(img, pts, ok=None, color=None, thickness=2, radius=3)`, `draw_points(img, pts, color, radius=3)`, `draw_text(img, text, org, color=WHITE, scale=0.5)`, `fit(img, size)`.
  - `vision.markers`: `aruco_dictionary(name)`, `MarkerDetector(mat)` with `detect(image) -> dict[int, ndarray(4,2)]`.
  - `vision.debug_view`: `PlaneMap(u_range, v_range, flip_u, size=(640,360))` callable on (N,2); `TOP`, `FRONT`; `render_camera(...)`, `render_topdown(frame, mat, cams)`, `render_front(frame, hud_lines)`, `compose_view(cams, mat, images, poses, out, stats, skew, recording, detector=None) -> ndarray(720,1280,3)`; CLI `python -m vision.debug_view [--replay FILE] [--retriangulate] [--speed S] [--model full|heavy]`.
  - CLI `python -m vision.cams`.

- [ ] **Step 1: Write failing tests `vision/tests/test_debug_view.py`**

```python
import numpy as np

from vision.config import MatConfig
from vision.debug_view import FRONT, TOP, compose_view
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams


def test_top_view_orientation():
    c = TOP(np.array([[0.0, 0.0]]))[0]
    left = TOP(np.array([[1.0, 0.0]]))[0]  # +x (patient's left) is drawn to the left from above
    front = TOP(np.array([[0.0, 1.0]]))[0]  # +z (Mira) is drawn at the top
    assert left[0] < c[0] and front[1] < c[1]


def test_front_view_orientation():
    c = FRONT(np.array([[0.0, 1.0]]))[0]
    assert FRONT(np.array([[0.5, 1.0]]))[0][0] > c[0]  # Mira's right is drawn to the right
    assert FRONT(np.array([[0.0, 1.5]]))[0][1] < c[1]


def test_compose_view_renders_with_and_without_images():
    cams, X = target_cams(), standing_skeleton()
    a, b = pose_from_points(cams["A"], X), pose_from_points(cams["B"], X)
    out = Pipeline(cams).process(1.0, a, b, 0.004)
    stats = {"A": {"fps": 30.0, "pose_ms": 20.0, "error": None, "warning": "camera A delivers 640x480"},
             "B": {"fps": 29.0, "pose_ms": 21.0, "error": None, "warning": None}}
    skew = {"median_ms": 4.0, "p95_ms": 9.0, "unpaired_pct": 0.0}
    img = compose_view(cams, MatConfig(), {"A": np.zeros((720, 1280, 3), np.uint8)}, {"A": a, "B": b}, out, stats, skew, "REC")
    assert img.shape == (720, 1280, 3)
    img = compose_view({}, MatConfig(), {}, {}, None, None, None, None)
    assert img.shape == (720, 1280, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_debug_view.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/draw.py`**

```python
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
```

- [ ] **Step 4: Implement the detector half of `vision/markers.py`**

```python
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
```

- [ ] **Step 5: Implement `vision/debug_view.py`**

```python
"""Live debug viewer.

    python -m vision.debug_view                   # live cameras
    python -m vision.debug_view --replay FILE     # a recording (add --retriangulate for current calibration)

Top: camera feeds with MediaPipe 2D (green) and the 3D result projected back (magenta).
Bottom: top-down view of the mat and a front view from Mira; orange = person's left, blue = right.
Keys: r record on/off, m markers on/off, q quit.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from vision.capture import mono_to_unix
from vision.config import CameraConfig, MatConfig, load_cameras, load_mat, mat_raw
from vision.draw import GREEN, MAGENTA, RED, WHITE, YELLOW, draw_points, draw_skeleton, draw_text, fit, pt
from vision.frames import cam_center, project
from vision.markers import MarkerDetector
from vision.pipeline import Pipeline, PipelineOutput
from vision.record import Recorder, default_path
from vision.replay import cams_from_header, read_header, replay_outputs
from vision.types import FrameResult, PoseResult

WINDOW = "Mira debug"
PANEL_W, PANEL_H = 640, 360
BG = (32, 32, 32)
GRID = (55, 55, 55)
MAT_FILL = (110, 60, 90)


class PlaneMap:
    """Maps two mat-frame coordinates (u, v) to panel pixels; flip_u draws +u to the left."""

    def __init__(self, u_range, v_range, flip_u: bool, size=(PANEL_W, PANEL_H), margin: int = 10):
        w, h = size
        self.s = min((w - 2 * margin) / (u_range[1] - u_range[0]), (h - 2 * margin) / (v_range[1] - v_range[0]))
        self.uc, self.vc = sum(u_range) / 2, sum(v_range) / 2
        self.cx, self.cy = w / 2, h / 2
        self.sign = -1.0 if flip_u else 1.0

    def __call__(self, uv) -> np.ndarray:
        uv = np.asarray(uv, float).reshape(-1, 2)
        px = np.empty_like(uv)
        px[:, 0] = self.cx + self.sign * (uv[:, 0] - self.uc) * self.s
        px[:, 1] = self.cy - (uv[:, 1] - self.vc) * self.s
        return px


TOP = PlaneMap((-2.6, 2.6), (-1.3, 2.9), flip_u=True)  # (x, z) seen from above, Mira at the top
FRONT = PlaneMap((-1.3, 1.3), (-0.1, 2.1), flip_u=False)  # (x, y) seen from Mira


def _blank() -> np.ndarray:
    return np.full((PANEL_H, PANEL_W, 3), BG, np.uint8)


def render_camera(image, pose: PoseResult | None, frame: FrameResult | None, cam: CameraConfig | None,
                  detector: MarkerDetector | None = None) -> np.ndarray:
    if image is not None:
        h, w = image.shape[:2]
        panel = fit(image, (PANEL_W, PANEL_H))
    else:
        w, h = (cam.width, cam.height) if cam is not None else (1280, 720)
        panel = _blank()
        draw_text(panel, "no image", (PANEL_W // 2 - 40, PANEL_H // 2), RED)
    scale = np.array([PANEL_W / w, PANEL_H / h])
    if detector is not None and image is not None:
        for i, c in detector.detect(image).items():
            poly = (c * scale).astype(np.int32)
            cv2.polylines(panel, [poly], True, YELLOW, 2)
            draw_text(panel, str(i), pt(poly[0]), YELLOW)
    if pose is not None and pose.detected:
        draw_skeleton(panel, pose.lm2d * scale, ok=pose.vis >= 0.5, color=GREEN)
    if frame is not None and cam is not None and cam.calibrated:
        draw_points(panel, project(frame.joints, cam.K, cam.dist, cam.R, cam.t) * scale, MAGENTA)
    return panel


def render_topdown(frame: FrameResult | None, mat: MatConfig, cams: dict[str, CameraConfig]) -> np.ndarray:
    img = _blank()
    for x in np.arange(-2.5, 2.51, 0.5):
        a, b = TOP([[x, -1.3], [x, 2.9]])
        cv2.line(img, pt(a), pt(b), GRID, 1)
    for z in np.arange(-1.0, 2.91, 0.5):
        a, b = TOP([[-2.6, z], [2.6, z]])
        cv2.line(img, pt(a), pt(b), GRID, 1)
    cv2.fillPoly(img, [TOP(mat.mat_polygon()).astype(np.int32)], MAT_FILL)
    for i, corners in mat.marker_corners().items():
        cv2.fillPoly(img, [TOP(corners[:, [0, 2]]).astype(np.int32)], (230, 230, 230))
        draw_text(img, str(i), pt(TOP([mat.marker_centers()[i][[0, 2]]])[0] + [-4, 4]), (0, 0, 0), 0.4)
    mira = TOP([[0.0, mat.length_m / 2 + 1.0]])[0]
    cv2.rectangle(img, pt(mira - 8), pt(mira + 8), GREEN, -1)
    draw_text(img, "Mira", pt(mira + [12, 5]), GREEN)
    for name, cam in cams.items():
        if not cam.calibrated:
            continue
        c = cam_center(cam.R, cam.t)
        p = TOP([[c[0], c[2]]])[0]
        f = cam.R[2]
        q = TOP([[c[0] + 0.4 * f[0], c[2] + 0.4 * f[2]]])[0]
        cv2.line(img, pt(p), pt(q), YELLOW, 2)
        cv2.circle(img, pt(p), 6, YELLOW, -1)
        draw_text(img, name, pt(p + [8, -8]), YELLOW)
    if frame is not None:
        draw_skeleton(img, TOP(frame.joints[:, [0, 2]]))
    a, b = TOP([[0.0, -1.15], [0.5, -1.15]])
    cv2.arrowedLine(img, pt(a), pt(b), WHITE, 1, tipLength=0.2)
    draw_text(img, "+x", pt(b + [-24, -6]), WHITE, 0.4)
    draw_text(img, "top view   orange = left, blue = right", (8, PANEL_H - 10), WHITE, 0.45)
    return img


def render_front(frame: FrameResult | None, hud_lines: list[str]) -> np.ndarray:
    img = _blank()
    a, b = FRONT([[-1.3, 0.0], [1.3, 0.0]])
    cv2.line(img, pt(a), pt(b), GRID, 2)
    a, b = FRONT([[-0.305, 0.0], [0.305, 0.0]])
    cv2.line(img, pt(a), pt(b), MAT_FILL, 4)
    if frame is not None:
        draw_skeleton(img, FRONT(frame.joints[:, [0, 1]]))
    for k, line in enumerate(hud_lines):
        draw_text(img, line, (8, 20 + 18 * k), RED if line.startswith("REC") else WHITE, 0.45)
    draw_text(img, "front view (from Mira)", (8, PANEL_H - 10), WHITE, 0.45)
    return img


def compose_view(cams: dict[str, CameraConfig], mat: MatConfig, images: dict, poses: dict,
                 out: PipelineOutput | None, stats: dict | None, skew: dict | None, recording: str | None,
                 detector: MarkerDetector | None = None) -> np.ndarray:
    frame = out.frame if out is not None else None
    panels = []
    for name in ("A", "B"):
        cam = cams.get(name)
        panel = render_camera(images.get(name), poses.get(name), frame, cam, detector)
        s = (stats or {}).get(name)
        draw_text(panel, f"{name}  {s['fps']:.0f} fps  pose {s['pose_ms']:.0f} ms" if s else name, (8, 20))
        y = 40
        for msg in ((s or {}).get("error"), (s or {}).get("warning")):
            if msg:
                draw_text(panel, msg, (8, y), RED, 0.45)
                y += 18
        if cam is None:
            draw_text(panel, "not configured: run python -m vision.cams", (8, y), RED, 0.45)
        elif not cam.calibrated:
            draw_text(panel, "not calibrated (2D only)", (8, y), YELLOW, 0.45)
        panels.append(panel)
    hud = []
    if frame is not None:
        hud.append(f"tracking: {frame.tracking}")
    if skew:
        hud.append(f"pair skew median {skew['median_ms']:.0f} ms  p95 {skew['p95_ms']:.0f} ms  unpaired {skew['unpaired_pct']:.0f}%")
    if out is not None and out.tri is not None and out.tri.ok.any():
        err = float(np.nanmean(out.tri.reproj_err[out.tri.ok]))
        hud.append(f"3D reprojection {err:.1f} px  ({int(out.tri.ok.sum())}/33 joints)")
    if recording:
        hud.append(recording)
    hud.append("keys: r record   m markers   q quit")
    bottom = np.hstack([render_topdown(frame, mat, cams), render_front(frame, hud)])
    return np.vstack([np.hstack(panels), bottom])


def run_live(cams: dict[str, CameraConfig], mat: MatConfig, model: str) -> None:
    from vision.live import LiveSource  # imports MediaPipe; keep replay usable without it

    detector, show_markers = MarkerDetector(mat), False
    pipeline = Pipeline(cams)
    recorder: Recorder | None = None
    last: PipelineOutput | None = None
    last_draw = 0.0
    with LiveSource(cams, model) as src:
        try:
            while True:
                for pair in src.poll(0.005):
                    last = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
                    if recorder is not None:
                        recorder.write(last)
                now = time.monotonic()
                if now - last_draw >= 1 / 30:
                    last_draw = now
                    latest = src.latest_frames()
                    images = {n: v[0] for n, v in latest.items() if v}
                    poses = {n: v[1] for n, v in latest.items() if v}
                    rec = f"REC {recorder.count} frames -> {recorder.path.name}" if recorder else None
                    img = compose_view(cams, mat, images, poses, last, src.stats(), src.skew.summary(), rec,
                                       detector if show_markers else None)
                    cv2.imshow(WINDOW, img)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("m"):
                    show_markers = not show_markers
                if key == ord("r"):
                    if recorder is not None:
                        recorder.close()
                        print(f"saved {recorder.count} frames to {recorder.path}")
                        recorder = None
                    else:
                        recorder = Recorder(default_path(), cams, mat_raw())
                        print(f"recording to {recorder.path}")
        finally:
            if recorder is not None:
                recorder.close()
                print(f"saved {recorder.count} frames to {recorder.path}")
            cv2.destroyAllWindows()


def run_replay(path: Path, cams: dict[str, CameraConfig], mat: MatConfig, retriangulate: bool, speed: float) -> None:
    view_cams = cams if retriangulate else cams_from_header(read_header(path))
    try:
        for out in replay_outputs(path, retriangulate=retriangulate, cams=cams, realtime=True, speed=speed):
            img = compose_view(view_cams, mat, {}, out.raw, out, None, None, f"replay {path.name}")
            cv2.imshow(WINDOW, img)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        cv2.destroyAllWindows()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--replay", type=Path)
    p.add_argument("--retriangulate", action="store_true")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    if args.replay:
        run_replay(args.replay, cams, mat, args.retriangulate, args.speed)
    else:
        run_live(cams, mat, args.model)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_debug_view.py -q`
Expected: all PASS.

- [ ] **Step 7: Implement `vision/cams.py`**

```python
"""Pick which camera index is A and which is B.

    python -m vision.cams

Shows every camera that opens, tiled and labelled by index. Press a or b, then the
index digit, to assign; s saves to config/cameras.yaml; q quits without saving.
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np

from vision.capture import BACKEND
from vision.config import CameraConfig, load_cameras, save_cameras
from vision.draw import GREEN, WHITE, YELLOW, draw_text, fit

TILE = (480, 270)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-index", type=int, default=6)
    args = p.parse_args()

    caps = {}
    for i in range(args.max_index):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            caps[i] = cap
        else:
            cap.release()
    if not caps:
        print("No cameras opened. Check the USB connections and the terminal's Camera permission.")
        return 1

    cams = load_cameras()
    assign = {n: c.index for n, c in cams.items()}
    sizes: dict[int, tuple[int, int]] = {}
    pending: str | None = None
    print(__doc__)
    try:
        while True:
            tiles = []
            for i, cap in caps.items():
                ok, frame = cap.read()
                tile = fit(frame, TILE) if ok else np.zeros((TILE[1], TILE[0], 3), np.uint8)
                if ok:
                    sizes[i] = (frame.shape[1], frame.shape[0])
                roles = [n for n, idx in assign.items() if idx == i]
                w, h = sizes.get(i, (0, 0))
                draw_text(tile, f"index {i}  {w}x{h}", (8, 22), WHITE, 0.6)
                if roles:
                    draw_text(tile, "camera " + "/".join(roles), (8, 48), GREEN, 0.8)
                tiles.append(tile)
            while len(tiles) % 3:
                tiles.append(np.zeros_like(tiles[0]))
            grid = np.vstack([np.hstack(tiles[r:r + 3]) for r in range(0, len(tiles), 3)])
            prompt = f"assign {pending}: press the index digit" if pending else "a/b then digit to assign, s save, q quit"
            draw_text(grid, prompt, (8, grid.shape[0] - 12), YELLOW, 0.6)
            cv2.imshow("Mira cameras", grid)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("a"), ord("b")):
                pending = chr(key).upper()
            elif pending and ord("0") <= key <= ord("9") and (key - ord("0")) in caps:
                assign[pending] = key - ord("0")
                pending = None
            elif key == ord("s"):
                for name, idx in sorted(assign.items()):
                    old = cams.get(name)
                    if old is not None and old.index == idx:
                        continue
                    if old is not None and (old.has_intrinsics or old.calibrated):
                        print(f"camera {name} moved from index {old.index} to {idx}: its calibration was cleared")
                    w, h = sizes.get(idx, (1280, 720))
                    cams[name] = CameraConfig(name=name, index=idx, hint=f"{w}x{h} at index {idx}")
                save_cameras(cams)
                print("saved:", {n: c.index for n, c in cams.items()})
                return 0
            elif key in (ord("q"), 27):
                return 0
    finally:
        for cap in caps.values():
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Hardware smoke test (with Eric)**

Run: `python -m vision.cams` → assign A (front-left webcam) and B (front-right webcam), press `s`.
Then: `python -m vision.debug_view` with someone in view.
Expected: both feeds visible with green skeletons; HUD fps ≥ 20 per camera; tracking `single_cam_A`/`single_cam_B`; a skeleton in the front panel; no red resolution warnings. If a resolution warning appears, record what size the camera delivers. If fps < 20, try `--model full` (default) and note pose ms.
Note: macOS will ask for Camera permission for the terminal on first run; grant it and re-run.

- [ ] **Step 9: Commit**

```bash
git add vision/draw.py vision/cams.py vision/debug_view.py vision/markers.py vision/tests/test_debug_view.py
git commit -m "vision: camera picker, drawing helpers, debug viewer"
```

---

### Task 8: Lens calibration from an on-screen checkerboard

**Files:**
- Create: `vision/calib_intrinsics.py`, `vision/tests/test_calib_intrinsics.py`

**Interfaces:**
- Consumes: `Camera`, `load_cameras`, `save_cameras`, `draw_text`, `fit`.
- Produces: `PATTERN = (9, 6)`, `board_image(square_px=120, margin_px=60) -> ndarray`, `board_object_points(square_m) -> (54,3) float32`, `view_descriptor(corners, size) -> ndarray(5)`, `is_novel(desc, previous, threshold=0.08) -> bool`, `calibrate(obj_points, img_points, size) -> (rms, K, dist, per_view_err)`, `calibrate_with_outlier_drop(obj_points, img_points, size) -> (rms, K, dist, per_view_err, n_dropped)`; CLI `python -m vision.calib_intrinsics --cam A|B [--row-mm MM] [--views 25] [--save-board PATH]`.

- [ ] **Step 1: Write failing tests `vision/tests/test_calib_intrinsics.py`**

```python
import cv2
import numpy as np

from vision.calib_intrinsics import (
    PATTERN, board_image, board_object_points, calibrate, calibrate_with_outlier_drop, is_novel, view_descriptor,
)

K = np.array([[1000.0, 0, 640], [0, 1000, 360], [0, 0, 1]])
DIST = np.array([0.1, -0.2, 0, 0, 0])


def _views(n=20, seed=0):
    rng = np.random.default_rng(seed)
    obj = board_object_points(0.03)
    obj = (obj - obj.mean(axis=0)).astype(np.float32)
    objs, imgs = [], []
    while len(objs) < n:
        rvec = rng.normal(0, 0.35, 3)
        tvec = np.array([rng.uniform(-0.1, 0.1), rng.uniform(-0.07, 0.07), rng.uniform(0.4, 0.7)])
        img, _ = cv2.projectPoints(obj, rvec, tvec, K, DIST)
        img = img.reshape(-1, 2) + rng.normal(0, 0.1, (len(obj), 2))
        if (img[:, 0] > 0).all() and (img[:, 0] < 1280).all() and (img[:, 1] > 0).all() and (img[:, 1] < 720).all():
            objs.append(obj)
            imgs.append(img.astype(np.float32))
    return objs, imgs


def test_board_image_has_detectable_pattern():
    img = board_image()
    found, corners = cv2.findChessboardCornersSB(img, PATTERN)
    assert found and len(corners) == PATTERN[0] * PATTERN[1]


def test_calibrate_recovers_intrinsics():
    objs, imgs = _views()
    rms, K2, dist2, errs = calibrate(objs, imgs, (1280, 720))
    assert rms < 0.3 and len(errs) == 20
    assert abs(K2[0, 0] - 1000) < 5 and abs(K2[0, 2] - 640) < 5 and abs(K2[1, 2] - 360) < 5


def test_outlier_view_is_dropped():
    objs, imgs = _views()
    imgs[3] = imgs[3] + np.random.default_rng(5).normal(0, 6, imgs[3].shape).astype(np.float32)
    rms, _, _, _, dropped = calibrate_with_outlier_drop(objs, imgs, (1280, 720))
    assert dropped >= 1 and rms < 0.3


def test_novelty():
    _, imgs = _views(2)
    d = view_descriptor(imgs[0], (1280, 720))
    assert not is_novel(d, [d])
    assert is_novel(d, [])
    assert is_novel(view_descriptor(imgs[0] + [400, 0], (1280, 720)), [d])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_calib_intrinsics.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/calib_intrinsics.py`**

```python
"""Lens calibration for one webcam from a checkerboard shown on a screen.

    python -m vision.calib_intrinsics --cam A
    python -m vision.calib_intrinsics --save-board board.png   # to show on an iPad/other screen

1. Move the 'board' window to the screen you will film (f toggles full screen).
2. Measure the width of one row of 10 squares in mm with a ruler, press space,
   and type it in the terminal (or pass --row-mm).
3. Point the webcam at the board from many angles, distances and positions;
   hold still briefly to capture. 25 views are captured automatically.
Calibrating clears that camera's extrinsics (they depend on the lens model).
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np

from vision.capture import Camera
from vision.config import load_cameras, save_cameras
from vision.draw import GREEN, RED, WHITE, draw_text, fit

PATTERN = (9, 6)  # inner corners (cols, rows): a 10 x 7 square board
SQUARES_ACROSS = PATTERN[0] + 1


def board_image(square_px: int = 120, margin_px: int = 60) -> np.ndarray:
    cols, rows = PATTERN[0] + 1, PATTERN[1] + 1
    img = np.full((rows * square_px + 2 * margin_px, cols * square_px + 2 * margin_px), 255, np.uint8)
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                y, x = margin_px + r * square_px, margin_px + c * square_px
                img[y:y + square_px, x:x + square_px] = 0
    return img


def board_object_points(square_m: float) -> np.ndarray:
    grid = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)
    return np.hstack([grid * square_m, np.zeros((len(grid), 1))]).astype(np.float32)


def view_descriptor(corners, size) -> np.ndarray:
    """Position, scale and perspective tilt of a detected board, for picking varied views."""
    c = np.asarray(corners, float).reshape(-1, 2)
    w, h = size
    g = c.reshape(PATTERN[1], PATTERN[0], 2)
    top, bottom = np.linalg.norm(g[0, -1] - g[0, 0]), np.linalg.norm(g[-1, -1] - g[-1, 0])
    left, right = np.linalg.norm(g[-1, 0] - g[0, 0]), np.linalg.norm(g[-1, -1] - g[0, -1])
    span = c.max(axis=0) - c.min(axis=0)
    return np.array([
        c[:, 0].mean() / w,
        c[:, 1].mean() / h,
        np.sqrt(span[0] * span[1] / (w * h)),
        2 * (top - bottom) / (top + bottom),
        2 * (left - right) / (left + right),
    ])


def is_novel(desc, previous, threshold: float = 0.08) -> bool:
    return all(np.linalg.norm(desc - p) > threshold for p in previous)


def calibrate(obj_points, img_points, size):
    obj = [np.asarray(o, np.float32) for o in obj_points]
    img = [np.asarray(i, np.float32).reshape(-1, 1, 2) for i in img_points]
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj, img, size, None, None)
    errs = []
    for o, i, r, t in zip(obj, img, rvecs, tvecs):
        proj, _ = cv2.projectPoints(o, r, t, K, dist)
        errs.append(float(np.sqrt(np.mean(np.sum((proj - i) ** 2, axis=2)))))
    return float(rms), K, dist.ravel(), np.array(errs)


def calibrate_with_outlier_drop(obj_points, img_points, size):
    """Calibrate, drop views with error > 2x the median once, recalibrate."""
    rms, K, dist, errs = calibrate(obj_points, img_points, size)
    keep = errs <= 2 * np.median(errs)
    if keep.all() or keep.sum() < 10:
        return rms, K, dist, errs, 0
    obj = [o for o, k in zip(obj_points, keep) if k]
    img = [i for i, k in zip(img_points, keep) if k]
    rms, K, dist, errs = calibrate(obj, img, size)
    return rms, K, dist, errs, int((~keep).sum())


def _wait_for_board(board: np.ndarray) -> bool:
    cv2.namedWindow("board", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    full = False
    while True:
        cv2.imshow("board", board)
        key = cv2.waitKey(50) & 0xFF
        if key == ord("f"):
            full = not full
            cv2.setWindowProperty("board", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN if full else cv2.WINDOW_NORMAL)
        elif key == ord(" "):
            return True
        elif key in (ord("q"), 27):
            return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cam", choices=["A", "B"])
    p.add_argument("--row-mm", type=float, help="measured width of one row of 10 squares, in mm")
    p.add_argument("--views", type=int, default=25)
    p.add_argument("--save-board", help="write the board as a PNG and exit")
    args = p.parse_args()

    if args.save_board:
        cv2.imwrite(args.save_board, board_image(160, 80))
        print(f"wrote {args.save_board}: show it full-screen, then measure the 10-square row")
        return 0
    if not args.cam:
        p.error("--cam is required")
    cams = load_cameras()
    if args.cam not in cams:
        print(f"camera {args.cam} is not configured: run python -m vision.cams first")
        return 1
    cfg = cams[args.cam]

    print(__doc__)
    board = board_image()
    if not _wait_for_board(board):
        return 1
    row_mm = args.row_mm or float(input("Width of one row of 10 squares, in mm: "))
    square_m = row_mm / SQUARES_ACROSS / 1000
    obj = board_object_points(square_m)

    cam = Camera(args.cam, cfg.index, cfg.width, cfg.height)
    cam.start()
    views, descs = [], []
    last_corners, last_seq, size = None, -1, None
    try:
        while len(views) < args.views:
            status = board.copy()
            draw_text(status, f"{len(views)}/{args.views} captured", (10, 40), 0, 1.0)
            cv2.imshow("board", status)
            item = cam.latest()
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 1
            if item is None or item[0] == last_seq:
                continue
            last_seq, _, frame = item
            size = (frame.shape[1], frame.shape[0])
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCornersSB(gray, PATTERN, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
            preview = frame.copy()
            if found:
                c = corners.reshape(-1, 2)
                still = last_corners is not None and np.mean(np.linalg.norm(c - last_corners, axis=1)) < 1.5
                last_corners = c
                desc = view_descriptor(c, size)
                cv2.drawChessboardCorners(preview, PATTERN, corners, found)
                if still and is_novel(desc, descs):
                    views.append(c.astype(np.float32))
                    descs.append(desc)
                    cv2.rectangle(preview, (0, 0), (size[0] - 1, size[1] - 1), GREEN, 20)
                    print(f"captured {len(views)}/{args.views}")
            else:
                last_corners = None
            preview = fit(preview, (960, 540))
            draw_text(preview, f"{len(views)}/{args.views}: vary angle, distance, position; hold still to capture",
                      (10, 25), WHITE if found else RED, 0.6)
            cv2.imshow("camera", preview)
    finally:
        cam.stop()
        cv2.destroyAllWindows()

    rms, K, dist, errs, dropped = calibrate_with_outlier_drop([obj] * len(views), views, size)
    print(f"camera {args.cam}: RMS {rms:.3f} px ({len(errs)} views, {dropped} dropped)")
    print(f"  fx {K[0, 0]:.1f}  fy {K[1, 1]:.1f}  cx {K[0, 2]:.1f}  cy {K[1, 2]:.1f}")
    print(f"  worst views (px): {np.round(np.sort(errs)[-5:], 3).tolist()}")
    if rms >= 0.5:
        print("  WARNING: RMS >= 0.5 px. Re-run with steadier, more varied views.")
    cfg.K, cfg.dist, cfg.intrinsics_rms = K, dist, rms
    cfg.width, cfg.height = size
    if cfg.R is not None:
        print("  extrinsics cleared: re-run python -m vision.calib_extrinsics")
    cfg.R = cfg.t = cfg.extrinsics_source = cfg.extrinsics_rms = None
    save_cameras(cams)
    print("saved config/cameras.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_calib_intrinsics.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add vision/calib_intrinsics.py vision/tests/test_calib_intrinsics.py
git commit -m "vision: lens calibration from an on-screen checkerboard"
```

---

### Task 9: Camera pose from floor markers + placement hints

**Files:**
- Modify: `vision/markers.py` (append PnP, averaging, hints, warnings)
- Create: `vision/calib_extrinsics.py`, `vision/tests/test_markers.py`

**Interfaces:**
- Consumes: `MatConfig`, `project`, `cam_center`, `angle_between_deg`, `Camera`, `MarkerDetector`, `draw_text`, `fit`.
- Produces (in `vision.markers`): `average_detections(history, min_fraction=0.5) -> dict[int, ndarray(4,2)]`; `solve_camera_pose(obs, mat, K, dist) -> (R, t, rms_px) | None`; `placement_hint(R, t, target_eye, aim, tol_position_m, tol_angle_deg) -> dict` (keys `ok, dx_cm, dy_cm, dist_cm, yaw_deg, pitch_deg, msg`); `camera_warnings(R, t) -> list[str]`. CLI `python -m vision.calib_extrinsics [--seconds 3]`.

- [ ] **Step 1: Write failing tests `vision/tests/test_markers.py`**

```python
import cv2
import numpy as np

from vision.config import MatConfig
from vision.frames import cam_center, look_at_pose, project, rotation_angle_deg
from vision.markers import (
    MarkerDetector, aruco_dictionary, average_detections, camera_warnings, placement_hint, solve_camera_pose,
)
from vision.tests.synth import K_DEFAULT, make_cam

MAT = MatConfig()
AIM = np.array([0.0, 0.8, 0.0])
TARGET_A = np.array([1.98, 0.85, 1.98])


def _obs(cam, ids=(0, 1, 2, 3)):
    corners = MAT.marker_corners()
    return {i: project(corners[i], cam.K, cam.dist, cam.R, cam.t) for i in ids}


def test_solve_recovers_target_pose():
    cam = make_cam("A", TARGET_A)
    R, t, rms = solve_camera_pose(_obs(cam), MAT, cam.K, cam.dist)
    np.testing.assert_allclose(cam_center(R, t), TARGET_A, atol=1e-4)
    assert rotation_angle_deg(R, cam.R) < 0.01 and rms < 1e-3


def test_solve_with_two_markers_and_none():
    cam = make_cam("B", (-1.98, 0.85, 1.98))
    R, t, _ = solve_camera_pose(_obs(cam, ids=(1, 3)), MAT, cam.K, cam.dist)
    np.testing.assert_allclose(cam_center(R, t), cam_center(cam.R, cam.t), atol=1e-3)
    assert solve_camera_pose({}, MAT, cam.K, cam.dist) is None


def _render(cam, size=(1280, 720)):
    img = np.full((size[1], size[0]), 255, np.uint8)
    d = aruco_dictionary(MAT.aruco_dictionary)
    for i, corners3d in MAT.marker_corners().items():
        tile = cv2.aruco.generateImageMarker(d, i, 240)
        src = np.float32([[0, 0], [240, 0], [240, 240], [0, 240]])
        dst = project(corners3d, cam.K, cam.dist, cam.R, cam.t).astype(np.float32)
        H = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(tile, H, size, borderValue=255)
        mask = cv2.warpPerspective(np.full_like(tile, 255), H, size, borderValue=0) > 0
        img[mask] = warped[mask]
    return img


def test_detect_rendered_markers_then_solve():
    cam = make_cam("T", (0.0, 1.6, 1.6), target=(0.0, 0.0, 0.0))
    det = MarkerDetector(MAT).detect(_render(cam))
    assert set(det) == {0, 1, 2, 3}
    expected = _obs(cam)
    for i in det:
        assert np.max(np.linalg.norm(det[i] - expected[i], axis=1)) < 1.5
    R, t, _ = solve_camera_pose(det, MAT, cam.K, cam.dist)
    assert np.linalg.norm(cam_center(R, t) - cam_center(cam.R, cam.t)) < 0.02
    assert rotation_angle_deg(R, cam.R) < 0.5


def test_average_detections_requires_half_the_frames():
    a = {0: np.zeros((4, 2)), 1: np.ones((4, 2))}
    b = {0: np.full((4, 2), 2.0)}
    avg = average_detections([a, b, b])
    assert set(avg) == {0} and np.allclose(avg[0], 2.0)
    assert average_detections([]) == {}


def test_placement_hint_ok_at_target():
    R, t = look_at_pose(TARGET_A, AIM)
    h = placement_hint(R, t, TARGET_A, AIM, 0.10, 5.0)
    assert h["ok"] and h["msg"] == "OK"


def test_placement_hint_says_move_left():
    R, t = look_at_pose(TARGET_A, AIM)
    right = R[0].copy()
    right[1] = 0
    right /= np.linalg.norm(right)
    R2, t2 = look_at_pose(TARGET_A + 0.15 * right, AIM)
    h = placement_hint(R2, t2, TARGET_A, AIM, 0.10, 5.0)
    assert not h["ok"] and h["dx_cm"] == -15 and h["msg"].startswith("Move 15 cm left")


def test_camera_warnings():
    R, t = look_at_pose(TARGET_A, AIM)
    assert camera_warnings(R, t) == []
    R, t = look_at_pose(np.array([1.0, -0.5, 2.0]), AIM)
    assert any("below the floor" in w for w in camera_warnings(R, t))
    R, t = look_at_pose(np.array([1.0, 0.85, -2.0]), AIM)
    assert any("behind" in w for w in camera_warnings(R, t))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_markers.py -q`
Expected: FAIL with `ImportError: cannot import name 'average_detections'`.

- [ ] **Step 3: Append to `vision/markers.py`**

Add `from vision.frames import angle_between_deg, cam_center, project` to the imports, then append:

```python
def average_detections(history: list[dict[int, np.ndarray]], min_fraction: float = 0.5) -> dict[int, np.ndarray]:
    """Median corners per marker over a window; markers seen in fewer than min_fraction of frames are dropped."""
    if not history:
        return {}
    seen: dict[int, list[np.ndarray]] = {}
    for det in history:
        for i, c in det.items():
            seen.setdefault(i, []).append(c)
    return {i: np.median(np.stack(cs), axis=0) for i, cs in seen.items() if len(cs) >= min_fraction * len(history)}


def solve_camera_pose(obs: dict[int, np.ndarray], mat: MatConfig, K, dist):
    """Camera pose (R, t, reprojection RMS px) from marker corners; None if no markers."""
    corners = mat.marker_corners()
    ids = [i for i in sorted(obs) if i in corners]
    if not ids:
        return None
    obj = np.concatenate([corners[i] for i in ids]).astype(np.float64)
    img = np.concatenate([obs[i] for i in ids]).astype(np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    R, _ = cv2.Rodrigues(rvec)
    t = tvec.ravel()
    rms = float(np.sqrt(np.mean(np.sum((project(obj, K, dist, R, t) - img) ** 2, axis=1))))
    return R, t, rms


def _floor_unit(v) -> np.ndarray:
    v = np.array(v, float)
    v[1] = 0.0
    return v / np.linalg.norm(v)


def placement_hint(R, t, target_eye, aim, tol_position_m: float, tol_angle_deg: float) -> dict:
    """How to move/turn a camera to reach its target, in the camera's own terms (as if standing behind it)."""
    C = cam_center(R, t)
    fwd = np.asarray(R[2], float)
    right_f = _floor_unit(R[0])
    aim_dir = (np.asarray(aim, float) - C) / np.linalg.norm(np.asarray(aim, float) - C)
    aim_f = _floor_unit(aim_dir)
    fwd_f = _floor_unit(fwd)
    delta = np.asarray(target_eye, float) - C
    dx, dy, dfwd = float(delta @ right_f), float(delta[1]), float(delta @ aim_f)
    yaw = float(np.degrees(np.arctan2(aim_f @ right_f, aim_f @ fwd_f)))
    pitch = float(np.degrees(np.arcsin(np.clip(aim_dir[1], -1, 1)) - np.arcsin(np.clip(fwd[1], -1, 1))))
    ok = np.linalg.norm(delta) <= tol_position_m and angle_between_deg(fwd, aim_dir) <= tol_angle_deg
    parts = []
    if abs(dx) >= 0.02:
        parts.append(f"move {abs(dx) * 100:.0f} cm {'right' if dx > 0 else 'left'}")
    if abs(dfwd) >= 0.02:
        parts.append(f"move {abs(dfwd) * 100:.0f} cm {'closer' if dfwd > 0 else 'back'}")
    if abs(dy) >= 0.02:
        parts.append(f"{'raise' if dy > 0 else 'lower'} {abs(dy) * 100:.0f} cm")
    if abs(yaw) >= 1:
        parts.append(f"turn {'right' if yaw > 0 else 'left'} {abs(yaw):.0f}°")
    if abs(pitch) >= 1:
        parts.append(f"tilt {'up' if pitch > 0 else 'down'} {abs(pitch):.0f}°")
    if ok or not parts:
        msg = "OK"
    else:
        msg = ", ".join(parts)
        msg = msg[0].upper() + msg[1:]
    return {"ok": bool(ok), "dx_cm": round(dx * 100), "dy_cm": round(dy * 100), "dist_cm": round(dfwd * 100),
            "yaw_deg": round(yaw, 1), "pitch_deg": round(pitch, 1), "msg": msg}


def camera_warnings(R, t) -> list[str]:
    C = cam_center(R, t)
    warnings = []
    if C[1] < 0:
        warnings.append("camera is below the floor: check marker IDs and that their top edges face Mira")
    if C[2] < 0:
        warnings.append("camera is behind the mat centre: cameras belong on the Mira (+z) side")
    return warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_markers.py -q`
Expected: all PASS.

- [ ] **Step 5: Implement `vision/calib_extrinsics.py`**

```python
"""Place both cameras using the four floor markers.

    python -m vision.calib_extrinsics

Shows both cameras with detected markers and live placement hints toward the target
spots (2.8 m from mat centre at ±45°, lens 0.85 m, aimed 0.8 m above mat centre).
Adjust the cameras until both say OK, then press s to save (q quits without saving).
"""
from __future__ import annotations

import argparse
import time
from collections import deque

import cv2
import numpy as np

from vision.capture import Camera
from vision.config import load_cameras, load_mat, save_cameras
from vision.draw import GREEN, RED, YELLOW, draw_text, fit, pt
from vision.frames import cam_center
from vision.markers import MarkerDetector, average_detections, camera_warnings, placement_hint, solve_camera_pose


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=3.0, help="averaging window")
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    missing = [n for n in ("A", "B") if n not in cams or not cams[n].has_intrinsics]
    if missing:
        print(f"camera(s) {missing} need lens calibration first: python -m vision.calib_intrinsics --cam X")
        return 1

    detector = MarkerDetector(mat)
    cameras = {n: Camera(n, cams[n].index, cams[n].width, cams[n].height) for n in ("A", "B")}
    for c in cameras.values():
        c.start()
    history = {n: deque(maxlen=int(args.seconds * 30)) for n in cameras}
    last_seq = {n: -1 for n in cameras}
    results: dict[str, tuple] = {}
    last_solve = last_print = 0.0
    print(__doc__)
    try:
        while True:
            tiles = []
            for n, cam in cameras.items():
                item = cam.latest()
                if item is None:
                    tiles.append(np.zeros((360, 640, 3), np.uint8))
                    continue
                seq, _, frame = item
                if seq != last_seq[n]:
                    last_seq[n] = seq
                    history[n].append(detector.detect(frame))
                tile = fit(frame, (640, 360))
                scale = np.array([640 / frame.shape[1], 360 / frame.shape[0]])
                for i, c in (history[n][-1] if history[n] else {}).items():
                    cv2.polylines(tile, [(c * scale).astype(np.int32)], True, YELLOW, 2)
                    draw_text(tile, str(i), pt(c[0] * scale), YELLOW)
                draw_text(tile, n, (8, 22))
                if n in results:
                    _, _, rms, obs, hint, warns = results[n]
                    draw_text(tile, f"markers {sorted(obs)}  rms {rms:.2f} px", (8, 44))
                    draw_text(tile, hint["msg"], (8, 66), GREEN if hint["ok"] else YELLOW, 0.6)
                    for k, w in enumerate(warns):
                        draw_text(tile, w, (8, 88 + 18 * k), RED, 0.45)
                tiles.append(tile)
            now = time.monotonic()
            if now - last_solve > 0.5:
                last_solve = now
                for n in cameras:
                    obs = average_detections(list(history[n]))
                    sol = solve_camera_pose(obs, mat, cams[n].K, cams[n].dist)
                    if sol is None:
                        results.pop(n, None)
                        continue
                    R, t, rms = sol
                    hint = placement_hint(R, t, mat.camera_targets[n], mat.aim_point, mat.tol_position_m, mat.tol_angle_deg)
                    warns = camera_warnings(R, t) + (["only one marker seen: pose is unreliable"] if len(obs) < 2 else [])
                    results[n] = (R, t, rms, obs, hint, warns)
            if now - last_print > 1.0:
                last_print = now
                for n, (R, t, rms, obs, hint, _) in sorted(results.items()):
                    c = cam_center(R, t)
                    print(f"{n}: markers {sorted(obs)} rms {rms:.2f}px  at ({c[0]:+.2f}, {c[1]:.2f}, {c[2]:+.2f}) "
                          f"{np.linalg.norm(c[[0, 2]]):.2f} m from centre  -> {hint['msg']}")
            cv2.imshow("Mira extrinsics", np.hstack(tiles))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("s"):
                if set(results) != {"A", "B"}:
                    print("both cameras need a marker solution before saving")
                    continue
                for n, (R, t, rms, obs, _, _) in results.items():
                    cfg = cams[n]
                    cfg.R, cfg.t, cfg.extrinsics_rms, cfg.marker_obs = R, t, rms, obs
                    cfg.extrinsics_source = "markers"
                save_cameras(cams)
                print("saved marker extrinsics. Next: python -m vision.calib_refine")
                return 0
    finally:
        for c in cameras.values():
            c.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add vision/markers.py vision/calib_extrinsics.py vision/tests/test_markers.py
git commit -m "vision: camera pose from floor markers with placement hints"
```

---

### Task 10: Body-based bundle adjustment + `calib_refine`

**Files:**
- Create: `vision/bundle.py`, `vision/calib_refine.py`, `vision/tests/test_bundle.py`

**Interfaces:**
- Consumes: `CameraConfig`, `MatConfig`, `triangulate_points`, `cam_center`, `rotation_angle_deg`, `project`, `PoseResult`, `LiveSource`, `Pipeline`, `Recorder`, `default_path`, `iter_frames`, `mono_to_unix`, `draw_skeleton`, `draw_text`, `fit`.
- Produces: `BA_JOINTS`; `BundleReport` (fields `ok, reasons, R, t, body_rms_before, body_rms_after, marker_rms_before, marker_rms_after, centers, moved_m, rotated_deg, baseline_m, median_angle_deg, n_frames, n_points`; `summary() -> str`); `rodrigues(r) -> (3,3)`; `evaluate_guards(body_before, body_after, moved_m, rotated_deg, marker_before, marker_after, max_move_m=0.5, max_rot_deg=15.0) -> list[str]`; `refine_extrinsics(cams, mat, pairs: list[tuple[PoseResult|None, PoseResult|None]], marker_weight=5.0, min_vis=0.8, max_frames=400, drop_initial_px=40.0) -> BundleReport` (raises `ValueError` when inputs are insufficient). CLI `python -m vision.calib_refine [--seconds 20] [--from FILE] [--force]`.

- [ ] **Step 1: Write failing tests `vision/tests/test_bundle.py`**

```python
from dataclasses import replace

import cv2
import numpy as np
import pytest

from vision.bundle import evaluate_guards, refine_extrinsics, rodrigues
from vision.config import MatConfig
from vision.frames import cam_center, project, rotation_angle_deg
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams

MAT = MatConfig()


def _scene(n_frames=120, seed=0):
    rng = np.random.default_rng(seed)
    truth = target_cams()
    for cam in truth.values():
        cam.marker_obs = {i: project(c, cam.K, cam.dist, cam.R, cam.t) + rng.normal(0, 0.2, (4, 2))
                          for i, c in MAT.marker_corners().items()}
    skel = standing_skeleton()
    pairs = []
    for f in range(n_frames):
        yaw = rng.uniform(-0.6, 0.6)
        Ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
        X = skel @ Ry.T + [rng.uniform(-0.2, 0.2), 0, rng.uniform(-0.6, 0.6)] + rng.normal(0, 0.03, skel.shape)
        pairs.append((pose_from_points(truth["A"], X, t=f / 30, noise_px=0.5, rng=rng),
                      pose_from_points(truth["B"], X, t=f / 30, noise_px=0.5, rng=rng)))
    return truth, pairs, rng


def _perturbed(cam, rng, move_m, rot_deg):
    d = rng.normal(size=3)
    axis = rng.normal(size=3)
    dR, _ = cv2.Rodrigues(axis / np.linalg.norm(axis) * np.radians(rot_deg))
    R2 = dR @ cam.R
    C2 = cam_center(cam.R, cam.t) + d / np.linalg.norm(d) * move_m
    return replace(cam, R=R2, t=-R2 @ C2)


def test_rodrigues_matches_opencv():
    r = np.array([0.1, -0.4, 0.25])
    np.testing.assert_allclose(rodrigues(r), cv2.Rodrigues(r)[0], atol=1e-12)
    np.testing.assert_allclose(rodrigues(np.zeros(3)), np.eye(3))


def test_refine_recovers_perturbed_poses():
    truth, pairs, rng = _scene()
    start = {n: _perturbed(c, rng, 0.05, 1.5) for n, c in truth.items()}
    report = refine_extrinsics(start, MAT, pairs)
    assert report.ok, report.reasons
    assert report.body_rms_after < report.body_rms_before and report.body_rms_after < 1.0
    for n in ("A", "B"):
        assert np.linalg.norm(report.centers[n] - cam_center(truth[n].R, truth[n].t)) < 0.01
        assert rotation_angle_deg(report.R[n], truth[n].R) < 0.3
    assert 75 < report.median_angle_deg < 100
    assert "body reprojection" in report.summary()


def test_too_few_observations_raises():
    truth, pairs, _ = _scene(n_frames=2)
    with pytest.raises(ValueError, match="not enough"):
        refine_extrinsics(truth, MAT, pairs)


def test_missing_markers_raises():
    truth, pairs, _ = _scene(n_frames=10)
    for cam in truth.values():
        cam.marker_obs = None
    with pytest.raises(ValueError, match="marker"):
        refine_extrinsics(truth, MAT, pairs)


def test_guards():
    ok = evaluate_guards(5.0, 2.0, {"A": 0.1}, {"A": 2.0}, {"A": 0.8}, {"A": 1.2})
    assert ok == []
    bad = evaluate_guards(2.0, 3.0, {"A": 0.7}, {"A": 20.0}, {"A": 0.5}, {"A": 3.0})
    assert len(bad) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_bundle.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/bundle.py`**

```python
"""Refine both camera poses from body observations, pinned to the floor markers.

Parameters: each camera's pose (Rodrigues vector + translation, mat -> camera) and one 3D point
per selected (frame, joint). Residuals: pinhole reprojection of the body points in both views and
of the fixed marker corners (weighted), on undistorted pixel observations. Intrinsics are fixed.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix

from vision.config import CameraConfig, MatConfig
from vision.frames import cam_center, rotation_angle_deg
from vision.triangulate import triangulate_points
from vision.types import PoseResult

BA_JOINTS = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32]
NAMES = ("A", "B")
MIN_POINTS = 50


@dataclass
class BundleReport:
    ok: bool
    reasons: list[str]
    R: dict[str, np.ndarray]
    t: dict[str, np.ndarray]
    body_rms_before: float
    body_rms_after: float
    marker_rms_before: dict[str, float]
    marker_rms_after: dict[str, float]
    centers: dict[str, np.ndarray]
    moved_m: dict[str, float]
    rotated_deg: dict[str, float]
    baseline_m: float
    median_angle_deg: float
    n_frames: int
    n_points: int

    def summary(self) -> str:
        lines = [
            f"frames used: {self.n_frames}, joint observations: {self.n_points}",
            f"body reprojection RMS: {self.body_rms_before:.2f} px -> {self.body_rms_after:.2f} px",
        ]
        for n in NAMES:
            c = self.centers[n]
            mb, ma = self.marker_rms_before.get(n), self.marker_rms_after.get(n)
            markers = f"marker RMS {mb:.2f} -> {ma:.2f} px, " if mb is not None else "no markers, "
            lines.append(f"camera {n}: {markers}centre ({c[0]:+.3f}, {c[1]:.3f}, {c[2]:+.3f}) m, "
                         f"moved {self.moved_m[n] * 100:.1f} cm, rotated {self.rotated_deg[n]:.2f}°")
        lines.append(f"baseline {self.baseline_m:.3f} m, median triangulation angle {self.median_angle_deg:.1f}°")
        lines.append("OK" if self.ok else "REJECTED: " + "; ".join(self.reasons))
        return "\n".join(lines)


def rodrigues(r) -> np.ndarray:
    r = np.asarray(r, float)
    theta = np.linalg.norm(r)
    if theta < 1e-12:
        return np.eye(3)
    k = r / theta
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * Kx + (1 - np.cos(theta)) * Kx @ Kx


def _pinhole(P, K, R, t) -> np.ndarray:
    Xc = P @ R.T + t
    uv = Xc[:, :2] / Xc[:, 2:3]
    return uv @ K[:2, :2].T + K[:2, 2]


def _undistort(uv, cam: CameraConfig) -> np.ndarray:
    return cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), cam.K, cam.dist, P=cam.K).reshape(-1, 2)


def _rms(err2d: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum(err2d.reshape(-1, 2) ** 2, axis=1)))) if err2d.size else float("nan")


def evaluate_guards(body_before, body_after, moved_m, rotated_deg, marker_before, marker_after,
                    max_move_m: float = 0.5, max_rot_deg: float = 15.0) -> list[str]:
    reasons = []
    if not body_after < body_before:
        reasons.append(f"body reprojection did not improve ({body_before:.2f} -> {body_after:.2f} px)")
    for n, m in moved_m.items():
        if m > max_move_m:
            reasons.append(f"camera {n} moved {m:.2f} m from the marker solution (limit {max_move_m} m)")
    for n, r in rotated_deg.items():
        if r > max_rot_deg:
            reasons.append(f"camera {n} rotated {r:.1f}° from the marker solution (limit {max_rot_deg}°)")
    for n, after in marker_after.items():
        limit = max(2 * marker_before[n], 1.0)
        if after > limit:
            reasons.append(f"camera {n} marker RMS grew to {after:.2f} px (limit {limit:.2f} px)")
    return reasons


def _collect(pairs, min_vis, max_frames):
    both = [(a, b) for a, b in pairs if a is not None and b is not None and a.detected and b.detected]
    if len(both) > max_frames:
        both = [both[i] for i in np.linspace(0, len(both) - 1, max_frames).astype(int)]
    uv_a, uv_b, frame = [], [], []
    for f, (a, b) in enumerate(both):
        for j in BA_JOINTS:
            if a.vis[j] >= min_vis and b.vis[j] >= min_vis:
                uv_a.append(a.lm2d[j])
                uv_b.append(b.lm2d[j])
                frame.append(f)
    return np.array(uv_a, float).reshape(-1, 2), np.array(uv_b, float).reshape(-1, 2), np.array(frame, int)


def _sparsity(n_pts: int, marker_rows: dict[str, int]) -> coo_matrix:
    rows, cols = [], []
    i = np.arange(n_pts)
    for k, off in enumerate((0, 6)):  # body residuals of camera A then B
        base = k * 2 * n_pts
        for r in (0, 1):
            rr = base + 2 * i + r
            for c in range(6):
                rows.append(rr)
                cols.append(np.full(n_pts, off + c))
            for c in range(3):
                rows.append(rr)
                cols.append(12 + 3 * i + c)
    row = 4 * n_pts
    for n in NAMES:
        m = marker_rows.get(n, 0)
        if m:
            off = 0 if n == "A" else 6
            rr = np.repeat(np.arange(row, row + m), 6)
            cc = np.tile(np.arange(off, off + 6), m)
            rows.append(rr)
            cols.append(cc)
            row += m
    r, c = np.concatenate(rows), np.concatenate(cols)
    return coo_matrix((np.ones(len(r)), (r, c)), shape=(row, 12 + 3 * n_pts))


def refine_extrinsics(cams: dict[str, CameraConfig], mat: MatConfig, pairs, marker_weight: float = 5.0,
                      min_vis: float = 0.8, max_frames: int = 400, drop_initial_px: float = 40.0) -> BundleReport:
    for n in NAMES:
        if n not in cams or not cams[n].calibrated:
            raise ValueError(f"camera {n} needs lens and marker calibration before refinement")
    ca, cb = cams["A"], cams["B"]
    corners3d = mat.marker_corners()
    markers = {}
    for n in NAMES:
        obs = cams[n].marker_obs or {}
        ids = sorted(set(obs) & set(corners3d))
        if ids:
            markers[n] = (np.concatenate([corners3d[i] for i in ids]),
                          _undistort(np.concatenate([obs[i] for i in ids]), cams[n]))
    if not markers:
        raise ValueError("no marker observations stored: run python -m vision.calib_extrinsics first")

    uv_a, uv_b, frame = _collect(pairs, min_vis, max_frames)
    X0, err0, _ = triangulate_points(uv_a, uv_b, ca, cb)
    keep = np.isfinite(err0) & (err0 <= drop_initial_px)
    for f in np.unique(frame):
        m = frame == f
        if not np.isfinite(err0[m]).any() or np.nanmedian(err0[m]) > drop_initial_px:
            keep[m] = False
    uv_a, uv_b, X0, frame = uv_a[keep], uv_b[keep], X0[keep], frame[keep]
    n_pts = len(X0)
    if n_pts < MIN_POINTS:
        raise ValueError(f"not enough body observations ({n_pts}; need ≥ {MIN_POINTS} joints seen well by both "
                         f"cameras). Stay in view of both cameras and move around the mat.")

    obs = {"A": _undistort(uv_a, ca), "B": _undistort(uv_b, cb)}
    K = {n: cams[n].K for n in NAMES}
    x0 = np.concatenate([cv2.Rodrigues(ca.R)[0].ravel(), ca.t, cv2.Rodrigues(cb.R)[0].ravel(), cb.t, X0.ravel()])

    def pieces(x):
        poses = {"A": (rodrigues(x[0:3]), x[3:6]), "B": (rodrigues(x[6:9]), x[9:12])}
        P = x[12:].reshape(-1, 3)
        body = {n: _pinhole(P, K[n], *poses[n]) - obs[n] for n in NAMES}
        mark = {n: _pinhole(M3, K[n], *poses[n]) - m2 for n, (M3, m2) in markers.items()}
        return body, mark, poses

    def residuals(x):
        body, mark, _ = pieces(x)
        return np.concatenate([body["A"].ravel(), body["B"].ravel()] +
                              [marker_weight * mark[n].ravel() for n in NAMES if n in mark])

    body0, mark0, poses0 = pieces(x0)
    sparsity = _sparsity(n_pts, {n: 2 * len(m[0]) for n, m in markers.items()})
    result = least_squares(residuals, x0, jac_sparsity=sparsity, loss="soft_l1", f_scale=2.0,
                           x_scale="jac", method="trf", max_nfev=200)
    body1, mark1, poses1 = pieces(result.x)

    body_before = _rms(np.concatenate([body0["A"], body0["B"]]))
    body_after = _rms(np.concatenate([body1["A"], body1["B"]]))
    marker_before = {n: _rms(v) for n, v in mark0.items()}
    marker_after = {n: _rms(v) for n, v in mark1.items()}
    R = {n: poses1[n][0] for n in NAMES}
    t = {n: np.asarray(poses1[n][1], float).copy() for n in NAMES}
    centers = {n: cam_center(R[n], t[n]) for n in NAMES}
    moved = {n: float(np.linalg.norm(centers[n] - cam_center(*poses0[n]))) for n in NAMES}
    rotated = {n: rotation_angle_deg(R[n], poses0[n][0]) for n in NAMES}
    P = result.x[12:].reshape(-1, 3)
    ra, rb = P - centers["A"], P - centers["B"]
    cos = np.sum(ra * rb, axis=1) / (np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1))
    reasons = evaluate_guards(body_before, body_after, moved, rotated, marker_before, marker_after)
    return BundleReport(
        ok=not reasons, reasons=reasons, R=R, t=t,
        body_rms_before=body_before, body_rms_after=body_after,
        marker_rms_before=marker_before, marker_rms_after=marker_after,
        centers=centers, moved_m=moved, rotated_deg=rotated,
        baseline_m=float(np.linalg.norm(centers["A"] - centers["B"])),
        median_angle_deg=float(np.degrees(np.median(np.arccos(np.clip(cos, -1, 1))))),
        n_frames=int(len(np.unique(frame))), n_points=n_pts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_bundle.py -q`
Expected: all PASS (the refine test should take a few seconds).

- [ ] **Step 5: Implement `vision/calib_refine.py`**

```python
"""Refine both camera poses from ~20 s of you moving on the mat.

    python -m vision.calib_refine            # live capture (also saved as a recording)
    python -m vision.calib_refine --from data/recordings/refine-<time>.jsonl

During capture: walk around the mat, squat, raise and spread your arms, turn.
Stay visible to both cameras. Requires lens calibration and calib_extrinsics first.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from vision.bundle import refine_extrinsics
from vision.capture import mono_to_unix
from vision.config import load_cameras, load_mat, mat_raw, save_cameras
from vision.draw import GREEN, YELLOW, draw_skeleton, draw_text, fit
from vision.pipeline import Pipeline
from vision.record import Recorder, default_path
from vision.replay import iter_frames

COUNTDOWN_S = 3.0


def capture_live(cams, model: str, seconds: float):
    from vision.live import LiveSource

    pairs = []
    path = default_path("refine-")
    pipeline = Pipeline(cams)
    with LiveSource(cams, model) as src, Recorder(path, cams, mat_raw()) as rec:
        start = time.monotonic()
        while (elapsed := time.monotonic() - start) < COUNTDOWN_S + seconds:
            capturing = elapsed >= COUNTDOWN_S
            for pair in src.poll(0.005):
                if capturing:
                    rec.write(pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s))
                    pairs.append((pair.a, pair.b))
            tiles = []
            for n, v in sorted(src.latest_frames().items()):
                if v is None:
                    tiles.append(np.zeros((360, 640, 3), np.uint8))
                    continue
                frame, pose = v
                tile = fit(frame, (640, 360))
                if pose.detected:
                    draw_skeleton(tile, pose.lm2d * [640 / frame.shape[1], 360 / frame.shape[0]], ok=pose.vis >= 0.5, color=GREEN)
                tiles.append(tile)
            msg = (f"capturing: {COUNTDOWN_S + seconds - elapsed:.0f} s left, keep moving"
                   if capturing else f"get ready: {COUNTDOWN_S - elapsed:.0f}")
            img = np.hstack(tiles)
            draw_text(img, msg, (10, 30), GREEN if capturing else YELLOW, 0.8)
            cv2.imshow("Mira refine", img)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    cv2.destroyAllWindows()
    print(f"saved capture to {path} ({len(pairs)} frames)")
    return pairs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--from", dest="from_path", type=Path)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    p.add_argument("--marker-weight", type=float, default=5.0)
    p.add_argument("--force", action="store_true", help="save even if the guards reject the result")
    args = p.parse_args()

    cams, mat = load_cameras(), load_mat()
    if args.from_path:
        pairs = [(raw.get("A"), raw.get("B")) for _, raw, _ in iter_frames(args.from_path)]
    else:
        print(__doc__)
        pairs = capture_live(cams, args.model, args.seconds)
    try:
        report = refine_extrinsics(cams, mat, pairs, marker_weight=args.marker_weight)
    except ValueError as e:
        print(f"refinement not possible: {e}")
        return 1
    print(report.summary())
    if not report.ok and not args.force:
        print("not saved (use --force to override)")
        return 1
    for n in ("A", "B"):
        cams[n].R, cams[n].t = report.R[n], report.t[n]
        cams[n].extrinsics_source, cams[n].extrinsics_rms = "bundle", report.body_rms_after
    save_cameras(cams)
    print("saved refined extrinsics. Next: python -m vision.debug_view and python -m vision.checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add vision/bundle.py vision/calib_refine.py vision/tests/test_bundle.py
git commit -m "vision: body-based bundle adjustment and calib_refine tool"
```

---

### Task 11: Sanity checks

**Files:**
- Create: `vision/checks.py`, `vision/tests/test_checks.py`

**Interfaces:**
- Consumes: `PipelineOutput`, `Pipeline`, `CameraConfig`, `MatConfig`, `triangulate_points`, `replay_outputs`, `LiveSource`, `mono_to_unix`.
- Produces: `Check(name, value, unit, target, passed)`; `bone_length_stats(seq) -> dict[str, (mean, std)]`; `knee_angle_deg(joints, side) -> float`; `triangulate_marker_centers(cam_a, cam_b) -> dict[int, ndarray(3)]`; `marker_center_distances(centers, mat) -> list[(label, measured, expected)]`; `run_checks(outputs, cams, mat, fps=None) -> list[Check]`; `format_checks(checks) -> str`; CLI `python -m vision.checks [--seconds 5] [--from FILE]`.

- [ ] **Step 1: Write failing tests `vision/tests/test_checks.py`**

```python
import numpy as np

from vision.checks import (
    bone_length_stats, format_checks, knee_angle_deg, marker_center_distances, run_checks,
    triangulate_marker_centers,
)
from vision.config import MatConfig
from vision.frames import project
from vision.pipeline import Pipeline
from vision.tests.synth import pose_from_points, standing_skeleton, target_cams

MAT = MatConfig()


def _cams():
    cams = target_cams()
    for cam in cams.values():
        cam.intrinsics_rms = 0.3
        cam.marker_obs = {i: project(c, cam.K, cam.dist, cam.R, cam.t) for i, c in MAT.marker_corners().items()}
    return cams


def test_bone_lengths_and_knee_angle():
    X = standing_skeleton()
    stats = bone_length_stats(np.stack([X] * 20))
    assert abs(stats["thigh_l"][0] - np.linalg.norm(X[23] - X[25])) < 1e-12 and stats["thigh_l"][1] < 1e-12
    assert 170 < knee_angle_deg(X, "l") < 180
    bent = X.copy()
    bent[25] = bent[23] + [0, -0.43, 0]  # thigh vertical
    bent[27] = bent[25] + [0, 0, -0.43]  # shin horizontal: 90° at the knee
    assert abs(knee_angle_deg(bent, "l") - 90) < 1e-6


def test_marker_centres_and_distances():
    cams = _cams()
    centers = triangulate_marker_centers(cams["A"], cams["B"])
    np.testing.assert_allclose(centers[0], MAT.marker_centers()[0], atol=1e-3)
    dists = {label: (m, e) for label, m, e in marker_center_distances(centers, MAT)}
    assert abs(dists["0–2"][1] - 1.52) < 1e-9 and abs(dists["0–1"][1] - 0.40) < 1e-9
    assert abs(dists["0–2"][0] - 1.52) < 2e-3


def test_run_checks_all_pass_on_clean_synthetic_data():
    cams, X = _cams(), standing_skeleton()
    rng = np.random.default_rng(0)
    pipe = Pipeline(cams)
    outs = [pipe.process(k / 30, pose_from_points(cams["A"], X, noise_px=0.3, rng=rng),
                         pose_from_points(cams["B"], X, noise_px=0.3, rng=rng), 0.005) for k in range(40)]
    checks = run_checks(outs, cams, MAT, fps={"A": 30.0, "B": 29.5})
    names = {c.name for c in checks}
    assert {"lens RMS A", "frames tracked in 3D", "mean body reprojection", "thigh_l length std",
            "standing knee angle l", "median triangulation angle", "marker distance 0–2",
            "pair skew p95", "capture fps A"} <= names
    failed = [c for c in checks if c.passed is False]
    assert failed == [], format_checks(failed)
    assert "PASS" in format_checks(checks)


def test_run_checks_with_no_outputs():
    checks = run_checks([], {}, MAT)
    assert all(c.passed is None for c in checks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest vision/tests/test_checks.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `vision/checks.py`**

```python
"""Numeric sanity checks for the 3D skeleton.

    python -m vision.checks               # 5 s live: stand still, facing Mira, in view of both cameras
    python -m vision.checks --from FILE   # on a recording, re-triangulated with the current calibration
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vision.config import CameraConfig, MatConfig, load_cameras, load_mat
from vision.pipeline import Pipeline, PipelineOutput
from vision.triangulate import triangulate_points

BONES_CHECKED = {"thigh_l": (23, 25), "thigh_r": (24, 26), "shin_l": (25, 27), "shin_r": (26, 28)}
KNEE = {"l": (23, 25, 27), "r": (24, 26, 28)}
MARKER_PAIRS = [(0, 2), (1, 3), (0, 1), (2, 3)]


@dataclass
class Check:
    name: str
    value: float | None
    unit: str
    target: str
    passed: bool | None  # None = could not be measured


def bone_length_stats(seq: np.ndarray) -> dict[str, tuple[float, float]]:
    out = {}
    for name, (i, j) in BONES_CHECKED.items():
        lengths = np.linalg.norm(seq[:, i] - seq[:, j], axis=1)
        lengths = lengths[np.isfinite(lengths)]
        out[name] = (float(lengths.mean()), float(lengths.std())) if len(lengths) >= 10 else (float("nan"), float("nan"))
    return out


def knee_angle_deg(joints: np.ndarray, side: str) -> float:
    hip, knee, ankle = (joints[k] for k in KNEE[side])
    u, v = hip - knee, ankle - knee
    with np.errstate(invalid="ignore"):
        c = u @ v / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def triangulate_marker_centers(cam_a: CameraConfig, cam_b: CameraConfig) -> dict[int, np.ndarray]:
    obs_a, obs_b = cam_a.marker_obs or {}, cam_b.marker_obs or {}
    out = {}
    for i in sorted(set(obs_a) & set(obs_b)):
        X, _, _ = triangulate_points(obs_a[i], obs_b[i], cam_a, cam_b)
        out[i] = X.mean(axis=0)
    return out


def marker_center_distances(centers: dict[int, np.ndarray], mat: MatConfig) -> list[tuple[str, float, float]]:
    known = mat.marker_centers()
    return [(f"{i}–{j}", float(np.linalg.norm(centers[i] - centers[j])), float(np.linalg.norm(known[i] - known[j])))
            for i, j in MARKER_PAIRS if i in centers and j in centers]


def _check(name, value, unit, target, test) -> Check:
    ok = None if value is None or not np.isfinite(value) else bool(test(value))
    return Check(name, None if value is None or not np.isfinite(value) else float(value), unit, target, ok)


def run_checks(outputs: list[PipelineOutput], cams: dict[str, CameraConfig], mat: MatConfig,
               fps: dict[str, float] | None = None) -> list[Check]:
    checks = []
    for n in ("A", "B"):
        rms = cams[n].intrinsics_rms if n in cams else None
        checks.append(_check(f"lens RMS {n}", rms, "px", "< 0.5", lambda v: v < 0.5))

    n3d = sum(o.frame.tracking == "3d" for o in outputs)
    frac = 100.0 * n3d / len(outputs) if outputs else None
    checks.append(_check("frames tracked in 3D", frac, "%", "≥ 90", lambda v: v >= 90))

    tris = [o.tri for o in outputs if o.tri is not None]
    errs = np.concatenate([t.reproj_err[t.ok] for t in tris]) if tris else np.empty(0)
    checks.append(_check("mean body reprojection", float(errs.mean()) if errs.size else None, "px", "≤ 5", lambda v: v <= 5))

    seq = np.stack([o.frame.joints for o in outputs if o.frame.tracking == "3d"]) if n3d else None
    stats = bone_length_stats(seq) if seq is not None else {k: (float("nan"),) * 2 for k in BONES_CHECKED}
    for name, (_, std) in stats.items():
        checks.append(_check(f"{name} length std", std * 100, "cm", "< 1.0", lambda v: v < 1.0))
    for side in ("l", "r"):
        vals = [knee_angle_deg(j, side) for j in seq] if seq is not None else []
        vals = [v for v in vals if np.isfinite(v)]
        checks.append(_check(f"standing knee angle {side}", float(np.median(vals)) if vals else None, "°", "170–180",
                             lambda v: 170 <= v <= 180))

    angles = np.concatenate([t.angle_deg[t.ok] for t in tris]) if tris else np.empty(0)
    checks.append(_check("median triangulation angle", float(np.median(angles)) if angles.size else None, "°", "60–100",
                         lambda v: 60 <= v <= 100))

    if "A" in cams and "B" in cams and cams["A"].calibrated and cams["B"].calibrated:
        centers = triangulate_marker_centers(cams["A"], cams["B"])
        for label, measured, expected in marker_center_distances(centers, mat):
            checks.append(_check(f"marker distance {label}", measured * 100, "cm", f"{expected * 100:.1f} ± 2",
                                 lambda v, e=expected: abs(v / 100 - e) <= 0.02))

    skews = [abs(o.skew_s) * 1000 for o in outputs if o.skew_s is not None]
    checks.append(_check("pair skew p95", float(np.percentile(skews, 95)) if skews else None, "ms", "≤ 33",
                         lambda v: v <= 33))
    for n, v in sorted((fps or {}).items()):
        checks.append(_check(f"capture fps {n}", v, "fps", "≥ 20", lambda x: x >= 20))
    return checks


def format_checks(checks: list[Check]) -> str:
    lines = [f"{'check':32s} {'value':>10s}  {'target':14s} result"]
    for c in checks:
        value = "n/a" if c.value is None else f"{c.value:.2f} {c.unit}"
        result = {True: "PASS", False: "FAIL", None: "--"}[c.passed]
        lines.append(f"{c.name:32s} {value:>10s}  {c.target:14s} {result}")
    return "\n".join(lines)


def _live_outputs(cams, model: str, seconds: float):
    from vision.capture import mono_to_unix
    from vision.live import LiveSource

    outputs = []
    pipeline = Pipeline(cams)
    with LiveSource(cams, model) as src:
        print("Stand still on the mat, facing Mira, arms by your sides. Starting in 2 s ...")
        start = time.monotonic()
        while time.monotonic() - start < 2.0 + seconds:
            for pair in src.poll(0.01):
                out = pipeline.process(mono_to_unix(pair.t), pair.a, pair.b, pair.skew_s)
                if time.monotonic() - start >= 2.0:
                    outputs.append(out)
        fps = {n: s["fps"] for n, s in src.stats().items()}
    return outputs, fps


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--from", dest="from_path", type=Path)
    p.add_argument("--model", default="full", choices=["full", "heavy"])
    args = p.parse_args()
    cams, mat = load_cameras(), load_mat()
    if args.from_path:
        from vision.replay import replay_outputs

        outputs, fps = list(replay_outputs(args.from_path, retriangulate=True, cams=cams, realtime=False)), None
    else:
        outputs, fps = _live_outputs(cams, args.model, args.seconds)
    checks = run_checks(outputs, cams, mat, fps)
    print(format_checks(checks))
    return 0 if all(c.passed is not False for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest vision/tests/test_checks.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add vision/checks.py vision/tests/test_checks.py
git commit -m "vision: sanity checks (bones, knee angle, markers, reprojection, timing)"
```

---

### Task 12: README and hardware walkthrough

**Files:**
- Create: `vision/README.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Write `vision/README.md`**

````markdown
# vision

Two webcams → calibrated 3D skeleton in the mat frame. Design: `docs/superpowers/specs/2026-10-10-vision-cameras-to-3d-design.md`.

## Setup

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r vision/requirements.txt
python -m vision.models.download
python -m pytest
```

macOS asks for Camera permission for your terminal on first use.

## Mat frame

Origin at mat centre on the mat surface; +y up; +z toward Mira; +x toward the patient's left
(Mira's right), the side with markers 0 and 2. Right-handed, same as Three.js/ARKit. Joints in
`FrameResult.joints` are metres in this frame.

## Workflow

1. `python -m vision.cams`: assign A (front-left) and B (front-right).
2. `python -m vision.debug_view`: check both feeds, skeletons, ≥ 20 fps each (2D only until calibrated).
3. `python -m vision.calib_intrinsics --cam A` then `--cam B`: before mounting, point each webcam at the
   on-screen checkerboard; target RMS < 0.5 px.
4. Mount the cameras. `python -m vision.calib_extrinsics`: follow the hints until both say OK, press s.
5. `python -m vision.calib_refine`: move around the mat for 20 s; saves only if the fit improves.
6. `python -m vision.debug_view`: magenta (3D) dots should sit on the green (2D) skeletons; raise your
   left hand → it moves on the +x side (left in the top view, right in the front view).
7. `python -m vision.checks`: stand still for 5 s; everything should PASS.
8. Press r in the debug view to record; `python -m vision.replay FILE` or
   `python -m vision.debug_view --replay FILE [--retriangulate]` to play back.

If exposure changes make fps drop, lock exposure in the Logitech app: OpenCV on macOS cannot.

## For the team

```python
from vision.replay import replay
for frame in replay("data/recordings/<file>.jsonl"):
    frame.joints  # (33, 3) metres, mat frame; NaN where unknown
    frame.tracking  # "3d" | "single_cam_A" | "single_cam_B" | "lost"
```
````

- [ ] **Step 2: Commit**

```bash
git add vision/README.md
git commit -m "vision: README with setup and calibration workflow"
```

- [ ] **Step 3: Hardware walkthrough with Eric**

Run the README workflow steps 1–8 together and record in the PR description: per-camera fps, lens RMS, extrinsics placement messages, refine before/after RMS, and the `checks` table. Any FAIL becomes a follow-up item, not a silent pass.

- [ ] **Step 4: Whole-branch review**

Use superpowers:requesting-code-review on `vision/cameras-to-3d` against `main` (one review for the whole branch), then superpowers:receiving-code-review for the findings.
