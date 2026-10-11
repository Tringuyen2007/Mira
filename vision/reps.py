"""Exercise-specific rep and hold counters over ready ``FrameResult`` frames.

Create one ``ExerciseCounter`` per patient/exercise/set. Call ``update`` once per
frame in timestamp order, after angles (and optionally physics) are populated.
It writes phase, cumulative accepted rep count, and namespaced rule flags to
the frame; the returned events and results are suitable for a future Engine.

Quality is a bounded 0–100 heuristic, not a clinical assessment. Dynamic reps
start at 100 and lose up to 30 for missing target ROM, 20 for tempo outside the
configured interval, 15 for maximum left/right joint asymmetry above 10°, and
35 for distinct sustained form violations (17.5 each). Holds lose up to 45
for duration short of target, 20 for trunk side lean above 5°, and 35 for
distinct sustained form violations. Scores are rounded to the nearest integer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Mapping

import numpy as np

from vision.physics import PhysicsResult
from vision.rules import RuleEngine, RuleEvent, load_exercise, metric, trusted
from vision.types import FrameResult


@dataclass(frozen=True)
class RepResult:
    number: int
    exercise: str
    start_t: float
    end_t: float
    duration_s: float
    accepted: bool
    quality: int
    rom_deg: float | None
    max_asymmetry_deg: float | None
    stance: str | None
    violations: tuple[str, ...]


@dataclass(frozen=True)
class SetResult:
    exercise: str
    accepted_reps: int
    attempts: int
    mean_quality: float | None
    results: tuple[RepResult, ...]


@dataclass(frozen=True)
class ExerciseUpdate:
    phase: str
    rep: int
    rule_events: tuple[RuleEvent, ...] = ()
    result: RepResult | None = None


class ExerciseCounter:
    """Standalone session API. `finish_set` snapshots and resets the set."""

    def __init__(self, exercise: str, patient_overrides: Mapping | None = None,
                 rules_dir: Path | None = None):
        self.config = load_exercise(exercise, patient_overrides, rules_dir)
        self.exercise = exercise
        self.rules = RuleEngine(self.config["rules"])
        self.reset()

    def reset(self) -> None:
        self.rules.reset()
        self.phase = "idle"
        self.rep = 0
        self.results: list[RepResult] = []
        self._last_t: float | None = None
        self._start: float | None = None
        self._extreme: float | None = None
        self._origin: float | None = None
        self._ready_value: float | None = None
        self._ready_hip_y: float | None = None
        self._origin_hip_y: float | None = None
        self._lowest_hip_y: float | None = None
        self._ready_image_hip_y: float | None = None
        self._ready_image_torso_px: float | None = None
        self._origin_image_hip_y: float | None = None
        self._origin_image_torso_px: float | None = None
        self._image_depth_since: float | None = None
        self._image_depth_seen = False
        self._single_camera = False
        self._max_asym = 0.0
        self._max_sway = 0.0
        self._violations: set[str] = set()
        self._stance: str | None = None
        self._candidate_t: float | None = None
        self._exit_t: float | None = None

    def finish_set(self) -> SetResult:
        accepted = [result.quality for result in self.results if result.accepted]
        summary = SetResult(self.exercise, self.rep, len(self.results),
                            sum(accepted) / len(accepted) if accepted else None, tuple(self.results))
        self.reset()
        return summary

    def _interrupt(self) -> None:
        self.phase = "idle"
        self._last_t = None
        self._start = self._origin = self._extreme = None
        self._ready_value = None
        self._ready_hip_y = self._origin_hip_y = self._lowest_hip_y = None
        self._ready_image_hip_y = self._ready_image_torso_px = None
        self._origin_image_hip_y = self._origin_image_torso_px = None
        self._image_depth_since = None
        self._image_depth_seen = False
        self._stance = None
        self._candidate_t = self._exit_t = None
        self._violations.clear()
        self._max_asym = self._max_sway = 0.0
        self.rules.interrupt()

    def interrupt(self) -> None:
        """Discard an in-progress attempt while keeping completed reps in this set."""
        self._interrupt()

    def _start_attempt(self, t: float, value: float, phase: str, stance: str | None = None) -> None:
        self.phase, self._start, self._origin, self._extreme = phase, t, (
            self._ready_value if self._ready_value is not None else value), value
        self._stance = stance
        self._violations.clear()
        self._max_asym = self._max_sway = 0.0

    def _complete(self, t: float, accepted: bool) -> RepResult:
        assert self._start is not None
        duration = t - self._start
        if self.exercise == "single_leg_stance":
            target = self.config["target_hold_s"]
            duration_penalty = 45 * max(0.0, 1 - duration / target)
            sway_penalty = 20 * min(1.0, max(0.0, self._max_sway - 5) / 15)
            score = 100 - duration_penalty - sway_penalty
            rom = asym = None
        else:
            assert self._origin is not None and self._extreme is not None
            rom = abs(self._extreme - self._origin)
            asym = self._max_asym
            shortfall = max(0.0, 1 - rom / self.config["min_rom_deg"])
            tempo = (duration < self.config["min_rep_s"] or duration > self.config["max_rep_s"])
            score = 100 - 30 * shortfall - (20 if tempo else 0)
            score -= 15 * min(1.0, max(0.0, asym - 10) / 20)
        violations = tuple(sorted(self._violations))
        score -= 35 * min(1.0, len(violations) / 2)
        if not accepted:
            score = min(score, 49)  # rejected attempts must not display a high form score
        if accepted:
            self.rep += 1
        result = RepResult(self.rep, self.exercise, self._start, t, duration, accepted,
                           round(max(0.0, min(100.0, score))), rom, asym, self._stance, violations)
        self.results.append(result)
        self._interrupt()
        return result

    def update(self, frame: FrameResult, physics: PhysicsResult | None = None) -> ExerciseUpdate:
        t = frame.t
        if not trusted(frame) or not math.isfinite(t) or (self._last_t is not None and
                (t <= self._last_t or t - self._last_t > 0.500001)):
            self._interrupt()
            frame.phase, frame.rep = "idle", self.rep
            return ExerciseUpdate(frame.phase, self.rep)
        single_camera = self.exercise == "squat" and frame.tracking.startswith("single_cam_")
        if single_camera != self._single_camera:
            self._interrupt()
            self._single_camera = single_camera
        self._last_t = t
        if self.exercise == "single_leg_stance":
            result = self._update_hold(frame, physics)
        elif self.exercise == "glute_bridge" and not self._floor_posture(frame, physics):
            self._interrupt()
            result = None
        elif single_camera:
            result = self._update_single_cam_squat(frame)
        else:
            result = self._update_dynamic(frame)
        active = self._start is not None
        events = self.rules.update(frame, self.phase, active=active)
        self._violations.update(self.rules.active_ids)
        for rule_id in sorted(self.rules.active_ids):
            flag = f"form:{rule_id}"
            if flag not in frame.flags:
                frame.flags.append(flag)
        frame.phase, frame.rep = self.phase, self.rep
        return ExerciseUpdate(self.phase, self.rep, tuple(events), result)

    @staticmethod
    def _floor_posture(frame: FrameResult, physics: PhysicsResult | None) -> bool:
        if physics is not None and physics.stance == "floor":
            return True
        shoulders = np.asarray(frame.joints)[[11, 12], 1]
        hips = np.asarray(frame.joints)[[23, 24], 1]
        return bool(np.isfinite(shoulders).all() and np.isfinite(hips).all() and
                    shoulders.mean() < 0.6 and hips.mean() < 0.9 and
                    shoulders.mean() < hips.mean() + 0.15)

    def _update_dynamic(self, frame: FrameResult) -> RepResult | None:
        value = metric(frame, self.config["metric"])
        if value is None:
            self._interrupt()  # no movement evidence: do not bridge an occlusion
            return None
        squat = self.exercise == "squat"
        if self.phase == "idle":
            if (squat and value >= self.config["start_above"]) or (
                    not squat and value <= self.config["start_below"]):
                self.phase = "ready"
                self._ready_value = value
        elif self.phase == "ready":
            self._ready_value = max(self._ready_value, value) if squat else min(self._ready_value, value)
            if (squat and value < self.config["enter_below"]) or (
                    not squat and value > self.config["enter_above"]):
                self._start_attempt(frame.t, value, "descending" if squat else "lifting")
        else:
            assert self._extreme is not None
            self._extreme = min(self._extreme, value) if squat else max(self._extreme, value)
            asym = metric(frame, "knee_difference" if squat else "hip_difference")
            if asym is not None:
                self._max_asym = max(self._max_asym, asym)
            if self.phase in ("descending", "lifting"):
                if (squat and value <= self.config["target_below"]) or (
                        not squat and value >= self.config["target_above"]):
                    self.phase = "bottom" if squat else "top"
                elif (squat and value >= self.config["finish_above"]) or (
                        not squat and value <= self.config["finish_below"]):
                    result = self._complete(frame.t, False)
                    self.phase = "ready"
                    self._ready_value = value
                    return result
            elif self.phase in ("bottom", "top"):
                if (squat and value >= self.config["target_below"] + 8) or (
                        not squat and value <= self.config["target_above"] - 8):
                    self.phase = "ascending" if squat else "lowering"
            elif self.phase in ("ascending", "lowering"):
                if (squat and value >= self.config["finish_above"]) or (
                        not squat and value <= self.config["finish_below"]):
                    duration = frame.t - self._start
                    rom = abs(self._extreme - self._origin)
                    accepted = (self.config["min_rep_s"] <= duration <= self.config["max_rep_s"] and
                                rom >= self.config["min_rom_deg"])
                    result = self._complete(frame.t, accepted)
                    self.phase = "ready"
                    self._ready_value = value
                    return result
            if frame.t - self._start > self.config["max_rep_s"]:
                return self._complete(frame.t, False)
        return None

    def _update_single_cam_squat(self, frame: FrameResult) -> RepResult | None:
        """One-camera knee angles have a person/session-specific upright offset.

        Use the current standing knee/hip position as the reference. Both knee flexion and
        vertical hip travel are required for depth; returning to both ends completes a rep.
        """
        knee = metric(frame, "knee_mean")
        hips = np.asarray(frame.joints)[[23, 24], 1]
        if knee is None or not np.isfinite(hips).all():
            self._interrupt()
            return None
        hip_y = float(hips.mean())
        image = frame.image_metrics
        image_ok = all(name in image and math.isfinite(image[name]) for name in
                       ("knee_mean", "hip_y", "torso_px")) and image.get("torso_px", 0) > 1
        c = self.config
        if self.phase == "idle":
            if (image_ok and knee >= c["single_ready_above"] and
                    hip_y >= c["single_ready_hip_min_m"]):
                self._set_single_ready(knee, hip_y, image)
            return None
        if self.phase == "ready":
            assert self._ready_value is not None and self._ready_hip_y is not None
            self._ready_value = max(self._ready_value, knee)
            self._ready_hip_y = max(self._ready_hip_y, hip_y)
            if image_ok:
                self._ready_image_hip_y = min(self._ready_image_hip_y, image["hip_y"])
                self._ready_image_torso_px = max(self._ready_image_torso_px, image["torso_px"])
            if image_ok and knee <= self._ready_value - c["single_enter_drop_deg"]:
                self._start_attempt(frame.t, knee, "descending")
                self._origin_hip_y = self._ready_hip_y
                self._lowest_hip_y = hip_y
                self._origin_image_hip_y = self._ready_image_hip_y
                self._origin_image_torso_px = self._ready_image_torso_px
            return None

        assert self._start is not None and self._origin is not None and self._extreme is not None
        assert self._origin_hip_y is not None and self._lowest_hip_y is not None
        self._extreme = min(self._extreme, knee)
        self._lowest_hip_y = min(self._lowest_hip_y, hip_y)
        asym = metric(frame, "knee_difference")
        if asym is not None:
            self._max_asym = max(self._max_asym, asym)
        if image_ok and self._origin_image_hip_y is not None and self._origin_image_torso_px:
            image_drop = ((image["hip_y"] - self._origin_image_hip_y) /
                          self._origin_image_torso_px)
            image_deep = (self._origin - knee >= c["single_target_drop_deg"] and
                          self._origin_hip_y - hip_y >= c["single_target_hip_drop_m"] and
                          image["knee_mean"] <= c["single_image_target_knee_below"] and
                          image_drop >= c["single_image_target_hip_drop_torso"])
            if image_deep:
                if self._image_depth_since is None:
                    self._image_depth_since = frame.t
                if frame.t - self._image_depth_since >= c["single_image_depth_dwell_s"]:
                    self._image_depth_seen = True
            else:
                self._image_depth_since = None
        else:
            self._image_depth_since = None
        depth = (self._origin - self._extreme >= c["single_target_drop_deg"] and
                 self._origin_hip_y - self._lowest_hip_y >= c["single_target_hip_drop_m"] and
                 self._image_depth_seen)
        returned = (image_ok and knee >= self._origin - c["single_finish_within_deg"] and
                    hip_y >= self._origin_hip_y - c["single_finish_hip_within_m"])
        if self.phase == "descending":
            if depth:
                self.phase = "bottom"
            elif returned:
                result = self._complete(frame.t, False)
                self._set_single_ready(knee, hip_y, image)
                return result
        elif self.phase == "bottom":
            if knee >= self._extreme + 8:
                self.phase = "ascending"
        elif self.phase == "ascending" and returned:
            duration = frame.t - self._start
            accepted = (depth and c["min_rep_s"] <= duration <= c["max_rep_s"])
            result = self._complete(frame.t, accepted)
            self._set_single_ready(knee, hip_y, image)
            return result
        if frame.t - self._start > c["max_rep_s"]:
            return self._complete(frame.t, False)
        return None

    def _set_single_ready(self, knee: float, hip_y: float, image: Mapping[str, float]) -> None:
        self.phase = "ready"
        self._ready_value, self._ready_hip_y = knee, hip_y
        self._ready_image_hip_y = image["hip_y"]
        self._ready_image_torso_px = image["torso_px"]

    def _update_hold(self, frame: FrameResult, physics: PhysicsResult | None) -> RepResult | None:
        stance = physics.stance if physics is not None else "unknown"
        valid = stance in ("left", "right") and physics.load_conf > 0
        if self.phase == "idle":
            if valid:
                if self._candidate_t is None or self._stance != stance:
                    self._candidate_t, self._stance = frame.t, stance
                elif frame.t - self._candidate_t >= self.config["enter_s"]:
                    self._start_attempt(self._candidate_t, 0, "holding", stance)
            else:
                self._candidate_t = self._stance = None
            return None
        if valid and stance == self._stance:
            self._exit_t = None
            sway = metric(frame, "trunk_side_lean_abs")
            if sway is not None:
                self._max_sway = max(self._max_sway, sway)
            return None
        if self._exit_t is None:
            self._exit_t = frame.t
        if frame.t - self._exit_t >= self.config["exit_s"]:
            duration = self._exit_t - self._start
            return self._complete(self._exit_t, duration >= self.config["min_hold_s"])
        return None
