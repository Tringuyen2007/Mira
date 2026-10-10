"""Simulated history (TRI.md §5.14): 15 mock patients × ~75 days, 10 Hz frames, loaded with COPY.

usage: python -m server.sim.simulate_history --dsn "$DSN" [--seed 42] [--days 75] [--patients 15]

Story (fixed seed → identical data relative to today):
  * Maya (right knee replacement): over the last 3 weeks p95 knee depth ~60° → ~85° while the load on her
    affected (right) leg falls 48% → 36% (load_l_pct 52 → 64), trunk lean grows, pain spikes after
    back-to-back days, 2 missed days in the last 2 weeks.
  * Two "story" patients with milder compensation (Copilot ranks three people). Landmarks are stored for the
    three story patients' squat sessions only; everyone else has landmarks NULL (§5.12).
  * Twelve ordinary patients; a seeded few miss 3+ days in the last 2 weeks; one patient has only 1 session.
Knee angles are flexion in degrees (0 = straight), as the rollup assumes (max = deepest bend).
The default run writes the eval answers to server/sim/expected.json (Copilot evals, F4).
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from server.db import seed as seed_mod
from server.engine_bridge import squat_frame_body

TZ = ZoneInfo("America/Chicago")
HZ = 10
SQUAT_S, BRIDGE_S = 120, 60
SETS, REPS, REP_S, SET_S = 3, 10, 3.5, 40.0  # 3 × (10 reps × 3.5 s + 5 s rest) = 120 s
STORY_DAYS = 21
RECENT_DAYS = 14
DEFAULT_EXPECTED = Path(__file__).with_name("expected.json")
FRAME_COLUMNS = ("time, patient_id, session_id, exercise_id, set_no, knee_l, knee_r, hip_l, hip_r, trunk_lean, "
                 "trunk_side, pelvis_rot, knee_ratio, com_x, com_y, com_z, load_l_pct, heel_l_pct, heel_r_pct, "
                 "confidence, landmarks")


@dataclass
class Spec:
    patient_id: str
    name: str
    age: int
    affected_side: str
    condition: str
    story: str = "ordinary"          # maya | mild | ordinary
    depth_start: float = 55.0        # p95-ish flexion at the start of the history
    depth_story_from: float = 0.0    # story patients: depth at the start of the last 3 weeks
    depth_end: float = 75.0
    load_affected_from: float = 50.0  # % body weight on the affected leg (story window start)
    load_affected_to: float = 50.0
    lean_from: float = 8.0
    lean_to: float = 8.0
    weekdays: tuple[int, ...] = (0, 1, 3, 4, 5)
    session_hour: float = 9.0
    landmarks: bool = False
    enrolled_days_ago: int | None = None  # None = whole history


@dataclass
class DayPlan:
    day: date
    offset: int                      # days before today (1 = yesterday)
    sessions: list[str] = field(default_factory=list)


PATIENTS: list[Spec] = [
    Spec("maya", "Maya Ellis", 67, "right", "right knee replacement", "maya", depth_start=42, depth_story_from=57,
         depth_end=85, load_affected_from=49.2, load_affected_to=36, lean_from=9, lean_to=17, landmarks=True,
         weekdays=(0, 1, 3, 4, 5), session_hour=9.5),
    Spec("p02", "Daniel Reyes", 58, "left", "left ACL reconstruction", "mild", depth_start=55, depth_story_from=66,
         depth_end=80, load_affected_from=49, load_affected_to=42, lean_from=8, lean_to=11, landmarks=True,
         weekdays=(0, 2, 3, 4, 6), session_hour=18.0),
    Spec("p03", "Grace Kim", 71, "right", "right hip replacement", "mild", depth_start=50, depth_story_from=62,
         depth_end=72, load_affected_from=49, load_affected_to=45, lean_from=10, lean_to=12, landmarks=True,
         weekdays=(0, 1, 2, 4, 5), session_hour=10.0),
    Spec("p04", "Samuel Ortiz", 63, "left", "left knee replacement", session_hour=21.0),  # 9 PM local sessions
    Spec("p05", "Helen Brooks", 74, "none", "balance and deconditioning"),
    Spec("p06", "Arjun Mehta", 52, "right", "right meniscus repair"),
    Spec("p07", "Rosa Delgado", 69, "left", "left hip replacement"),
    Spec("p08", "Thomas Webb", 60, "right", "right patellar tendinopathy"),
    Spec("p09", "Nadia Petrova", 66, "none", "post-hospital deconditioning"),
    Spec("p10", "Kenji Watanabe", 72, "left", "left knee osteoarthritis"),
    Spec("p11", "Olivia Grant", 55, "right", "right ACL reconstruction"),
    Spec("p12", "Marcus Bell", 64, "left", "left total knee replacement"),
    Spec("p13", "Fatima Haddad", 70, "right", "right hip fracture repair"),
    Spec("p14", "Peter Lindqvist", 59, "none", "low back pain, general strengthening"),
    Spec("p15", "Chloe Martin", 48, "right", "right ankle fracture", enrolled_days_ago=4),
]
SINGLE_SESSION_ID = "p15"


# ---- planning ------------------------------------------------------------------------------------------------

def _ordinary_params(spec: Spec, rng: random.Random) -> None:
    spec.depth_start = rng.uniform(45, 65)
    spec.depth_end = spec.depth_start + rng.uniform(8, 20)
    spec.depth_story_from = 0.0
    load = rng.uniform(47, 51) if spec.affected_side != "none" else 50.0
    spec.load_affected_from = spec.load_affected_to = load
    spec.lean_from = spec.lean_to = rng.uniform(6, 11)
    spec.weekdays = tuple(sorted(rng.sample(range(7), 5)))
    if spec.patient_id != "p04":
        spec.session_hour = rng.choice([8.0, 9.0, 10.5, 13.0, 16.0, 17.5])


def plan(specs: list[Spec], days: int, today: date, rng: random.Random) -> tuple[dict[str, list[DayPlan]], list[str]]:
    """Which days each patient trains. Returns (plans, missers)."""
    ordinary = [s.patient_id for s in specs if s.story == "ordinary" and s.patient_id != SINGLE_SESSION_ID]
    missers = sorted(rng.sample(ordinary, min(3, len(ordinary))))
    plans: dict[str, list[DayPlan]] = {}
    for spec in specs:
        recent_misses = 2 if spec.story == "maya" else (rng.choice([3, 4]) if spec.patient_id in missers else
                                                         rng.choice([0, 0, 1]))
        scheduled_recent = [o for o in range(1, RECENT_DAYS + 1)
                            if (today - timedelta(days=o)).weekday() in spec.weekdays]
        skip_recent = set(rng.sample(scheduled_recent, min(recent_misses, len(scheduled_recent))))
        out: list[DayPlan] = []
        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            if spec.enrolled_days_ago is not None:
                if offset == spec.enrolled_days_ago - 1:
                    out.append(DayPlan(day, offset, ["squat"]))
                continue
            if day.weekday() not in spec.weekdays:
                continue
            if offset <= RECENT_DAYS:
                if offset in skip_recent:
                    continue
            elif rng.random() < 0.08:
                continue
            exercises = ["squat"] + (["glute_bridge"] if day.weekday() == spec.weekdays[-1] else [])
            out.append(DayPlan(day, offset, exercises))
        plans[spec.patient_id] = out
    return plans, missers


def _story_value(spec: Spec, offset: int, days: int, start: float, story_from: float, end: float) -> float:
    """Linear progress over the history, with the story patients' steeper last-3-weeks segment."""
    window = min(STORY_DAYS, days)
    if spec.story == "ordinary":
        return start + (end - start) * (days - offset) / max(days - 1, 1)
    if offset > window:
        return start + (story_from - start) * (days - offset) / max(days - window, 1)
    return story_from + (end - story_from) * (window - offset) / max(window - 1, 1)


