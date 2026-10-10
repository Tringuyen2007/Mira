"""Environment configuration, feature flags and kill switches.

`cfg` is a module singleton. `reload_config()` re-reads the environment and updates `cfg`
in place, so modules that did `from server.config import cfg` see the new values.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

# Values that must never appear in logs. DSNs are listed whole; their passwords are added separately.
SECRET_FIELDS = (
    "MIRA_API_TOKEN",
    "TIGER_DSN",
    "COPILOT_DSN",
    "GEMINI_API_KEY",
    "ELEVEN_API_KEY",
    "RETELL_API_KEY",
)
PHONE_FIELDS = ("RETELL_FROM_NUMBER", "DEMO_EMERGENCY_PHONE", "DEMO_THERAPIST_PHONE")


@dataclass(frozen=True)
class Config:
    MIRA_API_TOKEN: str = ""
    MIRA_SOURCE: str = "mock"
    ENGINE_MODE: str = "thread"
    MOCK_PATIENT_ID: str = "maya"
    MOCK_FALL_RESPONDS_AT: float | None = None
    DEMO_TOOLS_ENABLED: bool = False

    TIGER_DSN: str | None = None
    COPILOT_DSN: str | None = None
    DB_DISABLED: bool = False

    GEMINI_API_KEY: str | None = None
    GEMINI_MODE: str = "fake"
    GEMINI_MODEL_FAST: str = "gemini-3.5-flash-lite"
    GEMINI_MODEL_SMART: str = "gemini-3.8-flash"
    GEMINI_DISABLED: bool = False
    GEMINI_DAILY_LIMIT: int = 1000

    ELEVEN_API_KEY: str | None = None
    ELEVEN_VOICE_ID: str | None = None
    ELEVEN_MODEL: str = "eleven_flash_v2_5"
    ELEVEN_DISABLED: bool = False

    RETELL_API_KEY: str | None = None
    RETELL_FROM_NUMBER: str | None = None
    RETELL_AGENT_ID: str | None = None
    CALLS_DISABLED: bool = True
    SIM_CALL_DURATION_S: float = 45.0  # log-only call simulator (CALLS_DISABLED=1): seconds "on the call"

    CHECKIN_TIMEOUT_S: float = 30.0
    DEMO_EMERGENCY_PHONE: str | None = None
    DEMO_THERAPIST_PHONE: str | None = None

    INSIGHT_ENABLED: bool = True
    COPILOT_CACHED_DEMO: bool = False
    PUBLIC_WEBHOOK_HOST: str | None = None

    LOG_DIR: str = "logs"
    DATA_DIR: str = "data"
    TZ_NAME: str = "America/Chicago"

    def redacted(self) -> dict[str, Any]:
        """Config safe for logging: secrets become "set"/"unset", phone numbers are masked."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in SECRET_FIELDS:
                out[f.name] = "set" if value else "unset"
            elif f.name in PHONE_FIELDS:
                out[f.name] = _mask_tail(value) if value else "unset"
            else:
                out[f.name] = value
        return out

    def secret_values(self) -> list[str]:
        """Every configured secret string (including DSN passwords), for the log redaction filter."""
        values: list[str] = []
        for name in SECRET_FIELDS:
            value = getattr(self, name)
            if not value:
                continue
            values.append(value)
            if name.endswith("_DSN"):
                password = _dsn_password(value)
                if password:
                    values.append(password)
        return values


def _mask_tail(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"***{digits[-4:]}"


def _dsn_password(dsn: str) -> str | None:
    try:
        return urlsplit(dsn).password
    except ValueError:
        return None


def _parse(field: dataclasses.Field[Any], raw: str) -> Any:
    kind = str(field.type)
    value = raw.strip()
    if kind == "bool":
        lowered = value.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"{field.name}: expected a boolean, got {raw!r}")
    if value == "" and "None" in kind:
        return None
    if kind.startswith("float"):
        return float(value)
    if kind.startswith("int"):
        return int(value)
    return value


def _load() -> Config:
    kwargs: dict[str, Any] = {}
    for f in fields(Config):
        raw = os.environ.get(f.name)
        if raw is None:
            continue
        try:
            kwargs[f.name] = _parse(f, raw)
        except ValueError as exc:
            raise ValueError(f"invalid environment variable {f.name}={raw!r}: {exc}") from exc
    return Config(**kwargs)


def reload_config() -> Config:
    """Re-read os.environ (the .env file was loaded once at import) and update `cfg` in place."""
    fresh = _load()
    for f in fields(Config):
        object.__setattr__(cfg, f.name, getattr(fresh, f.name))
    return cfg


load_dotenv(REPO_ROOT / ".env", override=False)
cfg: Config = _load()
