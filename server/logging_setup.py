"""JSON-line logging with correlation ids, phone masking and secret redaction (§3.1).

Records are scrubbed and formatted on the calling thread, then written to the console and the
rotating file by a QueueListener thread, so file I/O never runs on the event loop.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import re
import sys
from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import cfg

# E.164 ("+17135550123") or separated national formats ("(713) 555-0123", "713-555-0123").
_PHONE_RE = re.compile(
    r"\+[1-9]\d{7,14}\b"
    r"|(?:\+?\d{1,3}[\s.-])?(?:\(\d{3}\)|\b\d{3})[\s.-]?\d{3}[\s.-]\d{4}\b"
)
_TOKEN_PARAM_RE = re.compile(r"(token=)[^&\s\"']+")
_MIN_SECRET_LEN = 6
_REDACTED = "***REDACTED***"

# Attributes every LogRecord has; anything else came from `extra=`.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}

_listener: logging.handlers.QueueListener | None = None


def mask_phone(s: str) -> str:
    """Mask every phone-number-looking substring to its last 4 digits, e.g. ``***0123``. The configured phone
    numbers (config PHONE_FIELDS) are masked in any form, including bare digits the pattern can't tell apart
    from other numbers ("7135550111")."""
    for phone in cfg.phone_values():
        if phone in s:
            digits = re.sub(r"\D", "", phone)
            s = s.replace(phone, f"***{digits[-4:]}")

    def _mask(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return f"***{digits[-4:]}"

    return _PHONE_RE.sub(_mask, s)


def scrub(value: Any, secrets: list[str]) -> Any:
    """Mask phones and redact secrets in strings, recursing into dicts/lists/tuples."""
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, _REDACTED)
        value = _TOKEN_PARAM_RE.sub(r"\1" + _REDACTED, value)
        return mask_phone(value)
    if isinstance(value, Mapping):
        return {k: scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    return value


class RedactFilter(logging.Filter):
    """Scrubs the message and every `extra` field of each record in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = sorted((s for s in cfg.secret_values() if len(s) >= _MIN_SECRET_LEN), key=len, reverse=True)
        record.msg = scrub(record.getMessage(), secrets)
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, scrub(value, secrets))
        if record.exc_info:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info), secrets)
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": getattr(record, "component", None) or record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key != "component" and value is not None:
                entry[key] = value
        if record.exc_text:
            entry["exc"] = record.exc_text
        return json.dumps(entry, default=str, ensure_ascii=False)


class _PreformattedQueueHandler(logging.handlers.QueueHandler):
    """Puts the final JSON line on the queue so the listener only does I/O."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        line = self.format(record)
        out = logging.makeLogRecord({"msg": line, "levelno": record.levelno, "levelname": record.levelname})
        return out


class _ComponentAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """LoggerAdapter that merges call-site `extra` with the component instead of replacing it."""

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        kwargs["extra"] = {**(self.extra or {}), **(kwargs.get("extra") or {})}
        return msg, kwargs


def get_logger(component: str) -> logging.LoggerAdapter:  # type: ignore[type-arg]
    return _ComponentAdapter(logging.getLogger(f"mira.{component}"), {"component": component})


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging (idempotent): JSON lines to stderr and LOG_DIR/server.log (10 MB x 5)."""
    global _listener
    shutdown_logging()

    log_dir = Path(cfg.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    plain = logging.Formatter("%(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(plain)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "server.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(plain)

    q: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = _PreformattedQueueHandler(q)
    queue_handler.addFilter(RedactFilter())
    queue_handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(queue_handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; route its records (access lines carry ?token=) through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    _listener = logging.handlers.QueueListener(q, console, file_handler, respect_handler_level=False)
    _listener.start()


def shutdown_logging() -> None:
    """Flush and stop the writer thread (safe to call when logging was never set up)."""
    global _listener
    if _listener is not None:
        _listener.stop()
        for handler in _listener.handlers:
            handler.close()
        _listener = None
