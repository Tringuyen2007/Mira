"""Timed form rules and versionable exercise thresholds.

Rules are descriptive JSON in ``data/rules``. Patient overrides may replace
top-level numeric thresholds or individual rule thresholds/timings; unknown
keys and invalid numbers fail at session setup instead of silently changing
clinical behavior. Rule events require a continuous condition for duration_s.
An event's cooldown limits repeats, while flags remain active until recovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping

from vision.types import FrameResult

RULE_DIR = Path(__file__).resolve().parent.parent / "data" / "rules"


def trusted(frame: FrameResult) -> bool:
    return (frame.body == "ready" and frame.tracking != "lost" and
            not any(flag in frame.flags for flag in ("pose_held", "camera_moved")))


def metric(frame: FrameResult, name: str) -> float | None:
    angles = frame.angles
    if name.endswith("_abs"):
        value = metric(frame, name[:-4])
        return None if value is None else abs(value)
    if name.endswith("_mean"):
        side = name[:-5]
        pair = (angles.get(f"left_{side}"), angles.get(f"right_{side}"))
        return sum(pair) / 2 if all(_finite(v) for v in pair) else None
    if name.endswith("_difference"):
        side = name[:-11]
        pair = (angles.get(f"left_{side}"), angles.get(f"right_{side}"))
        return abs(pair[0] - pair[1]) if all(_finite(v) for v in pair) else None
    value = angles.get(name)
    return float(value) if _finite(value) else None


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_exercise(exercise: str, overrides: Mapping | None = None, rules_dir: Path | None = None) -> dict:
    """Load a bundled definition and apply checked patient-specific values."""
    if exercise not in {"squat", "glute_bridge", "single_leg_stance"}:
        raise ValueError(f"unknown exercise: {exercise}")
    config = json.loads(((Path(rules_dir) if rules_dir is not None else RULE_DIR) / f"{exercise}.json").read_text())
    overrides = overrides or {}
    for key, value in overrides.items():
        if key == "rules":
            if not isinstance(value, Mapping):
                raise ValueError("rules override must map rule ids to fields")
            by_id = {rule["id"]: rule for rule in config["rules"]}
            for rule_id, fields in value.items():
                if rule_id not in by_id or not isinstance(fields, Mapping):
                    raise ValueError(f"unknown rule override: {rule_id}")
                for field, replacement in fields.items():
                    if field not in {"threshold", "duration_s", "cooldown_s"} or not _finite(replacement):
                        raise ValueError(f"invalid rule override: {rule_id}.{field}")
                    if field.endswith("_s") and replacement < 0:
                        raise ValueError(f"negative rule timing: {rule_id}.{field}")
                    by_id[rule_id][field] = float(replacement)
        elif key in config and _finite(config[key]) and _finite(value):
            if value < 0:
                raise ValueError(f"negative threshold: {key}")
            config[key] = float(value)
        else:
            raise ValueError(f"invalid exercise override: {key}")
    if exercise == "squat" and not (config["start_above"] > config["enter_below"] >
                                     config["target_below"] and
                                     config["finish_above"] > config["target_below"]):
        raise ValueError("squat thresholds must preserve ready/entry/depth hysteresis")
    if exercise == "squat" and not (
            config["single_ready_above"] > 0 and config["single_ready_hip_min_m"] > 0 and
            config["single_target_drop_deg"] > config["single_enter_drop_deg"] >
            config["single_finish_within_deg"] >= 0 and
            config["single_target_hip_drop_m"] > config["single_finish_hip_within_m"] >= 0 and
            0 < config["single_image_target_knee_below"] < 180 and
            0 < config["single_image_target_hip_drop_torso"] < 3 and
            0 <= config["single_image_depth_dwell_s"] <= 1):
        raise ValueError("single-camera squat thresholds must preserve depth and return hysteresis")
    if exercise == "glute_bridge" and not (config["start_below"] < config["enter_above"] <
                                            config["target_above"] and
                                            config["finish_below"] < config["target_above"]):
        raise ValueError("bridge thresholds must preserve ready/entry/top hysteresis")
    positive = ("min_rom_deg", "max_rep_s") if exercise != "single_leg_stance" else ("target_hold_s",)
    if any(config[name] <= 0 for name in positive):
        raise ValueError("ROM, maximum rep duration and target hold must be positive")
    if exercise != "single_leg_stance" and config["min_rep_s"] > config["max_rep_s"]:
        raise ValueError("minimum rep duration exceeds maximum")
    return config


@dataclass(frozen=True)
class RuleEvent:
    t: float
    rule_id: str
    value: float
    threshold: float


class RuleEngine:
    """One per exercise/person; timestamp order required. ``reset`` on a new set."""

    def __init__(self, rules: list[dict]):
        self.rules = rules
        self.reset()

    def reset(self) -> None:
        self._since: dict[str, float] = {}
        self._last_t: float | None = None
        self._next_event: dict[str, float] = {}
        self.active_ids: set[str] = set()

    def interrupt(self) -> None:
        self._since.clear()
        self.active_ids.clear()
        self._last_t = None

    def update(self, frame: FrameResult, phase: str, active: bool = True) -> list[RuleEvent]:
        t = frame.t
        if not trusted(frame) or not active or not _finite(t) or (self._last_t is not None and
                (t <= self._last_t or t - self._last_t > 0.500001)):
            self.interrupt()
            return []
        self._last_t = t
        events = []
        self.active_ids.clear()
        for rule in self.rules:
            rule_id = rule["id"]
            value = metric(frame, rule["metric"])
            eligible = rule.get("phase") in (None, phase)
            crossing = value is not None and eligible and (
                value < rule["threshold"] if rule["op"] == "lt" else value > rule["threshold"])
            if not crossing:
                self._since.pop(rule_id, None)
                continue
            since = self._since.setdefault(rule_id, t)
            if t - since < rule["duration_s"]:
                continue
            self.active_ids.add(rule_id)
            if t >= self._next_event.get(rule_id, -math.inf):
                events.append(RuleEvent(t, rule_id, value, rule["threshold"]))
                self._next_event[rule_id] = t + rule["cooldown_s"]
        return events
