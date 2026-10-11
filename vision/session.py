"""Frame-clocked exercise session flow. No wall-clock timers or network calls."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Session:
    reps_target: int
    sets_target: int
    gesture_timeout_s: float = 10.0
    rest_s: float = 10.0

    def __post_init__(self) -> None:
        if self.reps_target < 1 or self.sets_target < 1:
            raise ValueError("reps_target and sets must be positive")
        self.state = "idle"
        self.set_index = 1
        self._since: float | None = None
        self._prompted = False
        self._countdown_sent = 0

    def arrived(self, t: float) -> list[dict]:
        if self.state != "idle":
            return []
        self.state, self._since, self._prompted = "awaiting_gesture", t, False
        return [{"type": "arrived", "t": t}, {"type": "awaiting_gesture", "gesture": "raised_hand", "t": t}]

    def gesture(self, t: float) -> list[dict]:
        if self.state != "awaiting_gesture":
            return []
        self.state, self._since, self._countdown_sent = "countdown", t, 1
        return [{"type": "gesture", "name": "raised_hand", "t": t}, {"type": "countdown", "n": 3, "t": t}]

    def tick(self, t: float) -> list[dict]:
        if self._since is None:
            return []
        elapsed = t - self._since
        if self.state == "awaiting_gesture" and elapsed >= self.gesture_timeout_s:
            if not self._prompted:
                self._prompted, self._since = True, t
                return [{"type": "awaiting_gesture", "gesture": "raised_hand", "t": t}]
            self.state, self._since = "idle", None
            return []
        if self.state == "countdown":
            events = []
            while self._countdown_sent < min(3, int(elapsed) + 1):
                events.append({"type": "countdown", "n": 3 - self._countdown_sent, "t": t})
                self._countdown_sent += 1
            if elapsed >= 3:
                self.state, self._since = "running", t
            return events
        if self.state == "rest" and elapsed >= self.rest_s:
            self.state, self._since, self._countdown_sent = "countdown", t, 1
            return [{"type": "countdown", "n": 3, "t": t}]
        return []

    def pause(self, t: float, reason: str) -> list[dict]:
        if self.state != "running":
            return []
        self.state, self._since = "paused", t
        return [{"type": "paused", "reason": reason, "t": t}]

    def resume(self, t: float) -> list[dict]:
        if self.state != "paused":
            return []
        self.state, self._since = "running", t
        return [{"type": "resumed", "t": t}]

    def set_done(self, t: float, reps: int, avg_quality: float | None) -> list[dict]:
        if self.state != "running":
            return []
        event = {"type": "set_done", "set": self.set_index, "reps": reps,
                 "avg_quality": avg_quality, "t": t}
        if self.set_index >= self.sets_target:
            self.state, self._since = "done", t
        else:
            self.set_index += 1
            self.state, self._since = "rest", t
        return [event]

    def stop(self) -> None:
        self.state, self._since = "done", None
