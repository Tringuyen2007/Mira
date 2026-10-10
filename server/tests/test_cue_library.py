from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.ai import cue_library, eleven, gemini
from server.ai.validators import validate_text
from server.config import cfg, reload_config


def _lib(n: int, seed: int = 0) -> cue_library.CueLibrary:
    entries = {"c": [{"text": f"line {i}", "file": f"c_{i}.mp3"} for i in range(n)]}
    return cue_library.CueLibrary(entries, Path(cfg.DATA_DIR) / "cues" / "mp3", random.Random(seed))


@pytest.mark.parametrize("seed", range(20))
def test_picker_never_repeats_last_two(seed: int) -> None:
    lib = _lib(3, seed)
    played = [lib.pick("c")["text"] for _ in range(60)]  # type: ignore[index]
    for i in range(2, len(played)):
        assert played[i] not in (played[i - 1], played[i - 2])


@pytest.mark.parametrize("seed", range(10))
def test_picker_with_two_variants_alternates(seed: int) -> None:
    lib = _lib(2, seed)
    played = [lib.pick("c")["text"] for _ in range(20)]  # type: ignore[index]
    assert all(a != b for a, b in zip(played, played[1:]))


def test_picker_single_variant_and_unknown_id() -> None:
    lib = _lib(1)
    assert lib.pick("c")["text"] == lib.pick("c")["text"] == "line 0"  # type: ignore[index]
    assert lib.pick("nope") is None


def test_audio_url_only_when_file_exists() -> None:
    mp3 = Path(cfg.DATA_DIR) / "cues" / "mp3"
    mp3.mkdir(parents=True)
    (mp3 / "c_0.mp3").write_bytes(b"x")
    lib = _lib(1)
    assert lib.pick("c") == {"id": "c", "text": "line 0", "audio_url": "/audio/c_0.mp3"}
    assert _lib(2).entries["c"][1]["file"] == "c_1.mp3"


def test_default_library_covers_builtin_ids_and_passes_validation() -> None:
    lib = cue_library.CueLibrary.load()  # DATA_DIR is empty in tests → committed default
    for cue_id in [*cue_library.CUE_IDS, *cue_library.FIXED_CUES]:
        assert lib.entries.get(cue_id), cue_id
    for cue_id, lines in lib.entries.items():
        if cue_id in cue_library.FIXED_CUES:  # hand-written safety lines, exempt from the AI cue limits
            continue
        for line in lines:
            assert validate_text(line["text"], "cue", []) == line["text"], (cue_id, line)
            assert line["file"].startswith(cue_id + "_")
    assert lib.pick("check_in")["text"] == "Are you okay? Give me a thumbs up or raise your hand."  # type: ignore[index]


def test_rule_ids_are_merged(tmp_path: Path) -> None:
    rules = Path(cfg.DATA_DIR) / "rules"
    rules.mkdir(parents=True)
    (rules / "squat.json").write_text(json.dumps({"rules": [{"id": "heel_lift", "cue": "keep heels down"}]}))
    (rules / "bad.json").write_text("{")
    ids = cue_library.all_cue_ids()
    assert ids["heel_lift"] == "keep heels down" and "knees_out" in ids


async def test_generate_text_one_batched_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def fake(messages: list[dict[str, Any]], tools: Any) -> gemini.Reply:
        requests.append(messages[0])
        ids = json.loads(messages[0]["content"].removeprefix("DATA:\n"))
        out = {i: [f"Variant {n} for you." for n in range(1, 4)] for i in ids}
        out["knees_out"] = ["You have a torn ligament.", "Knees out, nice.", "Knees out, nice."]
        out.pop("rest")
        return gemini.Reply(text=json.dumps(out))

    gemini.register_fake("cue_library", fake)
    out = await cue_library.generate_text()
    assert len(requests) == 1
    assert "check_in" not in requests[0]["content"]
    assert out["knees_out"] == [{"text": "Knees out, nice.", "file": "knees_out_1.mp3"}]
    assert out["rest"][0]["text"] == "Rest now and breathe."  # kept from the default
    assert out["check_in"][0]["text"].startswith("Are you okay?")
    assert json.loads((Path(cfg.DATA_DIR) / "cues" / "cues.json").read_text()) == out


async def test_generate_audio_only_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["text"])
        return httpx.Response(200, content=b"mp3")

    monkeypatch.setenv("ELEVEN_API_KEY", "k-not-real")
    monkeypatch.setenv("ELEVEN_VOICE_ID", "voice")
    reload_config()
    monkeypatch.setattr(eleven, "transport", httpx.MockTransport(handler))
    path = cue_library.cues_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"c": [{"text": "Hi.", "file": "c_1.mp3"}, {"text": "Yo.", "file": "c_2.mp3"}]}))
    (cue_library.mp3_dir()).mkdir(parents=True)
    (cue_library.mp3_dir() / "c_1.mp3").write_bytes(b"old")
    result = await cue_library.generate_audio()
    # c_1 already exists; the fixed safety lines are added to every library.
    assert calls == ["Yo.", "Are you okay? Give me a thumbs up or raise your hand.",
                     "Thanks for letting me know you're okay."]
    assert result == {"generated": 3, "failed": 0, "chars": sum(map(len, calls))}
    assert cue_library.pick("c") is not None and cue_library.pick("c")["audio_url"]  # type: ignore[index]
