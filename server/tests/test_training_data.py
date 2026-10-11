from __future__ import annotations

import json

import pytest

from server.agents import training_data
from server.agents.training_data import load_manifest


def test_training_manifest_covers_the_three_demo_exercises() -> None:
    records = load_manifest()
    exercises = {item["exercise_id"] for record in records for item in record["exercises"]}
    assert {"squat", "lunge", "straight_leg_raise"} <= exercises
    assert all(record["raw_media_allowed"] is False for record in records)


def test_training_manifest_rejects_raw_media(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{
        "id": "bad", "name": "Bad", "source_url": "https://example.test", "license": "x",
        "approval": "approved", "raw_media_allowed": True, "exercises": []
    }]))
    with pytest.raises(ValueError, match="raw media"):
        load_manifest(path)


async def test_registration_reports_a_failed_tiger_write(monkeypatch) -> None:
    async def fails(_db, _record):
        return False
    monkeypatch.setattr(training_data.writes, "upsert_training_dataset", fails)
    with pytest.raises(RuntimeError, match="Tiger did not register"):
        await training_data.register_manifest(None)
