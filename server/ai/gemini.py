"""The only module that talks to Gemini (TRI §6.2). Nobody else imports the Google SDK.

Every agent calls `await ask(...)` with the neutral message format:
  {"role": "user", "content": str}
  {"role": "model", "content": str}  or  {"role": "model", "function_call": {"name", "args"[, "id"]}}
  {"role": "tool", "name": str, "content": dict}
A model message may also carry "thought_signature" (base64): Gemini 3 requires it echoed back with the
function call, so callers should append `reply.as_message()` unchanged.

Modes (GEMINI_MODE): fake = registered fake per agent; replay = saved answers from DATA_DIR/ai_cache, else fake;
live = cache, else the real API. Any failure raises AIUnavailable and the caller uses its fallback.

`allowed_tools` forces a function call restricted to those tool names (function calling mode ANY); None = the
model may answer with text or any tool. Fakes see it only if they take an `allowed_tools` keyword.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from server.config import cfg
from server.logging_setup import get_logger

log = get_logger("ai.gemini")

PACIFIC = ZoneInfo("America/Los_Angeles")  # free-tier daily quota resets at midnight Pacific
RETRY_DELAYS_S = (0.0, 1.0, 3.0)
MAX_HINT_WAIT_S = 3.0  # a retry hint longer than this moves on to the next model instead of waiting
RPM_COOLDOWN_S = 60.0
OVERLOAD_COOLDOWN_S = 60.0  # 503/timeout: short cooldown, not a daily block
WARN_AT = 0.70
BACKGROUND_STOP_AT = 0.85
DEADLINE_FACTOR = 3  # whole ask() (retries + fallbacks) is bounded by timeout_s * DEADLINE_FACTOR

THINKING_LEVELS = {"minimal", "low", "medium", "high"}  # plus "off" = no thinking_config

# Free-tier requests per day per model (AI Studio, Oct 2026). GEMINI_DAILY_LIMITS overrides,
# GEMINI_DAILY_LIMIT covers models not listed.
DEFAULT_DAILY_LIMITS = {
    "gemini-3.8-flash": 20,
    "gemini-3.7-flash": 20,
    "gemini-3.6-flash": 20,
    "gemini-3.5-flash": 20,
    "gemini-3.5-flash-lite": 500,
}


class AIUnavailable(Exception):
    """Gemini could not answer (disabled, budget, quota, timeout, error). The caller uses its fallback."""


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class Reply:
    text: str | None = None
    function_call: FunctionCall | None = None
    cached: bool = False
    thought_signature: str | None = None  # base64, echoed back via as_message()
    model: str | None = None  # the model that actually answered (may be a fallback)
    cache_key: str | None = field(default=None, repr=False, compare=False)  # set when stored in the cache

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "model"}
        if self.text:
            msg["content"] = self.text
        if self.function_call is not None:
            call: dict[str, Any] = {"name": self.function_call.name, "args": self.function_call.args}
            if self.function_call.id:
                call["id"] = self.function_call.id
            msg["function_call"] = call
        if "content" not in msg and "function_call" not in msg:
            msg["content"] = ""
        if self.thought_signature:
            msg["thought_signature"] = self.thought_signature
        return msg

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text, "thought_signature": self.thought_signature, "model": self.model,
                               "function_call": None}
        if self.function_call is not None:
            out["function_call"] = {"name": self.function_call.name, "args": self.function_call.args,
                                    "id": self.function_call.id}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, cached: bool = False) -> Reply:
        fc = data.get("function_call")
        return cls(text=data.get("text"),
                   function_call=FunctionCall(fc["name"], dict(fc.get("args") or {}), fc.get("id")) if fc else None,
                   cached=cached, thought_signature=data.get("thought_signature"), model=data.get("model"))


FakeFn = Callable[..., Reply]
_fakes: dict[str, FakeFn] = {}
_fake_takes_allowed: dict[str, bool] = {}


def register_fake(agent: str, fn: FakeFn) -> None:
    """Fake-mode (and replay-miss) behaviour for `agent`: fn(messages, tools[, allowed_tools=...]) -> Reply."""
    _fakes[agent] = fn
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        params = []  # type: ignore[assignment]
    _fake_takes_allowed[agent] = any(p.name == "allowed_tools" or p.kind is p.VAR_KEYWORD for p in params)


def fast_model() -> str:
    return cfg.GEMINI_MODEL_FAST


def smart_model() -> str:
    return cfg.GEMINI_MODEL_SMART


models = SimpleNamespace(fast=fast_model, smart=smart_model)


# ---- budget / quota -------------------------------------------------------------------------------

def pacific_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), PACIFIC).date().isoformat()


def next_pacific_midnight(now: float | None = None) -> float:
    local = datetime.fromtimestamp(now if now is not None else time.time(), PACIFIC)
    midnight = datetime(local.year, local.month, local.day, tzinfo=PACIFIC) + timedelta(days=1)
    return midnight.timestamp()


def pacific_day_start(now: float | None = None) -> datetime:
    local = datetime.fromtimestamp(now if now is not None else time.time(), PACIFIC)
    return datetime(local.year, local.month, local.day, tzinfo=PACIFIC)


def api_keys() -> list[str]:
    """GEMINI_API_KEY first, then GEMINI_API_KEYS, without duplicates."""
    keys: list[str] = []
    for k in [cfg.GEMINI_API_KEY or "", *(cfg.GEMINI_API_KEYS or "").split(",")]:
        if k.strip() and k.strip() not in keys:
            keys.append(k.strip())
    return keys


def daily_limit(model: str) -> int:
    """Per-key limit times the number of keys (each key has its own free-tier quota)."""
    limits = dict(DEFAULT_DAILY_LIMITS)
    for item in (cfg.GEMINI_DAILY_LIMITS or "").split(","):
        name, _, value = item.partition("=")
        if name.strip() and value.strip().isdigit():
            limits[name.strip()] = int(value.strip())
    return limits.get(model, cfg.GEMINI_DAILY_LIMIT) * max(len(api_keys()), 1)


class Budget:
    """Requests sent per model per Pacific day, plus models blocked by 429s (until midnight or a cooldown)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.day = pacific_day(clock())
        self.counts: dict[str, int] = {}
        self.blocked_until: dict[str, float] = {}
        self._warned: set[str] = set()
        self.key_index = 0  # which of api_keys() is in use
        self.keys_spent: dict[int, float] = {}  # key index -> blocked until (next Pacific midnight)

    def _roll(self) -> None:
        day = pacific_day(self.clock())
        if day != self.day:
            self.day, self.counts, self._warned = day, {}, set()

    def refusal(self, model: str, background: bool) -> str | None:
        """Why `model` must not be called now, or None."""
        self._roll()
        if self.blocked_until.get(model, 0.0) > self.clock():
            return "quota"
        used = self.counts.get(model, 0) / max(daily_limit(model), 1)
        if used >= 1.0:
            return "budget"
        if background and used >= BACKGROUND_STOP_AT:
            return "budget"
        return None

    def count(self, model: str) -> None:
        self._roll()
        self.counts[model] = self.counts.get(model, 0) + 1
        limit = daily_limit(model)
        if self.counts[model] >= WARN_AT * limit and model not in self._warned:
            self._warned.add(model)
            log.warning("gemini daily budget at 70%", extra={"model": model, "count": self.counts[model],
                                                             "limit": limit})

    def block_until_midnight(self, model: str) -> None:
        self.blocked_until[model] = next_pacific_midnight(self.clock())

    def rotate_key(self, n_keys: int) -> bool:
        """The active key hit a daily quota: switch to the next key not spent today. False when none is left."""
        now = self.clock()
        self.keys_spent[self.key_index] = next_pacific_midnight(now)
        for step in range(1, n_keys):
            i = (self.key_index + step) % n_keys
            if self.keys_spent.get(i, 0.0) <= now:
                self.key_index = i
                self.blocked_until.clear()  # those 429s were the old key's
                return True
        return False

    def cooldown(self, model: str, seconds: float) -> None:
        self.blocked_until[model] = max(self.blocked_until.get(model, 0.0), self.clock() + seconds)

    def seed(self, counts: dict[str, int]) -> None:
        self._roll()
        for model, n in counts.items():
            self.counts[model] = max(self.counts.get(model, 0), int(n))

    def stats(self) -> dict[str, Any]:
        self._roll()
        now = self.clock()
        return {
            "day": self.day,
            "today": {m: {"count": n, "limit": daily_limit(m), "pct": round(100 * n / max(daily_limit(m), 1), 1)}
                      for m, n in self.counts.items()},
            "blocked": {m: datetime.fromtimestamp(t, PACIFIC).isoformat(timespec="seconds")
                        for m, t in self.blocked_until.items() if t > now},
            "key_index": self.key_index,
            "keys_spent": sorted(i for i, t in self.keys_spent.items() if t > now),
        }