# ---- frames --------------------------------------------------------------------------------------------------

class SkeletonCache:
    """Landmarks for a (left, right) depth pair from the mock skeleton, quantised so it is computed once."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int], np.ndarray] = {}
        self._rng = random.Random(0)

    def joints(self, flex_l: np.ndarray, flex_r: np.ndarray) -> np.ndarray:
        # mock skeleton: interior knee angle = 175 - 85 * depth  →  flexion = 5 + 85 * depth
        ql = np.clip(np.rint((flex_l - 5) / 85 * 40), 0, 48).astype(int)
        qr = np.clip(np.rint((flex_r - 5) / 85 * 40), 0, 48).astype(int)
        out = np.empty((len(ql), 99))
        for i, key in enumerate(zip(ql.tolist(), qr.tolist(), strict=True)):
            cached = self._cache.get(key)
            if cached is None:
                body = squat_frame_body(key[0] / 40, key[1] / 40, 0.0, self._rng)
                cached = np.asarray(body["joints"], dtype=float).reshape(99)
                self._cache[key] = cached
            out[i] = cached
        return out


def squat_frames(spec: Spec, depth: float, load_aff: float, lean: float, nrng: np.random.Generator,
                 skeleton: SkeletonCache | None) -> dict[str, np.ndarray]:
    n = SQUAT_S * HZ
    t = np.arange(n) / HZ
    set_idx = np.minimum((t // SET_S).astype(int), SETS - 1)
    in_set = t - set_idx * SET_S
    active = in_set < REPS * REP_S
    r = in_set / REP_S
    rep_idx = np.minimum(r.astype(int), REPS - 1)
    u = np.where(active, (1 - np.cos(2 * np.pi * (r - np.floor(r)))) / 2, 0.0)
    peaks = depth * 1.035 + nrng.normal(0, 1.5, SETS * REPS)  # p95 over a session sits ~3% below the rep peaks
    peak = peaks[set_idx * REPS + rep_idx]
    good = 5 + (peak - 5) * u + nrng.normal(0, 0.6, n)
    ratio = 0.9 if spec.affected_side != "none" else 0.98
    weak = 5 + (peak * ratio - 5) * u + nrng.normal(0, 0.6, n)
    knee_l, knee_r = (weak, good) if spec.affected_side == "left" else (good, weak)
    load_l = (100 - load_aff) if spec.affected_side == "right" else load_aff if spec.affected_side == "left" else 50.0
    conf = np.clip(nrng.normal(0.92, 0.02, n), 0.7, 0.99)
    glitch = nrng.random(n) < 0.01  # tracking glitches: low confidence, absurd knee angle
    conf[glitch] = nrng.uniform(0.25, 0.5, glitch.sum())
    knee_r = np.where(glitch, knee_r + 60, knee_r)
    cols = {
        "set_no": set_idx + 1,
        "knee_l": knee_l, "knee_r": knee_r,
        "hip_l": knee_l * 0.95 + nrng.normal(0, 1, n), "hip_r": knee_r * 0.95 + nrng.normal(0, 1, n),
        "trunk_lean": 4 + lean * u + nrng.normal(0, 0.8, n),
        "trunk_side": nrng.normal(1.0, 0.4, n), "pelvis_rot": nrng.normal(2.0, 0.6, n),
        "knee_ratio": 1.0 - 0.08 * u + nrng.normal(0, 0.02, n),
        "com_x": nrng.normal(0, 0.005, n), "com_y": 0.92 - 0.2 * u, "com_z": 0.03 * u,
        "load_l_pct": load_l + nrng.normal(0, 0.8, n),
        "heel_l_pct": 44 - 6 * u + nrng.normal(0, 0.5, n), "heel_r_pct": 47 - 4 * u + nrng.normal(0, 0.5, n),
        "confidence": conf,
        "_rep": set_idx * REPS + rep_idx, "_peaks": peaks, "_t": t, "_active": active,
    }
    if skeleton is not None:
        cols["_landmarks"] = skeleton.joints(knee_l, knee_r) + nrng.normal(0, 0.002, (n, 99))
    return cols


def bridge_frames(spec: Spec, load_aff: float, nrng: np.random.Generator) -> dict[str, np.ndarray]:
    n = BRIDGE_S * HZ
    t = np.arange(n) / HZ
    u = (1 - np.cos(2 * np.pi * t / 3.0)) / 2  # 3 s per bridge
    load_l = (100 - load_aff) if spec.affected_side == "right" else load_aff if spec.affected_side == "left" else 50.0
    return {
        "set_no": (t // 30).astype(int) + 1,
        "knee_l": 88 + nrng.normal(0, 1.5, n), "knee_r": 88 + nrng.normal(0, 1.5, n),
        "hip_l": 120 + 50 * u + nrng.normal(0, 1, n), "hip_r": 120 + 50 * u + nrng.normal(0, 1, n),
        "trunk_lean": 70 + nrng.normal(0, 1, n), "trunk_side": nrng.normal(0.5, 0.3, n),
        "pelvis_rot": nrng.normal(1.5, 0.5, n), "knee_ratio": 1.0 + nrng.normal(0, 0.02, n),
        "com_x": nrng.normal(0, 0.005, n), "com_y": 0.2 + 0.15 * u, "com_z": nrng.normal(0, 0.005, n),
        "load_l_pct": load_l + nrng.normal(0, 1.0, n),
        "heel_l_pct": 50 + nrng.normal(0, 1, n), "heel_r_pct": 50 + nrng.normal(0, 1, n),
        "confidence": np.clip(nrng.normal(0.9, 0.03, n), 0.65, 0.99), "_t": t,
    }


NUMERIC = ("knee_l", "knee_r", "hip_l", "hip_r", "trunk_lean", "trunk_side", "pelvis_rot", "knee_ratio", "com_x",
           "com_y", "com_z", "load_l_pct", "heel_l_pct", "heel_r_pct", "confidence")


def copy_text(start: datetime, patient_id: str, session_id: str, exercise_id: str, cols: dict[str, np.ndarray]
              ) -> str:
    """Frames as COPY text rows (tab-separated, \\N for NULL)."""
    nums = np.column_stack([np.round(cols[c], 3) for c in NUMERIC]).tolist()
    landmarks = cols.get("_landmarks")
    lm_rows = np.round(landmarks, 4).tolist() if landmarks is not None else None
    sets = cols["set_no"].tolist()
    buf = io.StringIO()
    prefix = f"\t{patient_id}\t{session_id}\t{exercise_id}\t"
    for i, dt in enumerate(cols["_t"].tolist()):
        stamp = (start + timedelta(seconds=dt)).isoformat()
        lm = "{" + ",".join(map(str, lm_rows[i])) + "}" if lm_rows is not None else "\\N"
        buf.write(f"{stamp}{prefix}{sets[i]}\t" + "\t".join(map(str, nums[i])) + f"\t{lm}\n")
    return buf.getvalue()


# ---- daily expectations (computed exactly like progress_v) ------------------------------------------------------

def day_metrics(cols: dict[str, np.ndarray], start: datetime, affected_side: str) -> dict[str, float]:
    keep = cols["confidence"] >= 0.6
    knee = np.maximum(cols["knee_l"], cols["knee_r"])[keep]
    t = cols["_t"][keep]
    load = cols["load_l_pct"][keep]
    tw = float(np.trapezoid(load, t) / (t[-1] - t[0]))
    affected = 100 - tw if affected_side == "right" else tw if affected_side == "left" else None
    lean = cols["trunk_lean"][keep]
    return {"p95_knee": float(np.percentile(knee, 95)), "max_knee": float(knee.max()), "tw_load_left": tw,
            "affected_load_pct": affected, "avg_trunk_lean": float(lean.mean()), "frames": int(keep.sum())}


def _mean(values: list[float]) -> float:
    return round(float(np.mean(values)), 1)


def expected_answers(specs: list[Spec], metrics: dict[str, list[tuple[int, dict[str, float]]]],
                     plans: dict[str, list[DayPlan]], missers: list[str], today: date, seed: int, days: int
                     ) -> dict[str, Any]:
    window = min(STORY_DAYS, days)
    ranking = []
    for spec in specs:
        series = [m for o, m in metrics[spec.patient_id] if o <= window]
        if spec.affected_side == "none" or len(series) < 6:
            continue
        load_from = _mean([m["affected_load_pct"] for m in series[:3]])
        load_to = _mean([m["affected_load_pct"] for m in series[-3:]])
        depth_from = _mean([m["p95_knee"] for m in series[:3]])
        depth_to = _mean([m["p95_knee"] for m in series[-3:]])
        if depth_to - depth_from > 5 and load_from - load_to > 2:
            ranking.append({"patient_id": spec.patient_id, "name": spec.name, "affected_side": spec.affected_side,
                            "affected_load_from": load_from, "affected_load_to": load_to,
                            "drop_pts": round(load_from - load_to, 1), "depth_from": depth_from,
                            "depth_to": depth_to})
    ranking.sort(key=lambda r: r["drop_pts"], reverse=True)

    missed: dict[str, dict[str, int]] = {}
    for spec in specs:
        if spec.enrolled_days_ago is not None:
            continue
        trained = {p.offset for p in plans[spec.patient_id] if p.offset <= RECENT_DAYS}
        scheduled = {o for o in range(1, RECENT_DAYS + 1) if (today - timedelta(days=o)).weekday() in spec.weekdays}
        missed[spec.patient_id] = {"missed": len(scheduled - trained), "sessions_last_14_days": len(trained)}
    by_id = {s.patient_id: s for s in specs}
    out: dict[str, Any] = {
        "seed": seed, "days": days, "patients": len(specs), "generated_for_local_date": today.isoformat(),
        "notes": "Relative to generated_for_local_date. Knee = flexion degrees (p95_knee). Day = America/Chicago.",
        "compensating_ranking": {
            "definition": "patients whose p95 squat depth rose > 5 deg while affected-leg load fell > 2 pts over "
                          "the last 21 days (mean of first vs last 3 squat days), ranked by load drop",
            "ranking": ranking},
        "missed_3plus_last_14_days": {
            "definition": "scheduled training days (5/week, prescriptions.targets.sessions_per_week) in the last 14 "
                          "days (yesterday back) with no session",
            "patients": [{"patient_id": pid, "name": by_id[pid].name, **missed[pid]} for pid in sorted(missed)
                         if missed[pid]["missed"] >= 3],
            "all": missed},
        "single_session_patient": None,
    }
    if "maya" in metrics:
        maya = [m for o, m in metrics["maya"] if o <= window]
        week = [m["affected_load_pct"] for o, m in metrics["maya"] if o <= 6]
        out["maya_depth"] = {
            "from": _mean([m["p95_knee"] for m in maya[:3]]), "to": _mean([m["p95_knee"] for m in maya[-3:]]),
            "window_days": window,
            "definition": "progress_v.p95_knee for squat, mean of the first vs last 3 session days in the window"}
        out["maya_right_load_this_week"] = {
            "value": _mean(week) if week else None,
            "definition": "mean of daily progress_v.affected_load_pct (100 - tw_load_left) for squat over the "
                          "last 7 local days including today (no session today in the simulated data)"}
    if SINGLE_SESSION_ID in plans:
        out["single_session_patient"] = {"patient_id": SINGLE_SESSION_ID, "name": by_id[SINGLE_SESSION_ID].name,
                                         "sessions": sum(len(p.sessions) for p in plans[SINGLE_SESSION_ID])}
    return out


# ---- main ----------------------------------------------------------------------------------------------------

def already_loaded(conn: psycopg.Connection[Any]) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM sessions WHERE simulated)").fetchone()[0]


def simulate(dsn: str, seed: int = 42, days: int = 75, patients: int = 15,
             expected_path: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    specs = [Spec(**{**s.__dict__}) for s in PATIENTS[:patients]]
    for spec in specs:
        if spec.story == "ordinary":
            _ordinary_params(spec, rng)
    today = datetime.now(TZ).date()
    plans, missers = plan(specs, days, today, rng)
    skeleton = SkeletonCache()
    stats = {"patients": len(specs), "sessions": 0, "frames": 0, "frames_with_landmarks": 0, "reps": 0,
             "form_events": 0}
    metrics: dict[str, list[tuple[int, dict[str, float]]]] = {s.patient_id: [] for s in specs}

    with psycopg.connect(dsn, autocommit=True) as conn:
        if already_loaded(conn):
            print("simulated history already loaded; skipping (drop the database to rebuild)")
            return {"skipped": True}
        seed_mod.seed(conn, history_days=days)
        for spec in specs:
            if spec.patient_id != seed_mod.MAYA_ID:
                seed_mod.upsert_patient(
                    conn, patient_id=spec.patient_id, name=spec.name, age=spec.age,
                    height_cm=round(rng.uniform(155, 188)), mass_kg=round(rng.uniform(55, 98)),
                    affected_side=spec.affected_side, condition=spec.condition, contact_name=None,
                    contact_phone=None, simulated=True,
                    created_days_ago=(spec.enrolled_days_ago or days + 5))
                seed_mod.ensure_prescriptions(conn, spec.patient_id)

        session_rows: list[tuple[Any, ...]] = []
        rep_rows: list[tuple[Any, ...]] = []
        event_rows: list[tuple[Any, ...]] = []
        frame_buf = io.StringIO()

        def flush_frames() -> None:
            data = frame_buf.getvalue()
            if data:
                with conn.cursor() as cur, cur.copy(f"COPY pose_frames ({FRAME_COLUMNS}) FROM STDIN") as cp:
                    cp.write(data)
            frame_buf.seek(0)
            frame_buf.truncate()

        for spec in specs:
            prev_offset: int | None = None
            for dp in plans[spec.patient_id]:
                depth = _story_value(spec, dp.offset, days, spec.depth_start, spec.depth_story_from or
                                     spec.depth_start, spec.depth_end) + nrng.normal(0, 1.0)
                load_aff = _story_value(spec, dp.offset, days, spec.load_affected_from, spec.load_affected_from,
                                        spec.load_affected_to) + nrng.normal(0, 0.5)
                lean = _story_value(spec, dp.offset, days, spec.lean_from, spec.lean_from, spec.lean_to)
                back_to_back = prev_offset == dp.offset + 1
                prev_offset = dp.offset
                local_start = datetime.combine(dp.day, datetime.min.time(), TZ) + timedelta(
                    hours=spec.session_hour, minutes=rng.randint(-20, 20))
                start = local_start.astimezone(UTC)
                for exercise_id in dp.sessions:
                    sid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
                    if exercise_id == "squat":
                        cols = squat_frames(spec, depth, load_aff, lean, nrng, skeleton if spec.landmarks else None)
                        metrics[spec.patient_id].append((dp.offset, day_metrics(cols, start, spec.affected_side)))
                        reps_done = SETS * REPS
                        _add_reps(spec, sid, start, cols, lean, rng, rep_rows, event_rows)
                    else:
                        cols = bridge_frames(spec, load_aff, nrng)
                        reps_done = 20
                    duration = float(cols["_t"][-1]) + 1 / HZ
                    pain_pre, pain_post = _pain(spec, back_to_back, rng)
                    session_rows.append((sid, spec.patient_id, exercise_id, start, start + timedelta(seconds=duration),
                                         reps_done, pain_pre, pain_post, rng.randint(4, 7)))
                    frame_buf.write(copy_text(start, spec.patient_id, sid, exercise_id, cols))
                    stats["sessions"] += 1
                    stats["frames"] += len(cols["_t"])
                    if "_landmarks" in cols:
                        stats["frames_with_landmarks"] += len(cols["_t"])
                    start += timedelta(seconds=duration + 60)
                if frame_buf.tell() > 32_000_000:
                    flush_frames()
            flush_frames()

        with conn.cursor() as cur:
            with cur.copy("COPY sessions (id, patient_id, exercise_id, started_at, ended_at, reps_done, pain_pre, "
                          "pain_post, effort, simulated) FROM STDIN") as cp:
                for row in session_rows:
                    cp.write_row((*row, True))
            with cur.copy("COPY reps (session_id, rep, set_no, started_at, ended_at, peak_angle, rom, quality, "
                          "flags) FROM STDIN") as cp:
                for row in rep_rows:
                    cp.write_row(row)
            with cur.copy("COPY form_events (time, patient_id, session_id, rep, rule_id, severity, duration_s) "
                          "FROM STDIN") as cp:
                for row in event_rows:
                    cp.write_row(row)
        stats["reps"], stats["form_events"] = len(rep_rows), len(event_rows)

    stats["wall_s"] = round(time.perf_counter() - started, 1)
    answers = expected_answers(specs, metrics, plans, missers, today, seed, days)
    if expected_path is not None:
        expected_path.write_text(json.dumps(answers, indent=2) + "\n")
    stats["expected"] = answers
    return stats


def _pain(spec: Spec, back_to_back: bool, rng: random.Random) -> tuple[int, int]:
    if spec.story == "maya":
        return 2, 6 if back_to_back else rng.choice([2, 3])
    return rng.randint(0, 3), rng.randint(1, 4) + (1 if back_to_back and spec.story == "mild" else 0)


def _add_reps(spec: Spec, sid: str, start: datetime, cols: dict[str, np.ndarray], lean: float, rng: random.Random,
              rep_rows: list[tuple[Any, ...]], event_rows: list[tuple[Any, ...]]) -> None:
    peaks = cols["_peaks"]
    lean_flag_p = max(0.0, (lean - 10) / 20)  # more trunk-lean flags as the lean grows
    for i in range(SETS * REPS):
        set_no, idx = divmod(i, REPS)
        t0 = start + timedelta(seconds=set_no * SET_S + idx * REP_S)
        t1 = t0 + timedelta(seconds=REP_S)
        flags = []
        if rng.random() < lean_flag_p:
            flags.append("trunk_lean")
        if rng.random() < 0.03:
            flags.append("knee_cave_l" if spec.affected_side != "left" else "knee_cave_r")
        peak = round(float(peaks[i]), 1)
        rep_rows.append((sid, i + 1, set_no + 1, t0, t1, peak, round(peak - 5, 1), 0.5 if flags else 1.0, flags))
        for flag in flags:
            event_rows.append((t1, spec.patient_id, sid, i + 1, flag, "warning", 0.8))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=75)
    parser.add_argument("--patients", type=int, default=len(PATIENTS))
    parser.add_argument("--expected", type=Path, default=None,
                        help="where to write eval answers (default: server/sim/expected.json for the full run)")
    args = parser.parse_args(argv)
    expected = args.expected
    if expected is None and args.days == 75 and args.patients == len(PATIENTS):
        expected = DEFAULT_EXPECTED
    stats = simulate(args.dsn, args.seed, args.days, args.patients, expected)
    stats.pop("expected", None)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
