from __future__ import annotations

import asyncio
import base64
import json
import sys
import types as pytypes
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from server.ai import gemini
from server.config import cfg, reload_config

FIXTURES = Path(__file__).parent / "fixtures"


def _text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]})


def _error(code: int, *, quota_id: str | None = None, retry: str | None = None,
          message: str = "boom") -> genai_errors.APIError:
    details: list[dict[str, Any]] = []
    if quota_id:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": quota_id, "quotaValue": "20"}]})
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    body = {"error": {"code": code, "message": message, "status": "X", "details": details}}
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, body)


class FakeSend:
    """Stands in for gemini._send: a script of responses/exceptions, recording the models called."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.models: list[str] = []
        self.requests: list[tuple[list[types.Content], types.GenerateContentConfig]] = []

    async def __call__(self, model: str, contents: list[types.Content],
                       config: types.GenerateContentConfig) -> types.GenerateContentResponse:
        self.models.append(model)
        self.requests.append((contents, config))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item()
        return item


@pytest.fixture(autouse=True)
def _gemini(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    gemini.reset_for_tests()
    monkeypatch.setattr(gemini, "RETRY_DELAYS_S", (0.0, 0.01, 0.03))
    yield
    gemini.reset_for_tests()


def _live(monkeypatch: pytest.MonkeyPatch, send: FakeSend, **env: str) -> None:
    monkeypatch.setenv("GEMINI_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-not-real")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reload_config()
    monkeypatch.setattr(gemini, "_send", send)


USER = [{"role": "user", "content": "hello"}]


# ---- modes / kill switch -------------------------------------------------------------------------

async def test_fake_mode_uses_registered_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("live"))
    monkeypatch.setattr(gemini, "_send", send)
    seen: list[Any] = []

    def fake(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> gemini.Reply:
        seen.append((messages, tools))
        return gemini.Reply(text="canned")

    gemini.register_fake("t_agent", fake)
    reply = await gemini.ask("t_agent", "m", "sys", USER)
    assert reply.text == "canned" and not reply.cached
    assert seen == [(USER, None)] and send.models == []


async def test_fake_mode_without_fake_is_unavailable() -> None:
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("nobody_registered", "m", "sys", USER)


async def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("live"))
    _live(monkeypatch, send, GEMINI_DISABLED="1")
    with pytest.raises(gemini.AIUnavailable, match="disabled"):
        await gemini.ask("a", "m", "sys", USER)
    assert send.models == []


async def test_live_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("live"))
    _live(monkeypatch, send, GEMINI_API_KEY="")
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("a", "m", "sys", USER)
    assert send.models == []


async def test_live_answer_is_cached_and_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("hi there"))
    _live(monkeypatch, send)
    first = await gemini.ask("a", "m", "sys", USER)
    second = await gemini.ask("a", "m", "sys", USER)
    assert first.text == second.text == "hi there"
    assert not first.cached and second.cached
    assert send.models == ["m"]
    files = list((Path(cfg.DATA_DIR) / "ai_cache").glob("*.json"))
    assert len(files) == 1 and "test-gemini-key" not in files[0].read_text()

    monkeypatch.setenv("GEMINI_MODE", "replay")
    reload_config()
    assert (await gemini.ask("a", "m", "sys", USER)).cached
    gemini.register_fake("a", lambda m, t: gemini.Reply(text="fake"))
    assert (await gemini.ask("a", "m", "sys", [{"role": "user", "content": "other"}])).text == "fake"
    assert send.models == ["m"]


def test_cache_key_depends_on_every_input() -> None:
    base = gemini.cache_key("m", "s", USER, None)
    assert base == gemini.cache_key("m", "s", [dict(USER[0])], None)
    assert base != gemini.cache_key("m2", "s", USER, None)
    assert base != gemini.cache_key("m", "s2", USER, None)
    assert base != gemini.cache_key("m", "s", USER, [{"name": "x", "description": "", "parameters": {}}])
    assert base != gemini.cache_key("m", "s", USER, None, json_mode=True)


# ---- retries / fallbacks / timeouts -------------------------------------------------------------

async def test_rate_limit_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(429, quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier"),
                    _text_response("ok"))
    _live(monkeypatch, send)
    reply = await gemini.ask("a", "m", "sys", USER)
    assert reply.text == "ok" and send.models == ["m", "m"]


async def test_daily_quota_429_falls_back_to_next_model(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(429, quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier", retry="40s"),
                    _text_response("from fallback"))
    _live(monkeypatch, send, GEMINI_MODEL_SMART="smart-a", GEMINI_SMART_FALLBACKS="smart-b,smart-c")
    reply = await gemini.ask("copilot", gemini.models.smart(), "sys", USER)
    assert reply.text == "from fallback" and reply.model == "smart-b"
    assert send.models == ["smart-a", "smart-b"]
    assert "smart-a" in gemini.stats()["blocked"]

    # The exhausted model is skipped without a request until midnight Pacific.
    await gemini.ask("copilot", gemini.models.smart(), "sys", [{"role": "user", "content": "again"}])
    assert send.models == ["smart-a", "smart-b", "smart-b"]


async def test_long_retry_hint_moves_on_instead_of_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(429, retry="30s"), _text_response("b"))
    _live(monkeypatch, send, GEMINI_MODEL_SMART="smart-a", GEMINI_SMART_FALLBACKS="smart-b")
    started = asyncio.get_running_loop().time()
    reply = await gemini.ask("copilot", "smart-a", "sys", USER)
    assert reply.model == "smart-b" and asyncio.get_running_loop().time() - started < 1


async def test_fast_model_has_no_fallback_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(429, quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    _live(monkeypatch, send, GEMINI_MODEL_FAST="fast-a")
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("summary", gemini.models.fast(), "sys", USER)
    assert send.models == ["fast-a"]


async def test_503_retried_once_then_unavailable_with_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """503 gets at most one retry before the model is put on a cooldown; with no fallback model to move
    to, the call fails after 2 attempts (not the full 3-attempt retry schedule used for other 5xx)."""
    send = FakeSend(_error(503))
    _live(monkeypatch, send)
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("a", "m", "sys", USER)
    assert send.models == ["m", "m"]
    assert "m" in gemini.stats()["blocked"]


async def test_other_5xx_retried_then_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(502))
    _live(monkeypatch, send)
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("a", "m", "sys", USER)
    assert send.models == ["m", "m", "m"]


async def test_503_moves_to_next_model_without_daily_block(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(503), _error(503), _text_response("from fallback"))
    _live(monkeypatch, send, GEMINI_MODEL_SMART="smart-a", GEMINI_SMART_FALLBACKS="smart-b")
    reply = await gemini.ask("copilot", gemini.models.smart(), "sys", USER)
    assert reply.text == "from fallback" and reply.model == "smart-b"
    assert send.models == ["smart-a", "smart-a", "smart-b"]
    # short cooldown, not an until-midnight block
    assert "smart-a" in gemini.stats()["blocked"]
    assert gemini.budget.blocked_until["smart-a"] < gemini.next_pacific_midnight()


async def test_timeout_moves_to_next_model_within_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow() -> types.GenerateContentResponse:
        await asyncio.sleep(5)
        return _text_response("late")

    send = FakeSend(slow, _text_response("fast"))
    _live(monkeypatch, send, GEMINI_MODEL_SMART="smart-a", GEMINI_SMART_FALLBACKS="smart-b")
    started = asyncio.get_running_loop().time()
    reply = await gemini.ask("copilot", gemini.models.smart(), "sys", USER, timeout_s=0.05)
    assert reply.text == "fast" and reply.model == "smart-b"
    assert send.models == ["smart-a", "smart-b"]
    assert asyncio.get_running_loop().time() - started < 1
    assert "smart-a" in gemini.stats()["blocked"]


async def test_cooldown_expires_and_model_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_700_000_000.0]
    clock_budget = gemini.Budget(clock=lambda: now[0])
    monkeypatch.setattr(gemini, "budget", clock_budget)
    send = FakeSend(_error(503))
    _live(monkeypatch, send)
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("a", "m", "sys", USER)
    assert clock_budget.refusal("m", background=False) == "quota"
    now[0] += gemini.OVERLOAD_COOLDOWN_S + 1
    assert clock_budget.refusal("m", background=False) is None


async def test_client_error_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(404))
    _live(monkeypatch, send)
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("a", "m", "sys", USER)
    assert send.models == ["m"]


async def test_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow() -> types.GenerateContentResponse:
        await asyncio.sleep(5)
        return _text_response("late")

    send = FakeSend(slow)
    _live(monkeypatch, send)
    started = asyncio.get_running_loop().time()
    with pytest.raises(gemini.AIUnavailable, match="timeout"):
        await gemini.ask("a", "m", "sys", USER, timeout_s=0.05)
    assert asyncio.get_running_loop().time() - started < 1
    assert send.models == ["m"]


# ---- thinking_config ----------------------------------------------------------------------------

async def test_thinking_config_sent_with_configured_level(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("ok"))
    _live(monkeypatch, send, GEMINI_THINKING_LEVEL="medium")
    await gemini.ask("a", "m", "sys", USER)
    _, config = send.requests[0]
    assert config.thinking_config.thinking_level.value.lower() == "medium"


async def test_thinking_level_off_sends_no_thinking_config(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("ok"))
    _live(monkeypatch, send, GEMINI_THINKING_LEVEL="off")
    await gemini.ask("a", "m", "sys", USER)
    _, config = send.requests[0]
    assert config.thinking_config is None


async def test_thinking_level_rejected_retried_without_and_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_error(400, message="Thinking level is not supported for this model"),
                    _text_response("ok without thinking"))
    _live(monkeypatch, send, GEMINI_THINKING_LEVEL="low")
    reply = await gemini.ask("a", "flash-lite", "sys", USER)
    assert reply.text == "ok without thinking"
    assert send.models == ["flash-lite", "flash-lite"]
    assert send.requests[0][1].thinking_config.thinking_level.value.lower() == "low"
    assert send.requests[1][1].thinking_config is None
    assert "flash-lite" in gemini._thinking_unsupported

    # Remembered: the next call for this model skips straight to no thinking_config.
    send2 = FakeSend(_text_response("still fine"))
    monkeypatch.setattr(gemini, "_send", send2)
    await gemini.ask("a", "flash-lite", "sys", [{"role": "user", "content": "again"}])
    assert send2.requests[0][1].thinking_config is None


async def test_empty_response_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = types.GenerateContentResponse.model_validate({"candidates": [{"finishReason": "SAFETY"}]})
    _live(monkeypatch, FakeSend(blocked))
    with pytest.raises(gemini.AIUnavailable, match="empty"):
        await gemini.ask("a", "m", "sys", USER)


# ---- budget ------------------------------------------------------------------------------------

async def test_budget_blocks_background_at_85_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("ok"))
    _live(monkeypatch, send, GEMINI_DAILY_LIMITS="m=20")
    gemini.budget.seed({"m": 17})
    with pytest.raises(gemini.AIUnavailable, match="budget"):
        await gemini.ask("insight", "m", "sys", USER, background=True)
    assert send.models == []
    assert (await gemini.ask("copilot", "m", "sys", USER)).text == "ok"
    assert gemini.stats()["today"]["m"] == {"count": 18, "limit": 20, "pct": 90.0}


async def test_budget_refuses_everyone_at_100_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch, FakeSend(_text_response("ok")), GEMINI_DAILY_LIMITS="m=20")
    gemini.budget.seed({"m": 20})
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("copilot", "m", "sys", USER)


def test_budget_resets_at_pacific_midnight() -> None:
    now = [datetime(2026, 10, 10, 23, 59, tzinfo=gemini.PACIFIC).timestamp()]
    b = gemini.Budget(clock=lambda: now[0])
    b.count("gemini-3.8-flash")
    b.block_until_midnight("gemini-3.8-flash")
    assert b.refusal("gemini-3.8-flash", background=False) == "quota"
    now[0] += 120
    assert b.refusal("gemini-3.8-flash", background=False) is None
    assert b.stats()["today"] == {}


def test_known_daily_limits() -> None:
    assert gemini.daily_limit("gemini-3.5-flash-lite") == 500
    assert gemini.daily_limit("gemini-3.8-flash") == 20
    assert gemini.daily_limit("something-else") == cfg.GEMINI_DAILY_LIMIT


async def test_seed_budget_from_ai_calls() -> None:
    class DB:
        async def fetch(self, sql: str, params: Any) -> list[dict[str, Any]]:
            assert "ai_calls" in sql and isinstance(params[0], datetime)
            return [{"model": "gemini-3.8-flash", "n": 5}]

    await gemini.seed_budget(DB())
    assert gemini.budget.counts["gemini-3.8-flash"] == 5


# ---- ai_calls logging ----------------------------------------------------------------------------

async def test_every_call_written_to_ai_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[tuple[Any, ...]] = []

    async def insert_ai_call(db: Any, *args: Any) -> None:
        rows.append(args)

    writes = pytypes.ModuleType("server.db.writes")
    writes.insert_ai_call = insert_ai_call  # type: ignore[attr-defined]
    pkg = pytypes.ModuleType("server.db")
    pkg.writes = writes  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server.db", pkg)
    monkeypatch.setitem(sys.modules, "server.db.writes", writes)
    from server import state

    monkeypatch.setattr(state, "_state", pytypes.SimpleNamespace(db=object()))

    _live(monkeypatch, FakeSend(_error(429), _text_response("ok")))
    await gemini.ask("coach", "m", "sys", USER)
    await gemini.ask("coach", "m", "sys", USER)
    await asyncio.sleep(0)
    assert [(r[0], r[1], r[3], r[4], r[5]) for r in rows] == [
        ("coach", "m", False, False, "429"), ("coach", "m", True, False, None), ("coach", "m", True, True, None)]


# ---- neutral format <-> SDK ----------------------------------------------------------------------

def test_messages_to_sdk_contents() -> None:
    sig = base64.b64encode(b"sig-bytes").decode()
    contents = gemini.to_sdk_contents([
        {"role": "user", "content": "how many reps?"},
        {"role": "model", "function_call": {"name": "run_sql", "args": {"q": "x"}}, "thought_signature": sig},
        {"role": "tool", "name": "run_sql", "content": {"rows": [1]}},
        {"role": "tool", "name": "other", "content": "plain"},
        {"role": "model", "content": "12 reps."},
    ])
    assert [c.role for c in contents] == ["user", "model", "user", "model"]
    call_part = contents[1].parts[0]
    assert call_part.function_call.name == "run_sql" and call_part.function_call.args == {"q": "x"}
    assert call_part.thought_signature == b"sig-bytes"
    assert [p.function_response.name for p in contents[2].parts] == ["run_sql", "other"]
    assert contents[2].parts[1].function_response.response == {"result": "plain"}
    assert contents[3].parts[0].text == "12 reps."


def test_tools_to_sdk_config() -> None:
    tool = {"name": "run_sql", "description": "Run a query",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}
    config = gemini.to_sdk_config("be nice", [tool], json_mode=False)
    decl = config.tools[0].function_declarations[0]
    assert decl.name == "run_sql" and decl.parameters_json_schema == tool["parameters"]
    assert config.system_instruction == "be nice" and config.response_mime_type is None
    assert gemini.to_sdk_config("s", None, json_mode=True).response_mime_type == "application/json"


def test_reply_message_round_trip() -> None:
    reply = gemini.Reply(function_call=gemini.FunctionCall("f", {"a": 1}, "id1"),
                         thought_signature=base64.b64encode(b"s").decode())
    content = gemini.to_sdk_contents([reply.as_message()])[0]
    assert content.parts[0].function_call.id == "id1" and content.parts[0].thought_signature == b"s"
    assert gemini.Reply.from_dict(reply.to_dict()) == reply


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_real_text_response_fixture() -> None:
    reply = gemini.from_sdk_response(
        types.GenerateContentResponse.model_validate(_fixture("gemini_text_response.json")))
    assert reply.text and reply.function_call is None


def test_real_function_call_round_trip_fixture() -> None:
    call = gemini.from_sdk_response(
        types.GenerateContentResponse.model_validate(_fixture("gemini_function_call_response.json")))
    assert call.function_call is not None and call.function_call.name == "get_rep_count"
    msg = call.as_message()
    content = gemini.to_sdk_contents([msg])[0]
    original = _fixture("gemini_function_call_response.json")["candidates"][0]["content"]["parts"]
    fc_part = next(p for p in original if "function_call" in p)
    assert content.parts[-1].function_call.args == fc_part["function_call"]["args"]
    # Gemini 3 rejects a function-call turn whose thought signature is not echoed back unchanged.
    assert content.parts[-1].thought_signature == base64.urlsafe_b64decode(fc_part["thought_signature"])
    final = gemini.from_sdk_response(
        types.GenerateContentResponse.model_validate(_fixture("gemini_function_result_response.json")))
    assert final.text and final.function_call is None


async def test_replay_mode_serves_saved_real_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = Path(cfg.DATA_DIR) / "ai_cache"
    cache_dir.mkdir(parents=True)
    for path in FIXTURES.glob("ai_cache_*.json"):
        data = json.loads(path.read_text())
        (cache_dir / f"{data['key']}.json").write_text(path.read_text())
    monkeypatch.setenv("GEMINI_MODE", "replay")
    reload_config()
    reply = await gemini.ask("probe", "gemini-3.5-flash-lite",
                             "You are a friendly exercise coach. Answer in one short sentence.",
                             [{"role": "user", "content": "Say hello to Maya, who is about to do 12 squats."}])
    assert reply.cached and reply.text == "Hello Maya, get ready to crush those 12 squats!"


async def test_discarded_answer_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("bad 999"), _text_response("good"))
    _live(monkeypatch, send)
    bad = await gemini.ask("a", "m", "sys", USER)
    await gemini.discard(bad)  # the caller's validator rejected it
    assert not list((Path(cfg.DATA_DIR) / "ai_cache").glob("*.json"))
    good = await gemini.ask("a", "m", "sys", USER)
    assert good.text == "good" and send.models == ["m", "m"]
    again = await gemini.ask("a", "m", "sys", USER)
    assert again.cached and again.text == "good"


async def test_replay_mode_keeps_recordings_on_discard(monkeypatch: pytest.MonkeyPatch) -> None:
    send = FakeSend(_text_response("recorded"))
    _live(monkeypatch, send)
    await gemini.ask("a", "m", "sys", USER)
    monkeypatch.setenv("GEMINI_MODE", "replay")
    reload_config()
    hit = await gemini.ask("a", "m", "sys", USER)
    await gemini.discard(hit)
    assert (await gemini.ask("a", "m", "sys", USER)).cached


async def test_failing_fake_raises_ai_unavailable() -> None:
    def broken(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> gemini.Reply:
        raise KeyError("set")

    gemini.register_fake("broken_agent", broken)
    with pytest.raises(gemini.AIUnavailable):
        await gemini.ask("broken_agent", "m", "sys", USER)


def test_text_part_thought_signature_is_kept() -> None:
    resp = types.GenerateContentResponse.model_validate({"candidates": [{"content": {"role": "model", "parts": [
        {"text": "hi", "thoughtSignature": base64.b64encode(b"txt-sig").decode()}]}, "finishReason": "STOP"}]})
    reply = gemini.from_sdk_response(resp)
    assert reply.thought_signature == base64.b64encode(b"txt-sig").decode()
    [content] = gemini.to_sdk_contents([reply.as_message()])
    assert content.parts[0].thought_signature == b"txt-sig"