budget = Budget()

# Models that answered a thinking_config with an error, remembered for the rest of the process so we
# stop paying for a doomed request on every call.
_thinking_unsupported: set[str] = set()


def _thinking_config(model: str) -> types.ThinkingConfig | None:
    level = (cfg.GEMINI_THINKING_LEVEL or "off").strip().lower()
    if level == "off" or model in _thinking_unsupported:
        return None
    if level not in THINKING_LEVELS:
        log.warning("invalid GEMINI_THINKING_LEVEL, sending none", extra={"level": level})
        return None
    return types.ThinkingConfig(thinking_level=level)


def _thinking_rejected(exc: genai_errors.APIError) -> bool:
    return exc.code == 400 and "thinking" in (exc.message or "").lower()


def model_chain(model: str) -> list[str]:
    """The SMART model falls back through GEMINI_SMART_FALLBACKS on quota errors; other models stand alone."""
    if model != cfg.GEMINI_MODEL_SMART:
        return [model]
    chain = [model]
    for m in (cfg.GEMINI_SMART_FALLBACKS or "").split(","):
        m = m.strip()
        if m and m not in chain:
            chain.append(m)
    return chain


# ---- cache ----------------------------------------------------------------------------------------

def cache_key(model: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
              json_mode: bool = False, allowed_tools: list[str] | None = None) -> str:
    data: dict[str, Any] = {"model": model, "system": system, "messages": messages, "tools": tools or [],
                            "json": json_mode}
    if allowed_tools is not None:  # absent when unused, so existing recordings keep their keys
        data["allowed_tools"] = list(allowed_tools)
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return Path(cfg.DATA_DIR) / "ai_cache" / f"{key}.json"


