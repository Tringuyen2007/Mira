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
from vision.moved import MoveMonitor
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


ROI_COLOR = (255, 255, 0)  # cyan: the area pose detection pays attention to
IGNORED_COLOR = (130, 130, 130)


def render_camera(image, pose: PoseResult | None, frame: FrameResult | None, cam: CameraConfig | None,
                  detector: MarkerDetector | None = None, ignored: list[PoseResult] | None = None,
                  roi=None) -> np.ndarray:
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
    if roi is not None:
        x0, y0, x1, y1 = roi
        cv2.rectangle(panel, pt(np.array([x0, y0]) * scale), pt(np.array([x1, y1]) * scale), ROI_COLOR, 2)
        draw_text(panel, "tracking area", pt(np.array([x0, y1]) * scale + [4, -6]), ROI_COLOR, 0.45)
    for other in ignored or []:
        if other.detected:
            draw_skeleton(panel, other.lm2d * scale, ok=other.vis >= 0.5, color=IGNORED_COLOR)
            top = other.lm2d[np.argmin(other.lm2d[:, 1])] * scale
            draw_text(panel, "ignored", pt(top + [-25, -8]), IGNORED_COLOR, 0.45)
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
    centers = mat.marker_centers()
    for i, corners in mat.marker_corners().items():
        cv2.fillPoly(img, [TOP(corners[:, [0, 2]]).astype(np.int32)], (230, 230, 230))
        draw_text(img, str(i), pt(TOP([centers[i][[0, 2]]])[0] + [-4, 4]), (0, 0, 0), 0.4)
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
                 detector: MarkerDetector | None = None, alarms: dict[str, str | None] | None = None) -> np.ndarray:
    frame = out.frame if out is not None else None
    panels = []
    for name in ("A", "B"):
        cam = cams.get(name)
        panel = render_camera(images.get(name), poses.get(name), frame, cam, detector,
                              (out.ignored or {}).get(name) if out is not None else None,
                              ((stats or {}).get(name) or {}).get("roi"))
        s = (stats or {}).get(name)
        draw_text(panel, f"{name}  {s['fps']:.0f} fps  pose {s['pose_ms']:.0f} ms" if s else name, (8, 20))
        y = 40
        for msg in ((s or {}).get("error"), (s or {}).get("warning"), (alarms or {}).get(name)):
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
    if out is not None and out.people is not None:
        n_seen = max((len(v) for v in out.people.values()), default=0)
        hud.append(f"{n_seen} people seen, tracking {'1' if out.frame.tracking != 'lost' else 'nobody'}")
    if out is not None and out.dropped:
        hud.append(f"ignoring camera {out.dropped} (disagrees)")
    if out is not None and out.swapped:
        hud.append(f"undid a left/right flip ({out.swapped})")
    if recording:
        hud.append(recording)
    hud.append("keys: r record   m markers   q quit")
    bottom = np.hstack([render_topdown(frame, mat, cams), render_front(frame, hud)])
    return np.vstack([np.hstack(panels), bottom])


def run_live(cams: dict[str, CameraConfig], mat: MatConfig, model: str) -> None:
    from vision.live import LiveSource  # imports MediaPipe; keep replay usable without it

    detector, show_markers = MarkerDetector(mat), False
    pipeline = Pipeline(cams, mat=mat)
    monitor = MoveMonitor(cams, detector)
    recorder: Recorder | None = None
    last: PipelineOutput | None = None
    last_draw = 0.0
    with LiveSource(cams, model, mat) as src:
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
                    poses = last.raw if last is not None else {}  # the tracked person per camera
                    for n, image in images.items():
                        monitor.update(n, image, now)
                    alarms = {n: monitor.message(n) for n in cams}
                    rec = f"REC {recorder.count} frames -> {recorder.path.name}" if recorder else None
                    img = compose_view(cams, mat, images, poses, last, src.stats(), src.skew.summary(), rec,
                                       detector if show_markers else None, alarms)
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
