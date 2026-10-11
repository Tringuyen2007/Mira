"""Apple Watch settings, stored as flat keys in patients.device_prefs (written through `PUT /me`), with the
contract's defaults. Shared by GET /me/watch, RotationCoach (which arm) and ClockSync (which wrist)."""

from __future__ import annotations

from typing import Any

WATCH_PREF_DEFAULTS: dict[str, Any] = {"watch_taps": "normal", "watch_arm": "right", "watch_double_tap": True,
                                       "watch_captions": True}
_CHOICES: dict[str, tuple[str, ...]] = {"watch_taps": ("normal", "strong"), "watch_arm": ("left", "right")}


def watch_prefs(device_prefs: dict[str, Any] | None) -> dict[str, Any]:
    """The four watch_* prefs; a missing or invalid value falls back to its default."""
    prefs = device_prefs if isinstance(device_prefs, dict) else {}
    out = dict(WATCH_PREF_DEFAULTS)
    for key, default in WATCH_PREF_DEFAULTS.items():
        value = prefs.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(value, str) and value in _CHOICES[key]:
            out[key] = value
    return out


def watch_arm(device_prefs: dict[str, Any] | None) -> str:
    """"left" or "right": the arm the watch is worn on."""
    return str(watch_prefs(device_prefs)["watch_arm"])