def _read_cache(key: str) -> Reply | None:
    try:
        data = json.loads(_cache_path(key).read_text(encoding="utf-8"))
        return Reply.from_dict(data["reply"], cached=True)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(key: str, agent: str, model: str, reply: Reply) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"key": key, "agent": agent, "model": model,
                               "created": datetime.now(PACIFIC).isoformat(timespec="seconds"),
                               "reply": reply.to_dict()}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---- neutral format <-> SDK -----------------------------------------------------------------------

def to_sdk_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    contents: list[types.Content] = []
    for msg in messages:
        role = msg.get("role")
        parts: list[types.Part] = []
        if role == "tool":
            sdk_role = "user"
            content = msg.get("content")
            parts.append(types.Part(function_response=types.FunctionResponse(
                name=msg["name"], id=msg.get("id"),
                response=content if isinstance(content, dict) else {"result": content})))
        elif role in ("user", "model"):
            sdk_role = role
            sig = base64.b64decode(msg["thought_signature"]) if msg.get("thought_signature") else None
            if msg.get("content"):
                parts.append(types.Part(text=msg["content"]))
            if msg.get("function_call"):
                fc = msg["function_call"]
                parts.append(types.Part(function_call=types.FunctionCall(
                    name=fc["name"], args=fc.get("args") or {}, id=fc.get("id")), thought_signature=sig))
            elif sig and parts:
                parts[0].thought_signature = sig
            if not parts:
                parts.append(types.Part(text=""))
        else:
            raise ValueError(f"unknown message role {role!r}")
        # Gemini wants alternating turns: merge consecutive same-role messages (e.g. parallel tool results).
        if contents and contents[-1].role == sdk_role:
            contents[-1].parts = [*(contents[-1].parts or []), *parts]
        else:
            contents.append(types.Content(role=sdk_role, parts=parts))
    return contents


def to_sdk_config(system: str, tools: list[dict[str, Any]] | None, json_mode: bool,
                  thinking_config: types.ThinkingConfig | None = None,
                  allowed_tools: list[str] | None = None) -> types.GenerateContentConfig:
    sdk_tools = None
    tool_config = None
    if tools:
        sdk_tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                      parameters_json_schema=t.get("parameters") or {"type": "object",
                                                                                     "properties": {}})
            for t in tools])]
        if allowed_tools:
            tool_config = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=list(allowed_tools)))
    return types.GenerateContentConfig(
        system_instruction=system or None,
        tools=sdk_tools,
        tool_config=tool_config,
        response_mime_type="application/json" if json_mode else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=thinking_config,
    )


class _EmptyResponse(Exception):
    pass


