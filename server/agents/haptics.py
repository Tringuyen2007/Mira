"""HapticCoach: turns engine events into haptic cues for the watch (§7, Apple Watch spec).

- `good_rep` / `fix_form` from `rep` (quality "good" or not). A rep counted from the watch (`source: "watch"`, by
  RotationCoach) only buzzes when it reached the goal: a short turn is not a form fault.
- `set_done` from `set_done`; `countdown_tick` from every `countdown` (the watch's 3-2-1).
- `check_in` and `breather` are documented here but emitted by the Safety Guardian itself (never duplicated).
"""

from __future__ import annotations

from typing import Any

from server.hub import Hub
from server.state import get_state

HAPTIC_PATTERNS = ("good_rep", "fix_form", "set_done", "check_in", "countdown_tick", "breather")


class HapticCoach:
    """Subscribes to engine events; emits a `haptic` event only while a session is live."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "rep":
            good = ev.get("quality") == "good"
            if ev.get("source") == "watch" and not good:
                return
            pattern = "good_rep" if good else "fix_form"
        elif kind == "set_done":
            pattern = "set_done"
        elif kind == "countdown":
            pattern = "countdown_tick"
        else:
            return
        if not self._session_live():
            return
        self.hub.emit("haptic", {"pattern": pattern, "session_id": ev.get("session_id"), "t": ev.get("t")})

    @staticmethod
    def _session_live() -> bool:
        try:
            state = get_state()
        except RuntimeError:
            return False
        return state.sessions.current is not None
