from __future__ import annotations

import pytest
from pydantic import BaseModel

from server.ai import validators as v


class Summary(BaseModel):
    spoken: str
    therapist_note: str


# ---- numbers --------------------------------------------------------------------------------------

def test_numbers_from_data_pass() -> None:
    assert v.check_numbers("You did 12 squats at 87.4 degrees.", [12, 87.4])


def test_numbers_rounded_to_zero_or_one_decimal_pass() -> None:
    assert v.check_numbers("Knee bend reached 87 degrees", [87.43])
    assert v.check_numbers("Knee bend reached 87.4 degrees", [87.43])
    assert v.check_numbers("Load was 56.5 percent", [56.52])


def test_invented_number_fails() -> None:
    assert not v.check_numbers("You did 14 squats.", [12])
    assert not v.check_numbers("Knee bend reached 95 degrees", [87.4])


def test_small_counting_integers_allowed() -> None:
    assert v.check_numbers("Great job on set 2, 3 more to go and 10 out of 10!", [])


def test_small_decimal_not_a_counting_integer() -> None:
    assert not v.check_numbers("Pain went up 2.5 points", [])


def test_number_words_are_checked() -> None:
    assert not v.check_numbers("You did fifteen squats", [12])
    assert v.check_numbers("You did twelve squats", [12])
    assert not v.check_numbers("You did twenty-five squats", [12])


def test_thousands_separator() -> None:
    assert v.check_numbers("1,200 steps", [1200])
    assert not v.check_numbers("1,300 steps", [1200])


# ---- length ----------------------------------------------------------------------------------------

def test_cue_word_limit() -> None:
    assert v.check_length("Push your knees out over your toes.", "cue")
    assert not v.check_length("Push your knees out a little bit more over your toes now.", "cue")


def test_spoken_sentence_limit_ignores_decimals_and_titles() -> None:
    assert v.check_length("Nice set. You reached 87.4 degrees. Dr. Okafor will see it.", "spoken")
    assert not v.check_length("One. Two. Three. Four.", "spoken")


def test_situation_sentence_limit() -> None:
    assert v.check_length("A. B. C. D.", "situation")
    assert not v.check_length("A. B. C. D. E.", "situation")


def test_empty_text_fails_length() -> None:
    assert not v.check_length("   ", "spoken")


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        v.check_length("hi", "poem")


# ---- banned content --------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "You have arthritis in that knee.",
    "That could be a torn ligament.",
    "Watch for an infection around the scar.",
    "Take some ibuprofen before the next set.",
    "Your medication may help.",
    "You should stop your treatment.",
    "You will fully recover in two weeks.",
    "Your plan has changed.",
])
def test_banned_content_rejected(text: str) -> None:
    assert not v.check_banned(text)


@pytest.mark.parametrize("text", [
    "Great work on your squats today!",
    "Your knees stayed nice and steady.",
    "Tell Dr. Okafor about any pain.",
])
def test_clean_content_passes(text: str) -> None:
    assert v.check_banned(text)


# ---- validate_text ---------------------------------------------------------------------------------

def test_validate_text_returns_clean_text() -> None:
    assert v.validate_text("  Nice set, Maya! 12 reps done.  ", "spoken", [12]) == "Nice set, Maya! 12 reps done."


def test_validate_text_none_on_any_failure() -> None:
    assert v.validate_text("You did 14 reps.", "spoken", [12]) is None
    assert v.validate_text("You have a torn tendon.", "spoken", []) is None
    assert v.validate_text("A. B. C. D.", "spoken", []) is None
    assert v.validate_text(None, "spoken", []) is None


# ---- JSON shape -----------------------------------------------------------------------------------

def test_parse_json_accepts_fenced_json() -> None:
    out = v.parse_json('```json\n{"spoken": "Hi", "therapist_note": "ok"}\n```', Summary)
    assert out == Summary(spoken="Hi", therapist_note="ok")


def test_parse_json_bad_shape_returns_none() -> None:
    assert v.parse_json('{"spoken": "Hi"}', Summary) is None
    assert v.parse_json("not json", Summary) is None


async def test_validate_json_retries_once() -> None:
    calls: list[str] = []

    async def retry(error: str) -> str | None:
        calls.append(error)
        return '{"spoken": "Hi", "therapist_note": "ok"}'

    out = await v.validate_json('{"spoken": 1}', Summary, retry)
    assert out == Summary(spoken="Hi", therapist_note="ok")
    assert len(calls) == 1


async def test_validate_json_gives_up_after_one_retry() -> None:
    calls: list[str] = []

    async def retry(error: str) -> str | None:
        calls.append(error)
        return "still not json"

    assert await v.validate_json("nope", Summary, retry) is None
    assert len(calls) == 1


def test_label_is_added_by_code() -> None:
    assert v.AI_DRAFT_LABEL == "AI draft — review before use"
    assert v.label_note("Good set.").startswith(v.AI_DRAFT_LABEL)
    assert v.label_note(v.label_note("Good set.")).count(v.AI_DRAFT_LABEL) == 1


def test_numbers_rounded_half_up_are_allowed() -> None:
    assert v.check_numbers("You reached 13 reps.", [12.5])  # people round 12.5 up; Python's round() gives 12
    assert v.check_numbers("72.3 degrees", [72.25])
    assert v.check_numbers("12 reps", [12.5])
    assert not v.check_numbers("14 reps", [12.5])


def test_scale_number_words_must_come_from_data() -> None:
    assert not v.check_numbers("You did a hundred reps!", [12])
    assert v.check_numbers("A dozen squats today.", [12])


@pytest.mark.parametrize("text", [
    "You might have a meniscus injury.", "It sounds like a strain.", "You may need surgery.",
    "Skip your exercises today.", "Stop doing your exercises.", "That is inflammation.",
])
def test_banned_hedged_diagnoses_and_stopping(text: str) -> None:
    assert not v.check_banned(text)


@pytest.mark.parametrize("text", ["Stop and rest for a moment.", "You have 2 sets left.", "Great depth, Maya!"])
def test_harmless_lines_still_pass(text: str) -> None:
    assert v.check_banned(text)
