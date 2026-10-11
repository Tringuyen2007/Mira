"""HapticCoach: turns rep/set_done engine events into haptic cues for the watch (§7).

`check_in` is a HAPTIC_PATTERNS member for documentation only: the Safety Guardian emits it directly
(via `templates.CHECKIN_HAPTIC`) and this agent never duplicates that emit.
"""

from __future__ import annotations

from typing import Any

from server.hub import Hub
from server.state import get_state

HAPTIC_PATTERNS = ("good_rep", "fix_form", "set_done", "check_in")


class HapticCoach:
    """Subscribes to engine events; emits a `haptic` event for `rep` and `set_done`, only while a session
    is live (`get_state().sessions.current` is not None)."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "rep":
            pattern = "good_rep" if ev.get("quality") == "good" else "fix_form"
        elif kind == "set_done":
            pattern = "set_done"
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
