"""Request models shared by /pair and /me (kept apart so the two routers don't import each other)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DEVICE_PREF_STR_MAX = 64


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=7, max_length=20)
    relationship: str | None = Field(default=None, max_length=40)


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    height_cm: float = Field(gt=50, lt=250)
    mass_kg: float = Field(gt=20, lt=400)
    affected_side: Literal["left", "right", "none"] | None = None
    condition: str | None = Field(default=None, max_length=120)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    height_cm: float | None = Field(default=None, gt=50, lt=250)
    mass_kg: float | None = Field(default=None, gt=20, lt=400)
    affected_side: Literal["left", "right", "none"] | None = None
    condition: str | None = Field(default=None, max_length=120)


class MeUpdate(BaseModel):
    profile: ProfilePatch | None = None
    emergency_contact: ContactIn | None = None
    alert_therapist: bool | None = None
    share_by_default: bool | None = None
    device_prefs: dict[str, Any] | None = None

    @field_validator("device_prefs")
    @classmethod
    def _small(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        if len(v) > 20 or any(not isinstance(k, str) or len(k) > 40 for k in v):
            raise ValueError("device_prefs: at most 20 short keys")
        for value in v.values():  # flat scalars only: no nested objects/lists, no long strings
            if isinstance(value, str):
                if len(value) > DEVICE_PREF_STR_MAX:
                    raise ValueError(f"device_prefs: strings are at most {DEVICE_PREF_STR_MAX} characters")
            elif not isinstance(value, (bool, int, float)):
                raise ValueError("device_prefs: values must be true/false, numbers or short strings")
        return v
