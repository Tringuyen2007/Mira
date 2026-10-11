"""System prompts for every Gemini agent. Each file ends with the shared §6.7 rules."""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    """The text of prompts/<name>.md."""
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
