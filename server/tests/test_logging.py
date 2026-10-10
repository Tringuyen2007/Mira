from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from server.config import cfg, reload_config
from server.logging_setup import get_logger, mask_phone, setup_logging, shutdown_logging
from server.tests.conftest import TOKEN

GEMINI_KEY = "AIzaSy-test-gemini-secret-value"
DB_PASSWORD = "s3cr3t-db-password"


@pytest.mark.parametrize("raw, expected", [
    ("+17135550123", "***0123"),
    ("call (713) 555-0123 now", "call ***0123 now"),
    ("713-555-0123", "***0123"),
    ("+1 713 555 0123", "***0123"),
])
def test_mask_phone(raw: str, expected: str) -> None:
    assert mask_phone(raw) == expected


@pytest.mark.parametrize("text", ["t=1760112345.117", "seq 48213", "2026-10-10T19:54:03", "rep 7 of 10"])
def test_mask_phone_leaves_other_numbers(text: str) -> None:
    assert mask_phone(text) == text


def _lines(monkeypatch: pytest.MonkeyPatch, emit) -> list[dict]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("TIGER_DSN", f"postgresql://tsdbadmin:{DB_PASSWORD}@host.example:5432/tsdb")
    reload_config()
    setup_logging()
    emit()
    shutdown_logging()
    text = (Path(cfg.LOG_DIR) / "server.log").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_log_lines_are_json_with_component_and_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    log = get_logger("guardian")

    lines = _lines(monkeypatch, lambda: log.info("alert step", extra={"session_id": "s1", "alert_id": "a1",
                                                                     "step": "check_in"}))
    entry = lines[-1]
    assert entry["component"] == "guardian" and entry["level"] == "INFO" and entry["msg"] == "alert step"
    assert entry["session_id"] == "s1" and entry["alert_id"] == "a1" and entry["step"] == "check_in"
    assert "time" in entry


def test_phone_masked_and_secrets_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    log = get_logger("retell")

    def emit() -> None:
        log.info("calling +17135550123 with key %s", GEMINI_KEY,
                 extra={"to": "+15550100111", "call": {"from": "(713) 555-0199", "auth": f"Bearer {TOKEN}"},
                        "dsn": cfg.TIGER_DSN, "numbers": ["+15550100222"]})
        logging.getLogger("uvicorn.access").info('GET /ws/pose?token=%s HTTP/1.1', TOKEN)
        try:
            raise ValueError(f"db failed: password={DB_PASSWORD}")
        except ValueError:
            log.exception("boom")

    lines = _lines(monkeypatch, emit)
    blob = "\n".join(json.dumps(line) for line in lines)
    for secret in (GEMINI_KEY, TOKEN, DB_PASSWORD, "7135550123", "5550100111", "7135550199", "5550100222"):
        assert secret not in blob, f"{secret} leaked into the log"
    entry = next(line for line in lines if line["component"] == "retell")
    assert "***0123" in entry["msg"]
    assert entry["to"] == "***0111" and entry["call"]["from"] == "***0199" and entry["numbers"] == ["***0222"]
    assert any("token=" in line["msg"] for line in lines if line["component"] == "uvicorn.access")
    assert any("exc" in line for line in lines)
