"""Database layer behaviour that needs no database: mapping, drop-oldest, kill switch, fallbacks."""

from __future__ import annotations

import json
import time
from pathlib import Path

from server.db import writes
from server.db.persist import profile_from_row
from server.db.pool import Database
from server.db.writer import COLUMNS, FrameWriter, frame_row

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
DEAD_DSN = "postgresql://postgres:x@127.0.0.1:1/none?connect_timeout=1"


def _frame() -> dict:
    return json.loads((SAMPLES / "engine_frame.json").read_text())


def test_frame_row_maps_contract_one_to_one() -> None:
    frame = _frame()
    row = dict(zip(COLUMNS, frame_row(frame, "maya"), strict=True))
    assert row["time"].timestamp() == frame["t"]
    assert row["patient_id"] == "maya" and row["session_id"] == frame["session_id"]
    assert row["knee_l"] == frame["angles"]["knee_l"] and row["knee_ratio"] == frame["angles"]["knee_ratio"]
    assert (row["com_x"], row["com_y"], row["com_z"]) == tuple(frame["com"])
    assert row["confidence"] == frame["confidence_avg"]
    assert len(row["landmarks"]) == 99 and row["landmarks"][:3] == frame["joints"][0]


def test_frame_without_joints_has_null_landmarks() -> None:
    frame = _frame()
    frame.pop("joints")
    assert dict(zip(COLUMNS, frame_row(frame, "maya"), strict=True))["landmarks"] is None


def test_submit_never_blocks_and_drops_oldest() -> None:
    writer = FrameWriter(DEAD_DSN, max_queue=10)  # not started: nothing drains the buffer
    frames = []
    for i in range(25):
        f = _frame()
        f["t"] = 1000.0 + i
        frames.append(f)
    started = time.perf_counter()
    for f in frames:
        writer.submit({**f, "patient_id": "maya"})
    assert time.perf_counter() - started < 0.05
    stats = writer.stats()
    assert stats["queue_depth"] == 10 and stats["dropped"] == 15
    assert [f["t"] for f, _ in writer._queue] == [1015.0 + i for i in range(10)]


def test_frames_without_session_or_patient_are_skipped() -> None:
    writer = FrameWriter(DEAD_DSN, patient_provider=lambda: None)
    writer.submit(_frame())
    frame = _frame()
    frame["session_id"] = None
    writer.submit({**frame, "patient_id": "maya"})
    assert writer.stats()["skipped"] == 2 and writer.stats()["queue_depth"] == 0


def test_writer_keeps_frames_while_database_is_down() -> None:
    writer = FrameWriter(DEAD_DSN, patient_provider=lambda: "maya", flush_interval_s=0.05)
    writer.start()
    try:
        for _ in range(5):
            writer.submit(_frame())
        time.sleep(0.4)
        stats = writer.stats()
        assert stats["errors"] >= 1 and stats["connected"] is False
        assert stats["queue_depth"] == 5 and stats["written_total"] == 0
    finally:
        writer.stop(timeout_s=3)


async def test_writes_without_db_are_noops() -> None:
    assert await writes.insert_session(None, "s", "maya", "squat", time.time()) is False
    assert await writes.insert_memory_event(None, "maya", "pain", "m", {}) is None
    assert await writes.update_session_ai(None, "s", ai_summary="x", ai_note=None) is None


async def test_writes_never_raise_when_database_is_unreachable() -> None:
    db = Database(DEAD_DSN)
    await db.open(wait_s=0.5)
    try:
        assert db.available is False
        started = time.perf_counter()
        ok = await writes.insert_alert_step(db, time.time(), "00000000-0000-0000-0000-000000000001", "maya",
                                            None, "fall", "detected", "device", {"x": 1})
        assert ok is False
        assert await writes.update_session_ai(db, "00000000-0000-0000-0000-000000000002",
                                              ai_summary="s", ai_note="n") is None
        assert time.perf_counter() - started < 10
    finally:
        await db.close()


def test_profile_from_row_uses_db_values_and_persona_pronouns() -> None:
    row = {"id": "maya", "name": "Maya Ellis", "age": 67, "condition": "right knee replacement",
           "affected_side": "right", "hr_limit": 125, "emergency_contact_name": "Linh",
           "emergency_contact_phone": "+15550100111", "therapist_id": "okafor", "therapist_name": "Dr. Okafor",
           "therapist_phone": "+15550100222", "pain_today": 3, "falls_30d": 1}
    p = profile_from_row(row)
    assert (p.first_name, p.pronoun_subj, p.hr_limit, p.age, p.pain_today, p.falls_30d) == ("Maya", "she", 125, 67,
                                                                                            3, 1)
    other = profile_from_row({**row, "id": "p07", "name": "Rosa Delgado"})
    assert other.first_name == "Rosa" and other.pronoun_subj == "they" and other.weeks_since_surgery is None