def from_sdk_response(resp: types.GenerateContentResponse) -> Reply:
    candidates = resp.candidates or []
    if not candidates or candidates[0].content is None or not candidates[0].content.parts:
        reason = candidates[0].finish_reason if candidates else getattr(resp.prompt_feedback, "block_reason", None)
        raise _EmptyResponse(f"empty response ({reason})")
    texts: list[str] = []
    call: FunctionCall | None = None
    signature: bytes | None = None
    for part in candidates[0].content.parts:
        if part.function_call is not None and call is None:
            call = FunctionCall(name=part.function_call.name or "", args=dict(part.function_call.args or {}),
                                id=part.function_call.id)
            signature = part.thought_signature
        elif part.text and not part.thought:
            texts.append(part.text)
            if call is None and signature is None:
                signature = part.thought_signature
    if call is None and not texts:
        raise _EmptyResponse(f"no text or function call ({candidates[0].finish_reason})")
    return Reply(text="".join(texts) or None, function_call=call,
                 thought_signature=base64.b64encode(signature).decode() if signature else None)


# ---- SDK call -------------------------------------------------------------------------------------

class _RateLimited(Exception):
    def __init__(self, daily: bool, retry_after: float | None) -> None:
        super().__init__("429")
        self.daily = daily
        self.retry_after = retry_after


class _Retryable(Exception):
    pass


class _Overloaded(Exception):
    """503 UNAVAILABLE/overloaded: gets at most one retry, then the caller moves to the next model."""


class _Fatal(Exception):
    pass


_client: genai.Client | None = None
_client_key: str | None = None


def _get_client() -> genai.Client:
    global _client, _client_key
    keys = api_keys()
    key = keys[budget.key_index % len(keys)]
    if _client is None or _client_key != key:
        _client = genai.Client(api_key=key)
        _client_key = key
    return _client


async def _send(model: str, contents: list[types.Content],
                config: types.GenerateContentConfig) -> types.GenerateContentResponse:
    """The single network call. Tests replace this function."""
    return await _get_client().aio.models.generate_content(model=model, contents=contents, config=config)


_DELAY_RE = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)


def _error_details(exc: genai_errors.APIError) -> list[dict[str, Any]]:
    details = exc.details if isinstance(exc.details, dict) else {}
    inner = details.get("error", details)
    items = inner.get("details") if isinstance(inner, dict) else None
    return [d for d in items or [] if isinstance(d, dict)]


def _retry_after(exc: genai_errors.APIError) -> float | None:
    for d in _error_details(exc):
        delay = d.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                pass
    m = _DELAY_RE.search(exc.message or "")
    return float(m.group(1)) if m else None


def _is_daily(exc: genai_errors.APIError) -> bool:
    for d in _error_details(exc):
        for v in d.get("violations") or []:
            if isinstance(v, dict) and "perday" in str(v.get("quotaId", "")).lower():
                return True
    return "per day" in (exc.message or "").lower()


def _classify(exc: BaseException) -> Exception:
    if isinstance(exc, genai_errors.APIError):
        if exc.code == 429:
            return _RateLimited(_is_daily(exc), _retry_after(exc))
        if exc.code == 503:
            return _Overloaded(f"{exc.code} {exc.status}")
        if exc.code in (408, 500, 502, 504):
            return _Retryable(f"{exc.code} {exc.status}")
        return _Fatal(f"{exc.code} {exc.status}")
    if isinstance(exc, httpx.TransportError):
        return _Retryable(type(exc).__name__)
    if isinstance(exc, _EmptyResponse):
        return _Fatal(str(exc))
    return _Fatal(type(exc).__name__)


async def _call_sdk(model: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                    json_mode: bool, allowed_tools: list[str] | None = None) -> Reply:
    contents = to_sdk_contents(messages)
    thinking = _thinking_config(model)
    try:
        resp = await _send(model, contents, to_sdk_config(system, tools, json_mode, thinking, allowed_tools))
        return from_sdk_response(resp)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise
    except genai_errors.APIError as exc:
        if thinking is not None and _thinking_rejected(exc):
            _thinking_unsupported.add(model)
            log.info("gemini thinking_level rejected, retrying without it",
                     extra={"model": model, "level": cfg.GEMINI_THINKING_LEVEL})
            try:
                resp = await _send(model, contents, to_sdk_config(system, tools, json_mode, None, allowed_tools))
                return from_sdk_response(resp)
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                raise
            except Exception as exc2:  # noqa: BLE001 - mapped to retry / fallback categories
                raise _classify(exc2) from exc2
        raise _classify(exc) from exc
    except Exception as exc:  # noqa: BLE001 - mapped to retry / fallback categories
        raise _classify(exc) from exc


