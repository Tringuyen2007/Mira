"""Seed the demo cast (idempotent): Dr. Okafor, Maya (mock persona, D21) and her prescriptions.

usage: python -m server.db.seed "$DSN"
Phone numbers come from DEMO_THERAPIST_PHONE / DEMO_EMERGENCY_PHONE and are refreshed on every run.
"""

from __future__ import annotations

import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from server.config import cfg

THERAPIST_ID = "okafor"
MAYA_ID = "maya"

DEFAULT_LIMITS = {"min_reps": 8, "max_reps": 15, "max_rest_s": 120, "min_rest_s": 30}
# (exercise_id, sets, reps, targets); single_leg_stance "reps" are holds.
PRESCRIPTIONS: list[tuple[str, int, int, dict[str, Any]]] = [
    ("squat", 3, 10, {"knee_deg": 90, "sessions_per_week": 5}),
    ("glute_bridge", 2, 10, {"hold_s": 2, "sessions_per_week": 1}),
    ("single_leg_stance", 3, 3, {"hold_s": 20}),
]


def upsert_therapist(conn: psycopg.Connection[Any]) -> None:
    conn.execute("INSERT INTO therapists (id, name, phone) VALUES (%s, %s, %s) "
                 "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone",
                 (THERAPIST_ID, "Dr. Okafor", cfg.DEMO_THERAPIST_PHONE))


def upsert_patient(conn: psycopg.Connection[Any], *, patient_id: str, name: str, age: int, height_cm: float,
                   mass_kg: float, affected_side: str, condition: str, contact_name: str | None,
                   contact_phone: str | None, hr_limit: int = 130, simulated: bool = True,
                   created_days_ago: int = 0) -> None:
    conn.execute(
        "INSERT INTO patients (id, name, age, height_cm, mass_kg, affected_side, condition, therapist_id, "
        "emergency_contact_name, emergency_contact_phone, hr_limit, simulated, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now() - make_interval(days => %s)) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, age = EXCLUDED.age, height_cm = EXCLUDED.height_cm, "
        "mass_kg = EXCLUDED.mass_kg, affected_side = EXCLUDED.affected_side, condition = EXCLUDED.condition, "
        "therapist_id = EXCLUDED.therapist_id, emergency_contact_name = EXCLUDED.emergency_contact_name, "
        "emergency_contact_phone = EXCLUDED.emergency_contact_phone, hr_limit = EXCLUDED.hr_limit, "
        "simulated = EXCLUDED.simulated",
        (patient_id, name, age, height_cm, mass_kg, affected_side, condition, THERAPIST_ID, contact_name,
         contact_phone, hr_limit, simulated, created_days_ago))


def ensure_prescriptions(conn: psycopg.Connection[Any], patient_id: str) -> None:
    """Version 1 of each exercise, only if the patient has no version yet (later versions are never touched)."""
    for exercise_id, sets, reps, targets in PRESCRIPTIONS:
        conn.execute(
            "INSERT INTO prescriptions (patient_id, exercise_id, version, sets, reps, targets, limits, created_by) "
            "SELECT %s, %s, 1, %s, %s, %s, %s, %s WHERE NOT EXISTS "
            "(SELECT 1 FROM prescriptions WHERE patient_id = %s AND exercise_id = %s)",
            (patient_id, exercise_id, sets, reps, Jsonb(targets), Jsonb(DEFAULT_LIMITS), THERAPIST_ID,
             patient_id, exercise_id))


def seed(conn: psycopg.Connection[Any], history_days: int = 75) -> None:
    upsert_therapist(conn)
    upsert_patient(conn, patient_id=MAYA_ID, name="Maya Ellis", age=67, height_cm=163, mass_kg=70,
                   affected_side="right", condition="right knee replacement",
                   contact_name="Linh", contact_phone=cfg.DEMO_EMERGENCY_PHONE, hr_limit=130, simulated=True,
                   created_days_ago=history_days + 5)
    ensure_prescriptions(conn, MAYA_ID)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m server.db.seed <dsn>", file=sys.stderr)
        return 2
    with psycopg.connect(argv[1], autocommit=True) as conn:
        seed(conn)
    print("seeded therapist okafor, patient maya, prescriptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
