"""Care Copilot: the tool loop (fake Gemini), SQL through the guard on copilot_ro, the number self-check,
proposals and the humans-decide REST flow, on a fresh local TimescaleDB with a small simulated history."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from server.agents import copilot_fake
from server.agents.copilot import (DEMO_QUESTION, MAX_STEPS, NUDGE, Copilot, PlanChange, cached_demo_answer,
                                   is_demo_question)
from server.ai import gemini
from server.ai.gemini import FunctionCall, Reply
from server.ai.validators import extract_numbers
from server.api.proposals import merged_targets
from server.db.pool import Database, DBError
from server.tests.conftest import AUTH
from server.tests.dbutil import fresh_database, prepare, ro_dsn

PHONE = "+15550100111"


@pytest.fixture(scope="module")
def sim() -> Iterator[tuple[str, dict[str, Any]]]:
    """A small simulated history (4 patients × 22 days: Maya, the 2 milder story patients, 1 ordinary)."""
    from server.sim.simulate_history import simulate

    with fresh_database() as url:
        prepare(url)
        stats = simulate(url, seed=42, days=22, patients=4)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CALL refresh_continuous_aggregate('daily_progress', NULL, NULL)")
        yield url, stats["expected"]


@pytest.fixture
async def db(sim: tuple[str, dict[str, Any]]) -> AsyncIterator[Database]:
    url, _ = sim
    database = Database(url, ro_dsn(url))
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def script() -> Iterator[Callable[[Callable[[list[dict[str, Any]]], Reply]], list[list[dict[str, Any]]]]]:
    """Replace the copilot fake with a scripted one; returns the list of message lists it was called with."""
    def install(fn: Callable[[list[dict[str, Any]]], Reply]) -> list[list[dict[str, Any]]]:
        seen: list[list[dict[str, Any]]] = []

        def fake(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> Reply:
            seen.append(json.loads(json.dumps(messages)))
            return fn(messages)
        gemini.register_fake("copilot", fake)
        return seen

    yield install
    gemini.register_fake("copilot", copilot_fake.fake)


def _call(name: str, **args: Any) -> Reply:
    return Reply(function_call=FunctionCall(name, args))


def _sql(url: str, query: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()


def _mentions(text: str, *values: float, tol: float = 1.0) -> bool:
    """Every value appears in the text within `tol` (progress_v's p95 is a sketch; the sim's key is exact)."""
    found = extract_numbers(text)
    return all(any(abs(n - v) <= tol for n in found) for v in values)


def _tools(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m["role"] == "tool"]


# ---- the fake-driven loop on real data -------------------------------------------------------------------

async def test_demo_question_ranks_from_real_data_and_creates_a_proposal(
        db: Database, sim: tuple[str, dict[str, Any]]) -> None:
    url, expected = sim
    out = await Copilot(db).ask(DEMO_QUESTION)
    assert out["cached"] is False and out["fallback"] is None
    ranking = expected["compensating_ranking"]["ranking"]
    assert [r["patient_id"] for r in ranking][:1] == ["maya"]
    positions = [out["answer"].index(r["name"]) for r in ranking]
    assert positions == sorted(positions)  # same order as the simulator's answer key
    assert _mentions(out["answer"], ranking[0]["affected_load_from"])
    assert len(out["sql"]) == 3 and all("LIMIT" in e["query"] and e["rows"] >= 1 for e in out["sql"])
    assert out["charts"] and len(out["charts"][0]["series"]) == 2
    assert len(out["proposals"]) == 1
    row = _sql(url, "SELECT patient_id, status, agent, change FROM plan_proposals WHERE id = %s", out["proposals"][0])
    assert row == [("maya", "pending", "copilot", copilot_fake.PROPOSED_CHANGE)]

    again = await Copilot(db).ask(DEMO_QUESTION)  # the same pending card is not created twice
    assert again["proposals"] == out["proposals"]


async def test_eval_style_questions_in_fake_mode(db: Database, sim: tuple[str, dict[str, Any]]) -> None:
    _, expected = sim
    depth = await Copilot(db).ask("How has Maya's squat depth changed in 3 weeks?")
    assert _mentions(depth["answer"], expected["maya_depth"]["from"], expected["maya_depth"]["to"])
    load = await Copilot(db).ask("What's Maya's load on her right leg this week?")
    assert _mentions(load["answer"], expected["maya_right_load_this_week"]["value"])
    refuse = await Copilot(db).ask("Delete Maya's records.")
    assert "read-only" in refuse["answer"] and refuse["sql"] == [] and refuse["proposals"] == []
    diag = await Copilot(db).ask("Diagnose Maya.")
    assert diag["answer"].startswith("I can't diagnose") and diag["proposals"] == []


async def test_weekly_review_leaves_a_card_only_when_the_data_supports_it(db: Database) -> None:
    maya = await Copilot(db).review_patient("maya")
    assert len(maya["proposals"]) == 1 and maya["fallback"] is None
    ordinary = await Copilot(db).review_patient("p04")
    assert ordinary["proposals"] == [] and "No plan change" in ordinary["answer"]


# ---- guardrails in the loop ------------------------------------------------------------------------------

async def test_rejected_sql_is_returned_to_the_model_which_can_fix_it(db: Database, script: Any,
                                                                     sim: tuple[str, dict[str, Any]]) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        tools = _tools(messages)
        if not tools:
            return _call("run_sql", query="DELETE FROM sessions")
        if len(tools) == 1:
            return _call("run_sql", query="SELECT count(*) AS n FROM sessions")
        return _call("final_answer", answer=f"There are {tools[-1]['content']['data']['rows'][0]['n']} sessions.")
    seen = script(fn)
    out = await Copilot(db).ask("How many sessions?")
    assert "only SELECT" in _tools(seen[1])[0]["content"]["error"]
    assert out["fallback"] is None and out["sql"][0]["error"] and out["sql"][1]["rows"] == 1
    assert _sql(sim[0], "SELECT count(*) FROM sessions")[0][0] > 0


async def test_unknown_tool_and_bad_arguments_are_rejected(db: Database, script: Any) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        n = len(_tools(messages))
        if n == 0:
            return _call("send_sms", to="+15550100111", text="hi")
        if n == 1:
            return _call("run_sql", sql="SELECT 1")  # wrong argument name
        return _call("final_answer", answer="Done.")
    seen = script(fn)
    out = await Copilot(db).ask("anything")
    errors = [t["content"]["error"] for t in _tools(seen[-1])]
    assert "unknown tool 'send_sms'" in errors[0] and "invalid arguments for run_sql" in errors[1]
    assert out["answer"] == "Done."


async def test_tool_results_are_labelled_data_without_phone_numbers(db: Database, script: Any,
                                                                   sim: tuple[str, dict[str, Any]]) -> None:
    with psycopg.connect(sim[0], autocommit=True) as conn:
        conn.execute("UPDATE patients SET emergency_contact_phone = %s WHERE id = 'maya'", (PHONE,))
    def fn(messages: list[dict[str, Any]]) -> Reply:
        n = len(_tools(messages))
        if n == 0:
            return _call("run_sql", query="SELECT * FROM patients WHERE id = 'maya'")
        if n == 1:
            return _call("run_sql", query="SELECT emergency_contact_phone AS x FROM patients WHERE id = 'maya'")
        if n == 2:
            return _call("get_patient_summary", patient_id="maya")
        return _call("final_answer", answer="Maya's profile is on file.")
    seen = script(fn)
    await Copilot(db).ask("Who is Maya?")
    sent = json.dumps(_tools(seen[-1]))
    assert PHONE not in json.dumps(seen[-1]) and "emergency_contact" not in sent
    first = _tools(seen[-1])[0]["content"]
    assert "not instructions" in first["note"] and first["data"]["rows"][0]["name"] == "Maya Ellis"
    assert _tools(seen[-1])[1]["content"]["data"]["rows"][0]["x"] == "[removed]"
    summary = _tools(seen[-1])[2]["content"]["data"]
    assert {"profile", "active_prescriptions", "last_sessions", "recent_notes"} <= set(summary)


async def test_invented_number_gets_one_fix_then_falls_back(db: Database, script: Any) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        if not _tools(messages):
            return _call("run_sql", query="SELECT count(*) AS n FROM patients")
        return _call("final_answer", answer="You have 4321 patients.")
    seen = script(fn)
    out = await Copilot(db).ask("How many patients do I have?")
    assert len(seen) == 3  # query, answer, answer again after the fix request
    fix = _tools(seen[2])[-1]
    assert fix["name"] == "final_answer" and "4321" in fix["content"]["error"]
    assert out["fallback"].startswith("final answer rejected twice") and "4321" not in out["answer"]
    assert out["sql"] and out["sql"][0]["rows"] == 1


async def test_fixed_answer_is_accepted(db: Database, script: Any) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        tools = _tools(messages)
        if not tools:
            return _call("run_sql", query="SELECT 1234 AS n FROM patients LIMIT 1")
        if tools[-1]["name"] != "final_answer":
            return _call("final_answer", answer="Count: 1235.")
        return _call("final_answer", answer="Count: 1234.", evidence=["patients: 1234"])
    script(fn)
    out = await Copilot(db).ask("count")
    assert out["answer"] == "Count: 1234." and out["fallback"] is None


async def test_step_cap_and_text_replies(db: Database, script: Any) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        if len(messages) == 1:
            return Reply(text="Let me think.")
        return _call("run_sql", query="SELECT 1 AS one FROM patients LIMIT 1")
    seen = script(fn)
    out = await Copilot(db).ask("loop forever")
    assert len(seen) == MAX_STEPS
    assert seen[1][-1] == {"role": "user", "content": NUDGE}
    assert out["fallback"] == "step limit" and len(out["sql"]) == MAX_STEPS - 1


async def test_ai_unavailable_falls_back_and_demo_uses_the_cache(
        db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from server.config import reload_config

    monkeypatch.setenv("GEMINI_DISABLED", "1")
    reload_config()
    out = await Copilot(db).ask("Who is compensating the most?")
    assert out["fallback"].startswith("ai unavailable") and out["cached"] is False
    _write_cache(tmp_path / "data" / "copilot_demo.json")
    demo = await Copilot(db).ask(DEMO_QUESTION)
    assert demo["cached"] is True and demo["answer"] == "Cached: Maya first."


def _write_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"question": DEMO_QUESTION, "answer": {
        "answer": "Cached: Maya first.", "charts": [], "evidence": ["e"], "sql": [{"query": "SELECT 1"}]}}))


async def test_cached_demo_flag_skips_gemini(db: Database, script: Any, monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    from server.config import reload_config

    _write_cache(tmp_path / "data" / "copilot_demo.json")
    monkeypatch.setenv("COPILOT_CACHED_DEMO", "1")
    reload_config()
    seen = script(lambda m: _call("final_answer", answer="live"))
    out = await Copilot(db).ask("which of my patients are compensating")
    assert out["cached"] is True and seen == []
    assert is_demo_question("Which of my patients are compensating?") and not is_demo_question("Who missed days?")


def test_committed_demo_cache_is_real_data() -> None:
    cached = cached_demo_answer()
    assert cached is not None and cached["cached"] is True
    assert cached["sql"] and all("progress_v" in e["query"] or "sessions" in e["query"] for e in cached["sql"])
    assert "Maya" in cached["answer"]


# ---- proposals & drafts ----------------------------------------------------------------------------------

async def test_create_plan_proposal_validates_the_change(db: Database, script: Any) -> None:
    good = {"exercise_id": "squat", "rest_days_between": 1}
    calls = [
        {"patient_id": "maya", "rationale": "Load dropped a lot recently.", "evidence": [],
         "change": {"exercise_id": "deadlift", "sets": 2}},
        {"patient_id": "maya", "rationale": "Load dropped a lot recently.", "evidence": [],
         "change": {"exercise_id": "squat"}},
        {"patient_id": "maya", "rationale": "Load dropped a lot recently.", "evidence": [],
         "change": {**good, "delete_history": True}},
        {"patient_id": "maya", "rationale": "Load fell to 12.34% this month.", "evidence": [], "change": good},
    ]

    def fn(messages: list[dict[str, Any]]) -> Reply:
        n = len(_tools(messages))
        if n < len(calls):
            return _call("create_plan_proposal", **calls[n])
        return _call("final_answer", answer="No proposal.")
    seen = script(fn)
    out = await Copilot(db).ask("propose something")
    errors = [t["content"]["error"] for t in _tools(seen[-1])]
    assert "not an active prescription" in errors[0]
    assert "no effect" in errors[1]
    assert "invalid arguments" in errors[2] and "delete_history" in errors[2]
    assert "12.34" in errors[3]
    assert out["proposals"] == []


async def test_draft_message_is_stored_never_sent(db: Database, script: Any,
                                                 sim: tuple[str, dict[str, Any]]) -> None:
    def fn(messages: list[dict[str, Any]]) -> Reply:
        n = len(_tools(messages))
        if n == 0:
            return _call("draft_message", patient_id="maya", text="You have a torn meniscus, take ibuprofen.")
        if n == 1:
            return _call("draft_message", patient_id="maya", text="Great work this week, Maya. Keep it up!")
        return _call("final_answer", answer="Draft saved for your review.")
    seen = script(fn)
    await Copilot(db).ask("draft a note to Maya")
    tools = _tools(seen[-1])
    assert "rewrite" in tools[0]["content"]["error"]
    assert tools[1]["content"]["data"] == {"saved": True, "sent": False,
                                           "note": "Draft only; the therapist reviews and sends it."}
    rows = _sql(sim[0], "SELECT detail FROM agent_actions WHERE agent = 'copilot' AND action = 'draft_message'")
    assert rows and rows[-1][0] == {"text": "Great work this week, Maya. Keep it up!", "sent": False}


async def test_copilot_ro_cannot_write_even_if_the_guard_is_bypassed(db: Database,
                                                                    sim: tuple[str, dict[str, Any]]) -> None:
    for sql in ("DELETE FROM sessions", "UPDATE prescriptions SET sets = 1",
                "INSERT INTO plan_proposals (patient_id, agent, rationale, evidence, change) "
                "VALUES ('maya', 'x', 'x', '{}', '{}')", "SELECT * FROM alerts", "SELECT * FROM plan_proposals"):
        with pytest.raises(DBError):
            await db.ro_fetch(sql)
    assert _sql(sim[0], "SELECT count(*) FROM plan_proposals WHERE agent = 'x'")[0][0] == 0


def test_merged_targets_keeps_rest_days_and_drills_in_targets() -> None:
    change = PlanChange(exercise_id="squat", rest_days_between=1, add="weight_shift_drill",
                        targets={"knee_deg": 80})
    assert merged_targets({"knee_deg": 90, "sessions_per_week": 5}, change) == {
        "knee_deg": 80, "sessions_per_week": 5, "rest_days_between": 1, "add": ["weight_shift_drill"]}
    drop = PlanChange(exercise_id="squat", remove=["weight_shift_drill"])
    assert merged_targets({"add": ["weight_shift_drill"]}, drop) == {"remove": ["weight_shift_drill"]}


# ---- REST ----------------------------------------------------------------------------------------------------

@pytest.fixture
def client(make_client: Callable[..., TestClient], sim: tuple[str, dict[str, Any]]) -> TestClient:
    url, _ = sim
    return make_client(TIGER_DSN=url, COPILOT_DSN=ro_dsn(url))


def _new_proposal(url: str, change: dict[str, Any]) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute("INSERT INTO plan_proposals (patient_id, agent, rationale, evidence, change) "
                            "VALUES ('maya', 'copilot', 'test', '{}', %s) RETURNING id",
                            (psycopg.types.json.Jsonb(change),)).fetchone()[0]


def test_ask_endpoint(client: TestClient) -> None:
    r = client.post("/copilot/ask", json={"question": "What's Maya's load on her right leg this week?"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert {"answer", "charts", "sql", "evidence", "cached", "proposals", "fallback"} <= set(body)
    assert body["sql"] and "Maya Ellis" in body["answer"]
    assert client.post("/copilot/ask", json={"question": ""}, headers=AUTH).status_code == 422
    assert client.post("/copilot/ask", json={"question": "x"}).status_code == 401


def test_ask_without_database(make_client: Callable[..., TestClient], tmp_path: Path) -> None:
    client = make_client()
    assert client.post("/copilot/ask", json={"question": DEMO_QUESTION}, headers=AUTH).status_code == 503
    _write_cache(tmp_path / "data" / "copilot_demo.json")
    client = make_client(COPILOT_CACHED_DEMO="1")
    r = client.post("/copilot/ask", json={"question": DEMO_QUESTION}, headers=AUTH)
    assert r.status_code == 200 and r.json()["cached"] is True


def test_approve_creates_a_new_prescription_version(client: TestClient, sim: tuple[str, dict[str, Any]]) -> None:
    url, _ = sim
    pid = _new_proposal(url, {"exercise_id": "squat", "rest_days_between": 1, "add": ["weight_shift_drill"],
                              "sets": 2})
    before = _sql(url, "SELECT max(version) FROM prescriptions WHERE patient_id = 'maya' AND exercise_id = 'squat'")
    pending = client.get("/proposals?status=pending", headers=AUTH).json()["proposals"]
    assert any(p["id"] == pid and p["patient_name"] == "Maya Ellis" for p in pending)

    with client.websocket_connect(f"/ws/events?token={AUTH['Authorization'].split()[1]}") as ws:
        r = client.post(f"/proposals/{pid}/approve", json={"therapist_id": "okafor"}, headers=AUTH)
        assert r.status_code == 200, r.text
        event = _next_event(ws, "proposal_decided")
    body = r.json()
    assert body["proposal"]["status"] == "approved" and body["proposal"]["decided_by"] == "okafor"
    assert body["proposal"]["decided_at"]
    rx = body["prescription"]
    assert rx["version"] == before[0][0] + 1 and rx["created_by"] == f"proposal:{pid}" and rx["sets"] == 2
    assert rx["targets"]["rest_days_between"] == 1 and rx["targets"]["add"] == ["weight_shift_drill"]
    assert rx["targets"]["knee_deg"] == 90  # carried over from the old version
    rows = _sql(url, "SELECT version, active FROM prescriptions WHERE patient_id = 'maya' AND exercise_id = 'squat' "
                     "ORDER BY version")
    assert [a for _, a in rows] == [False] * (len(rows) - 1) + [True]
    assert event["proposal_id"] == pid and event["prescription_version"] == rx["version"]
    actions = _sql(url, "SELECT detail FROM agent_actions WHERE action = 'proposal_approved'")
    assert any(d["proposal_id"] == pid and d["decided_by"] == "okafor" for (d,) in actions)

    again = client.post(f"/proposals/{pid}/approve", json={"therapist_id": "okafor"}, headers=AUTH)
    assert again.status_code == 409


def _next_event(ws: Any, kind: str) -> dict[str, Any]:
    for _ in range(500):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind} event")


def test_edit_and_dismiss(client: TestClient, sim: tuple[str, dict[str, Any]]) -> None:
    url, _ = sim
    edited = _new_proposal(url, {"exercise_id": "squat", "sets": 4})
    r = client.post(f"/proposals/{edited}/edit", json={"therapist_id": "okafor",
                                                       "change": {"exercise_id": "squat", "reps": 8}}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["status"] == "edited" and r.json()["prescription"]["reps"] == 8
    bad = _new_proposal(url, {"exercise_id": "squat", "sets": 4})
    assert client.post(f"/proposals/{bad}/edit", json={"therapist_id": "okafor", "change": {
        "exercise_id": "squat", "sets": 50}}, headers=AUTH).status_code == 422
    assert client.post(f"/proposals/{bad}/approve", json={"therapist_id": "nobody"}, headers=AUTH).status_code == 400

    r = client.post(f"/proposals/{bad}/dismiss", json={"therapist_id": "okafor", "reason": "not now"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["proposal"]["status"] == "dismissed" and r.json()["proposal"]["decided_by"] == "okafor"
    assert r.json()["prescription"] is None
    assert client.post("/proposals/999999/dismiss", json={"therapist_id": "okafor"}, headers=AUTH).status_code == 404
    decided = client.get("/proposals?status=dismissed", headers=AUTH).json()["proposals"]
    assert any(p["id"] == bad for p in decided)
    assert client.get("/proposals?status=bogus", headers=AUTH).status_code == 422


def test_proposals_without_database(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    assert client.get("/proposals", headers=AUTH).status_code == 503
    assert client.post("/proposals/1/approve", json={"therapist_id": "okafor"}, headers=AUTH).status_code == 503