# ---- logging --------------------------------------------------------------------------------------

_counters: dict[str, int] = {"live_ok": 0, "live_failed": 0, "cached": 0, "fake": 0, "unavailable": 0}
_tasks: set[asyncio.Task[Any]] = set()


def _record(agent: str, model: str, ms: int, ok: bool, cached: bool, error: str | None,
            *, persist: bool = True) -> None:
    log.info("ai call", extra={"agent": agent, "model": model, "ms": ms, "ok": ok, "cached": cached,
                               "error": error})
    if persist:
        _persist(agent, model, ms, ok, cached, error)


def _persist(agent: str, model: str, ms: int, ok: bool, cached: bool, error: str | None) -> None:
    """Fire-and-forget ai_calls row through server.db.writes, when a database is attached."""
    try:
        from server.state import get_state

        db = get_state().db
    except RuntimeError:
        return
    if db is None:
        return
    try:
        from server.db import writes  # type: ignore[import-not-found,attr-defined,unused-ignore]
    except ImportError:
        return
    insert = getattr(writes, "insert_ai_call", None)
    if insert is None:
        return
    task = asyncio.get_running_loop().create_task(insert(db, agent, model, ms, ok, cached, error))
    _tasks.add(task)
    task.add_done_callback(_persist_done)


def _persist_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.warning("ai_calls insert failed", extra={"error": repr(task.exception())})


async def seed_budget(db: Any, timeout_s: float = 2.0) -> None:
    """Seed today's per-model request counts from ai_calls (requests actually sent to Google)."""
    sql = ("SELECT model, count(*) AS n FROM ai_calls WHERE time >= %s AND cached IS NOT TRUE "
           "AND coalesce(error, '') NOT IN ('429', 'disabled', 'budget', 'quota') GROUP BY model")
    try:
        rows = await asyncio.wait_for(db.fetch(sql, (pacific_day_start(),)), timeout_s)
        budget.seed({r["model"]: r["n"] for r in rows if r.get("model")})
    except Exception as exc:  # noqa: BLE001 - budget seeding is best effort
        log.warning("ai budget seed failed", extra={"error": repr(exc)})


def stats() -> dict[str, Any]:
    return {"mode": cfg.GEMINI_MODE, "disabled": cfg.GEMINI_DISABLED, "calls": dict(_counters), **budget.stats()}


# ---- ask ------------------------------------------------------------------------------------------

def _fake(agent: str, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
          allowed_tools: list[str] | None = None) -> Reply:
    fn = _fakes.get(agent)
    if fn is None:
        _counters["unavailable"] += 1
        raise AIUnavailable(f"no fake registered for agent {agent!r}")
    try:
        if _fake_takes_allowed.get(agent):
            reply = fn(messages, tools, allowed_tools=allowed_tools)
        else:
            reply = fn(messages, tools)
    except Exception as exc:  # noqa: BLE001 - ask() only ever raises AIUnavailable
        _counters["unavailable"] += 1
        raise AIUnavailable(f"fake for {agent!r} failed: {exc!r}") from None
    reply.model = reply.model or model
    _counters["fake"] += 1
    _record(agent, model, 0, True, False, None, persist=False)
    return reply


async def ask(agent: str, model: str, system: str, messages: list[dict[str, Any]],
              tools: list[dict[str, Any]] | None = None, timeout_s: float = 4.0, background: bool = False,
              json_mode: bool = False, allowed_tools: list[str] | None = None) -> Reply:
    """One model turn. `json_mode` asks for a JSON response; `allowed_tools` forces a call to one of those tools
    (names not in `tools` are dropped; none left = no restriction). Raises AIUnavailable; never anything else."""
    if cfg.GEMINI_DISABLED:
        _counters["unavailable"] += 1
        raise AIUnavailable("disabled")
    if allowed_tools is not None:
        names = {t["name"] for t in tools or []}
        allowed_tools = [n for n in allowed_tools if n in names] or None
    mode = cfg.GEMINI_MODE
    if mode == "fake":
        return _fake(agent, model, messages, tools, allowed_tools)
    key = cache_key(model, system, messages, tools, json_mode, allowed_tools)
    hit = await asyncio.to_thread(_read_cache, key)
    if hit is not None:
        hit.cache_key = key
        _counters["cached"] += 1
        _record(agent, hit.model or model, 0, True, True, None)
        return hit
    if mode == "replay":
        return _fake(agent, model, messages, tools, allowed_tools)
    if mode != "live":
        _counters["unavailable"] += 1
        raise AIUnavailable(f"unknown GEMINI_MODE {mode!r}")
    if not api_keys():
        _counters["unavailable"] += 1
        raise AIUnavailable("no api key")
    reply = await _ask_live(agent, model, system, messages, tools, timeout_s, background, json_mode,
                            allowed_tools)
    try:
        await asyncio.to_thread(_write_cache, key, agent, model, reply)
        reply.cache_key = key
    except OSError as exc:
        log.warning("ai cache write failed", extra={"error": repr(exc)})
    return reply


