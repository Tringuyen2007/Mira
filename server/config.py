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
    "MIRA_DEVICE_TOKEN",
    "MIRA_VISION_TOKEN",
    "LCD_LINK_KEY",
    "TIGER_DSN",
    "COPILOT_DSN",
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "ELEVEN_API_KEY",
    "RETELL_API_KEY",
)
PHONE_FIELDS = ("RETELL_FROM_NUMBER", "DEMO_EMERGENCY_PHONE", "DEMO_THERAPIST_PHONE", "DEMO_PATIENT_PHONE")
# Read straight from the environment by setup/evals (not a Config field), but still a secret for the logs.
ENV_ONLY_SECRETS = ("COPILOT_RO_PASSWORD",)
# The token shipped in .env.example; the server refuses it (and any token shorter than MIN_TOKEN_LEN).
PLACEHOLDER_TOKEN = "change-me-long-random-token"
MIN_TOKEN_LEN = 24


@dataclass(frozen=True)
class Config:
    MIRA_API_TOKEN: str = ""
    MIRA_DEVICE_TOKEN: str = ""  # optional, least-privilege token for the LCD kiosk (/ws/live + /ws/events only)
    # Optional credential for the remote vision agent (for example Eric's laptop).  When set, it unlocks
    # /ws/vision; it is deliberately separate from the dashboard and patient credentials.
    MIRA_VISION_TOKEN: str = ""
    # Therapist-only WebRTC playback URL template.  Include {session_id}, e.g.
    # https://media.example.com/mira/{session_id}/whep. Empty keeps camera video disabled.
    MIRA_WHEP_URL: str = ""
    LCD_LINK_KEY: str = ""  # optional secret for GET /lcd/<key>, a link that opens the kiosk already paired
    MIRA_SOURCE: str = "mock"
    ENGINE_MODE: str = "thread"
    MOCK_PATIENT_ID: str = "maya"
    MOCK_FALL_RESPONDS_AT: float | None = None
    DEMO_TOOLS_ENABLED: bool = False
    MOCK_LOOP: bool = False  # True: the mock squat program repeats forever (pre-Plan-2 behaviour)
    MOCK_WRIST: bool = False  # mock mode: the demo runs forearm turns and synthesises the watch's wrist rotation
    MOCK_AUTO_RESTART_S: float = 60.0  # mock mode: start the next demo session this long after one ends (0 = off)
    AUTO_STOP_AFTER_LAST_SET: bool = True  # end the session when set == sets_target (engines without `stopped`)
    ANALYST_DELAY_S: float = 2.0  # SessionAnalyst waits this long after a session ends for rep writes to land
    PAIRING_DEMO_CODE: str = "K7P4M2"  # therapist code the seed binds to Maya (the iOS design preview uses it)
    PAIRING_DEMO_CODE_REUSABLE: bool = True  # the demo code may pair many phones; real codes are single-use
    PAIRING_REUSABLE_UPDATES_PROFILE: bool = True  # set to 0 on a public server once the demo phone has paired

    TIGER_DSN: str | None = None
    COPILOT_DSN: str | None = None
    DB_DISABLED: bool = False
    DB_AUTO_MIGRATE: bool = True  # apply server/db/migrations/*.sql (idempotent) at server start

    GEMINI_API_KEY: str | None = None
    GEMINI_API_KEYS: str = ""  # extra keys, comma-separated: used in turn when one hits its daily quota
    GEMINI_MODE: str = "fake"
    GEMINI_MODEL_FAST: str = "gemini-3.5-flash-lite"
    GEMINI_MODEL_SMART: str = "gemini-3.8-flash"
    GEMINI_DISABLED: bool = False
    GEMINI_DAILY_LIMIT: int = 1000
    GEMINI_DAILY_LIMITS: str = ""
    GEMINI_SMART_FALLBACKS: str = "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
    GEMINI_THINKING_LEVEL: str = "low"  # minimal | low | medium | high | off (off = no thinking_config sent)

    ELEVEN_API_KEY: str | None = None
    ELEVEN_VOICE_ID: str | None = None
    ELEVEN_MODEL: str = "eleven_flash_v2_5"
    ELEVEN_DISABLED: bool = False

    RETELL_API_KEY: str | None = None
    RETELL_FROM_NUMBER: str | None = None
    RETELL_AGENT_ID: str | None = None
    RETELL_AGENT_VERSION: int | None = None  # published version bound to the number (printed by retell_setup)
    RETELL_BACKUP_AGENT_ID: str | None = None  # non-Gemini fallback agent (printed by retell_setup)
    RETELL_BACKUP_AGENT_VERSION: int | None = None
    CALLS_DISABLED: bool = True
    SIM_CALL_DURATION_S: float = 45.0  # log-only call simulator (CALLS_DISABLED=1): seconds "on the call"
    ALLOW_TEST_CALLS: bool = False  # POST /me/test-alert may place a real call (only when CALLS_DISABLED is off too)

    CHECKIN_TIMEOUT_S: float = 30.0
    DEMO_EMERGENCY_PHONE: str | None = None
    DEMO_CONTACT_NAME: str = "Eric"  # Maya's emergency contact (seeded into the database, spoken in the call)
    DEMO_THERAPIST_PHONE: str | None = None
    DEMO_PATIENT_PHONE: str | None = None  # Maya's phone in the default profile ({{patient_phone}})

    INSIGHT_ENABLED: bool = True
    INSIGHT_DELAY_S: float = 120.0  # first scheduled Insight run after startup, then every INSIGHT_INTERVAL_S
    INSIGHT_INTERVAL_S: float = 900.0
    DATA_GUARD_ENABLED: bool = True
    DATA_GUARD_DELAY_S: float = 300.0  # 5 min after startup: normally just after a 15-min refresh-policy run
    DATA_GUARD_INTERVAL_S: float = 900.0
    COPILOT_CACHED_DEMO: bool = False
    PUBLIC_WEBHOOK_HOST: str | None = None
    NOTIFY_RESEND_MIN: float = 5.0  # urgent, unacked notifications are re-sent on the hub every N minutes

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
            if name == "GEMINI_API_KEYS":
                values += [k.strip() for k in value.split(",") if k.strip()]
            if name.endswith("_DSN"):
                password = _dsn_password(value)
                if password:
                    values.append(password)
        values += [v for v in (os.environ.get(name) for name in ENV_ONLY_SECRETS) if v]
        return values

    def phone_values(self) -> list[str]:
        """Every configured phone number in the forms it may be logged in: as configured, digits only, and
        with/without the +1 country code. A bare "7135550111" doesn't look like a phone to the log regex."""
        values: set[str] = set()
        for name in PHONE_FIELDS:
            value = getattr(self, name)
            if not value:
                continue
            digits = "".join(ch for ch in value if ch.isdigit())
            values.update({value.strip(), digits, f"+{digits}"})
            if len(digits) == 11 and digits.startswith("1"):
                values.add(digits[1:])
            elif len(digits) == 10:
                values.update({f"1{digits}", f"+1{digits}"})
        return sorted((v for v in values if sum(ch.isdigit() for ch in v) >= 7), key=len, reverse=True)

    def token_problem(self) -> str | None:
        """Why MIRA_API_TOKEN can't be used (None if it can). The server refuses to start on any problem."""
        if not self.MIRA_API_TOKEN:
            return "MIRA_API_TOKEN is not set"
        if self.MIRA_API_TOKEN == PLACEHOLDER_TOKEN:
            return "MIRA_API_TOKEN is still the .env.example placeholder"
        if len(self.MIRA_API_TOKEN) < MIN_TOKEN_LEN:
            return f"MIRA_API_TOKEN is shorter than {MIN_TOKEN_LEN} characters"
        return None

    def device_token_problem(self) -> str | None:
        """Why MIRA_DEVICE_TOKEN can't be used (None if unset or fine). Same rules as the API token, and it
        must not equal it: a kiosk token that is also the API token would hand the whole API to the LCD."""
        token = self.MIRA_DEVICE_TOKEN
        if not token:
            return None
        if token == PLACEHOLDER_TOKEN:
            return "MIRA_DEVICE_TOKEN is still the .env.example placeholder"
        if len(token) < MIN_TOKEN_LEN:
            return f"MIRA_DEVICE_TOKEN is shorter than {MIN_TOKEN_LEN} characters"
        if token == self.MIRA_API_TOKEN:
            return "MIRA_DEVICE_TOKEN must differ from MIRA_API_TOKEN"
        return None

    def lcd_link_problem(self) -> str | None:
        """Why LCD_LINK_KEY can't be used (None if unset or fine): long, and neither of the two tokens, since
        the link hands out the device token to whoever knows it."""
        key = self.LCD_LINK_KEY
        if not key:
            return None
        if key == PLACEHOLDER_TOKEN:
            return "LCD_LINK_KEY is still the .env.example placeholder"
        if len(key) < MIN_TOKEN_LEN:
            return f"LCD_LINK_KEY is shorter than {MIN_TOKEN_LEN} characters"
        if key in (self.MIRA_API_TOKEN, self.MIRA_DEVICE_TOKEN):
            return "LCD_LINK_KEY must differ from MIRA_API_TOKEN and MIRA_DEVICE_TOKEN"
        return None

    def vision_token_problem(self) -> str | None:
        """Why MIRA_VISION_TOKEN can't be used (None if unset or fine)."""
        token = self.MIRA_VISION_TOKEN
        if not token:
            return None
        if token == PLACEHOLDER_TOKEN:
            return "MIRA_VISION_TOKEN is still the .env.example placeholder"
        if len(token) < MIN_TOKEN_LEN:
            return f"MIRA_VISION_TOKEN is shorter than {MIN_TOKEN_LEN} characters"
        if token in (self.MIRA_API_TOKEN, self.MIRA_DEVICE_TOKEN):
            return "MIRA_VISION_TOKEN must differ from the API and device tokens"
        return None


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


# The test suite sets MIRA_SKIP_DOTENV=1 (conftest) so it never reads a developer's real .env or secrets.
if os.environ.get("MIRA_SKIP_DOTENV") != "1":
    load_dotenv(REPO_ROOT / ".env", override=False)
cfg: Config = _load()
