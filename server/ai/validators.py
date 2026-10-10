"""Output guardrails applied to every AI text (TRI §6.7).

A failed check means: discard the AI text and use the caller's template/fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from server.logging_setup import get_logger

log = get_logger("ai.validators")

AI_DRAFT_LABEL = "AI draft — review before use"

# Counting integers up to this value may appear in text even if they are not in the data.
SMALL_INT_MAX = 10

# kind → (max words, max sentences); None = unlimited.
LENGTH_LIMITS: dict[str, tuple[int | None, int | None]] = {
    "cue": (8, None),
    "greeting": (30, 3),
    "spoken": (None, 3),
    "situation": (None, 4),
    "note": (None, 5),
}

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD_RE = re.compile(
    r"\b(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(k for k in _UNITS if _UNITS[k] < 10) + r"))?\b"
    r"|\b(" + "|".join(_UNITS) + r")\b",
    re.IGNORECASE,
)

_COUNT_WORDS = "|".join(["\\d+", *_UNITS, "another", "more", "done", "completed", "finished", "got",
                         "made", "reached", "left", "worked", "shown", "kept", "already", "just"])
_BANNED = [
    # diagnosis
    rf"\byou have (?!(?:a |an )?(?:{_COUNT_WORDS})\b)",
    r"\bdiagnos\w*", r"\btorn\b", r"\btears?\b(?= in| of)", r"\brupture\w*", r"\binfect\w*", r"\bfractur\w*",
    r"\bdislocat\w*", r"\bblood clot", r"\bdvt\b", r"\barthritis\b", r"\btendinitis\b", r"\bsprain\w*",
    # medication
    r"\bmedication\w*", r"\bmedicine\w*", r"\bibuprofen\b", r"\bacetaminophen\b", r"\btylenol\b", r"\badvil\b",
    r"\baspirin\b", r"\bopioid\w*", r"\bpainkiller\w*", r"\bpain killer\w*", r"\bpills?\b", r"\bdos(?:e|age)s?\b",
    # stopping treatment
    r"\bstop (?:your |the )?(?:treatment|therapy|physical therapy|exercises|taking)\b", r"\bquit (?:your )?therapy\b",
    # recovery promises
    r"\bfully recover\w*", r"\bfull recovery\b", r"\bwill recover\b", r"\bguarantee\w*", r"\bcured?\b",
    r"\bheal(?:ed)? completely\b", r"\bback to normal\b",
    # plan changes (only the therapist changes the plan)
    r"\bplan (?:has |was )?changed\b", r"\bchanged your plan\b", r"\bupdated your plan\b", r"\bnew plan\b",
]
_BANNED_RE = re.compile("|".join(f"(?:{p})" for p in _BANNED), re.IGNORECASE)
_TITLES_RE = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|St|vs|e\.g|i\.e)\.", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

M = TypeVar("M", bound=BaseModel)


def extract_numbers(text: str) -> list[float]:
    """Every number in `text`, digits and English number words ("twelve", "twenty-five")."""
    found = [float(m.replace(",", "")) for m in _NUMBER_RE.findall(text)]
    for m in _WORD_RE.finditer(text):
        tens, unit, single = m.group(1), m.group(2), m.group(3)
        if single:
            found.append(float(_UNITS[single.lower()]))
        else:
            found.append(float(_TENS[tens.lower()] + (_UNITS[unit.lower()] if unit else 0)))
    return found


def _allowed(n: float, allowed: list[float]) -> bool:
    if n.is_integer() and 0 <= n <= SMALL_INT_MAX:
        return True
    return any(abs(n - a) < 1e-9 or abs(n - round(a)) < 1e-9 or abs(n - round(a, 1)) < 1e-9 for a in allowed)


def check_numbers(text: str, allowed: Iterable[float]) -> bool:
    """True when every number in `text` comes from `allowed` (rounded to 0–1 decimals) or is a count ≤ 10."""
    values = [abs(float(a)) for a in allowed if isinstance(a, (int, float)) and not isinstance(a, bool)]
    return all(_allowed(n, values) for n in extract_numbers(text))


def count_sentences(text: str) -> int:
    stripped = _TITLES_RE.sub(lambda m: m.group(0)[:-1], text.strip())
    parts = [p for p in _SENTENCE_END_RE.split(stripped) if p.strip()]
    return len(parts)


def check_length(text: str, kind: str) -> bool:
    if kind not in LENGTH_LIMITS:
        raise ValueError(f"unknown text kind {kind!r}")
    if not text.strip():
        return False
    max_words, max_sentences = LENGTH_LIMITS[kind]
    if max_words is not None and len(text.split()) > max_words:
        return False
    return max_sentences is None or count_sentences(text) <= max_sentences


def check_banned(text: str) -> bool:
    """True when the text contains no diagnosis, medication, stop-treatment, promise or plan-change wording."""
    return _BANNED_RE.search(text) is None


def validate_text(text: str | None, kind: str, allowed_numbers: Iterable[float]) -> str | None:
    """The stripped text if it passes every check, else None (caller uses its fallback)."""
    if not isinstance(text, str):
        return None
    clean = " ".join(text.split())
    for name, ok in (("length", lambda: check_length(clean, kind)), ("banned", lambda: check_banned(clean)),
                     ("numbers", lambda: check_numbers(clean, allowed_numbers))):
        if not ok():
            log.info("ai text rejected", extra={"check": name, "kind": kind})
            return None
    return clean


def parse_json(raw: str | None, model: type[M]) -> M | None:
    """Parse `raw` (optionally inside a ``` fence) into `model`; None if it is not valid JSON of that shape."""
    if not isinstance(raw, str):
        return None
    try:
        return model.model_validate(json.loads(_FENCE_RE.sub("", raw)))
    except (ValueError, ValidationError):
        return None


def _shape_error(raw: str | None, model: type[BaseModel]) -> str:
    try:
        model.model_validate(json.loads(_FENCE_RE.sub("", raw or "")))
    except json.JSONDecodeError as exc:
        return f"not valid JSON: {exc.msg}"
    except ValidationError as exc:
        return "wrong JSON shape: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return "unknown error"


async def validate_json(raw: str | None, model: type[M],
                        retry: Callable[[str], Awaitable[str | None]] | None = None) -> M | None:
    """Parse `raw` into `model`. On failure call `retry(error)` once for a new raw answer, then give up."""
    parsed = parse_json(raw, model)
    if parsed is not None or retry is None:
        return parsed
    error = _shape_error(raw, model)
    log.info("ai json rejected, retrying once", extra={"error": error[:200]})
    return parse_json(await retry(error), model)


def label_note(text: str) -> str:
    """Prefix a therapist note with the AI draft label (added by code, never by the model)."""
    if text.startswith(AI_DRAFT_LABEL):
        return text
    return f"{AI_DRAFT_LABEL}: {text}"