async def discard(reply: Reply) -> None:
    """Forget a cached answer the caller rejected (failed validation), so the same request is asked again
    next time instead of replaying the bad answer forever. Replay mode keeps its recordings."""
    if reply.cache_key is None or cfg.GEMINI_MODE != "live":
        return
    try:
        await asyncio.to_thread(_cache_path(reply.cache_key).unlink, missing_ok=True)
    except OSError as exc:
        log.warning("ai cache discard failed", extra={"error": repr(exc)})
    reply.cache_key = None


async def _ask_live(agent: str, model: str, system: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None, timeout_s: float, background: bool,
                    json_mode: bool, allowed_tools: list[str] | None = None) -> Reply:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s * DEADLINE_FACTOR
    last_error = "unavailable"
    for m in model_chain(model):
        wait = 0.0
        for attempt in range(len(RETRY_DELAYS_S)):
            refusal = budget.refusal(m, background)
            if refusal:
                last_error = refusal
                break
            if wait:
                if loop.time() + wait >= deadline:
                    break
                await asyncio.sleep(wait)
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            budget.count(m)
            started = loop.time()
            try:
                reply = await asyncio.wait_for(_call_sdk(m, system, messages, tools, json_mode, allowed_tools),
                                               min(timeout_s, remaining))
            except (asyncio.TimeoutError, TimeoutError):
                _counters["live_failed"] += 1
                _record(agent, m, _ms(loop, started), False, False, "timeout")
                last_error = "timeout"
                budget.cooldown(m, OVERLOAD_COOLDOWN_S)
                break
            except _Overloaded as exc:
                _counters["live_failed"] += 1
                _record(agent, m, _ms(loop, started), False, False, str(exc))
                last_error = str(exc)
                if attempt == 0 and len(RETRY_DELAYS_S) > 1:
                    wait = RETRY_DELAYS_S[1]
                    continue
                budget.cooldown(m, OVERLOAD_COOLDOWN_S)
                break
            except _RateLimited as exc:
                _counters["live_failed"] += 1
                _record(agent, m, _ms(loop, started), False, False, "429")
                last_error = "429"
                if exc.daily:
                    if budget.rotate_key(len(api_keys())):
                        log.warning("gemini key spent for today, switching key",
                                    extra={"model": m, "key_index": budget.key_index})
                        wait = 0.0
                        continue
                    budget.block_until_midnight(m)
                    break
                if exc.retry_after is not None and exc.retry_after > MAX_HINT_WAIT_S:
                    budget.cooldown(m, exc.retry_after)
                    break
                if attempt == len(RETRY_DELAYS_S) - 1:
                    budget.cooldown(m, RPM_COOLDOWN_S)
                    break
                wait = max(RETRY_DELAYS_S[attempt + 1], exc.retry_after or 0.0)
                continue
            except _Retryable as exc:
                _counters["live_failed"] += 1
                _record(agent, m, _ms(loop, started), False, False, str(exc))
                last_error = str(exc)
                if attempt == len(RETRY_DELAYS_S) - 1:
                    raise AIUnavailable(last_error) from None
                wait = RETRY_DELAYS_S[attempt + 1]
                continue
            except _Fatal as exc:
                _counters["live_failed"] += 1
                _record(agent, m, _ms(loop, started), False, False, str(exc)[:200])
                raise AIUnavailable(str(exc)) from None
            _counters["live_ok"] += 1
            _record(agent, m, _ms(loop, started), True, False, None)
            reply.model = m
            return reply
        if loop.time() >= deadline:
            break
    _counters["unavailable"] += 1
    raise AIUnavailable(last_error)


def _ms(loop: asyncio.AbstractEventLoop, started: float) -> int:
    return int((loop.time() - started) * 1000)


def reset_for_tests() -> None:
    """Fresh budget, counters and thinking-support memory (tests only)."""
    global budget
    budget = Budget()
    for k in _counters:
        _counters[k] = 0
    _thinking_unsupported.clear()
