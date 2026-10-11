"""The Retell agent configuration equals TRI.md §8.3 word for word. No network: a fake client stands in."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from server.config import reload_config
from server.notify.retell_setup import agent_payload, apply, llm_payload, main, pick_voice

# Copied verbatim from TRI.md §8.3 (the single source for the agent).
OPENING = ("Hello {{contact_name}}, this is Mira, the home exercise assistant for {{patient_first_name}}. "
           "This is an urgent safety call. {{situation}}")
VOICEMAIL = ("Hello {{contact_name}}, this is Mira, the home exercise assistant for {{patient_first_name}}. "
             "Urgent: {{situation_short}} Please call {{patient_first_name}} at {{patient_phone}}. "
             "If you can't reach her, call 911.")
WHISPER = "Mira safety alert for {{patient_first_name}}: {{situation_short}}"
PROMPT = """You are Mira's safety caller. You are calling {{contact_name}}, the emergency contact for {{patient_first_name}}.
You have already said the opening message, which described what happened.
WHAT HAPPENED: {{situation}}
CURRENT STATUS: {{current_status}}
CONTEXT: {{context}}
LIMITS: {{limits}}
During the call you may receive messages starting with "UPDATE". They are the newest facts and replace older ones.
Tell the caller promptly and plainly, starting with "Update:".
Goal: make sure they understood what happened, ask whether they can check on {{patient_first_name}} right now,
give her phone number ({{patient_phone}}) if asked, and offer to connect them with her therapist {{therapist_name}}.
Rules:
- Only use the facts above and UPDATE messages. If you don't know something, say Mira can't tell.
- Never guess, diagnose or give medical advice.
- If they cannot reach her or are worried, tell them to call 911. You cannot call 911 yourself.
- Stay calm, use short sentences, and keep the call under 2 minutes."""
LIMITS = ("Mira only sees body position and movement through its cameras. It can't see injuries, bleeding, "
          "or whether she is conscious, and it never records video.")


def test_llm_payload_matches_section_8_3() -> None:
    llm = llm_payload()
    assert llm["model"] == "gemini-3.8-flash"
    assert llm["start_speaker"] == "agent"
    assert llm["begin_message"] == OPENING
    assert llm["general_prompt"] == PROMPT
    assert llm["default_dynamic_variables"] == {"current_status": "No newer information yet.", "limits": LIMITS}
    end_call, tool = llm["general_tools"]
    assert end_call["type"] == "end_call" and end_call["name"] == "end_call" and end_call["description"]
    assert tool["type"] == "transfer_call"
    assert tool["transfer_destination"] == {"type": "predefined", "number": "{{therapist_phone}}"}
    assert tool["transfer_option"] == {"type": "warm_transfer",
                                       "private_handoff_option": {"type": "static_message", "message": WHISPER}}


def test_agent_payload_matches_section_8_3() -> None:
    agent = agent_payload("llm_x", "11labs-Grace")
    assert agent["agent_name"] == "Mira Safety Caller"
    assert agent["response_engine"] == {"type": "retell-llm", "llm_id": "llm_x"}
    assert agent["language"] == "en-US"
    assert agent["handbook_config"] == {"speech_normalization": True}
    assert agent["voice_speed"] == 0.95
    assert agent["begin_message_delay_ms"] == 1000
    assert agent["max_call_duration_ms"] == 180_000
    assert agent["end_call_after_silence_ms"] == 20_000
    assert agent["ambient_sound"] is None
    assert agent["voicemail_option"] == {"action": {"type": "static_text", "text": VOICEMAIL}}
    assert [(f["name"], f["type"]) for f in agent["post_call_analysis_data"]] == [
        ("will_check_on_patient", "boolean"), ("contact_reached", "boolean"), ("notes", "string")]
    assert agent["data_storage_setting"] == "everything"


def voice(voice_id: str, provider: str = "elevenlabs", gender: str = "female") -> Any:
    return SimpleNamespace(voice_id=voice_id, provider=provider, gender=gender)


def test_pick_voice_prefers_a_calm_female_elevenlabs_voice() -> None:
    voices = [voice("openai-Alloy", "openai"), voice("11labs-Adrian", gender="male"), voice("11labs-Zoe"),
              voice("11labs-Marissa")]
    assert pick_voice(voices) == "11labs-Marissa"
    assert pick_voice([voice("11labs-Zoe")]) == "11labs-Zoe"
    with pytest.raises(RuntimeError):
        pick_voice([voice("cartesia-x", "cartesia")])


BACKUP_CREATE = ["llm.create", "agent.create", "agent.publish"]  # the backup agent (not found by name → created)


class FakeRetell:
    """Mimics Retell versioning: edits go to the latest draft; a published latest needs create_version first."""

    def __init__(self, existing: bool, published: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.version, self.published = 0, published
        items = [SimpleNamespace(agent_id="agent_old", agent_name="Mira Safety Caller")] if existing else []
        self.voice = SimpleNamespace(list=lambda: [voice("11labs-Grace")])
        self.agent = SimpleNamespace(
            list=lambda **kw: SimpleNamespace(items=items, has_more=False, pagination_key=None),
            retrieve=lambda agent_id, **kw: SimpleNamespace(version=self.version, is_published=self.published,
                                                             response_engine=SimpleNamespace(llm_id="llm_old")),
            create=lambda **kw: self._rec("agent.create", kw, SimpleNamespace(agent_id="agent_new")),
            update=self._agent_update,
            create_version=self._create_version,
            publish=self._publish)
        self.llm = SimpleNamespace(
            create=lambda **kw: self._rec("llm.create", kw, SimpleNamespace(llm_id="llm_new")),
            update=lambda llm_id, **kw: self._rec("llm.update", (llm_id, kw), None))
        self.phone_number = SimpleNamespace(update=lambda number, **kw: self._rec("phone.update", (number, kw), None))
        self.call = SimpleNamespace(create_phone_call=lambda **kw: pytest.fail("setup must never place a call"))

    def _rec(self, name: str, args: Any, result: Any) -> Any:
        self.calls.append((name, args))
        return result

    def _agent_update(self, agent_id: str, **kw: Any) -> None:
        assert not self.published, "a published version is read-only"
        self._rec("agent.update", (agent_id, kw), None)

    def _create_version(self, agent_id: str, *, base_version: int) -> None:
        assert base_version == self.version
        self.version, self.published = self.version + 1, False
        self._rec("agent.create_version", base_version, None)

    def _publish(self, agent_id: str, *, version: int, **kw: Any) -> None:
        assert version == self.version
        self.published = True
        self._rec("agent.publish", version, None)


def test_apply_creates_publishes_and_pins_the_outbound_number() -> None:
    client = FakeRetell(existing=False)
    ids = apply(client, "+15550100999")
    assert ids == {"action": "created", "agent_id": "agent_new", "agent_version": 0, "llm_id": "llm_new",
                   "voice_id": "11labs-Grace", "backup_agent_id": "agent_new", "backup_agent_version": 0,
                   "backup_llm_id": "llm_new"}
    assert [c[0] for c in client.calls] == ["llm.create", "agent.create", "agent.publish", "phone.update",
                                            *BACKUP_CREATE]
    assert client.calls[3][1] == ("+15550100999", {"outbound_agents": [{"agent_id": "agent_new", "weight": 1.0,
                                                                         "agent_version": 0}]})


def test_apply_updates_a_draft_in_place_when_the_agent_exists() -> None:
    client = FakeRetell(existing=True)
    ids = apply(client, "+15550100999")
    assert ids["action"] == "updated" and ids["agent_id"] == "agent_old" and ids["llm_id"] == "llm_old"
    assert [c[0] for c in client.calls] == ["llm.update", "agent.update", "agent.publish", "phone.update",
                                            *BACKUP_CREATE]
    assert client.calls[1][1][1]["response_engine"]["llm_id"] == "llm_old"


def test_apply_opens_a_new_draft_when_the_latest_version_is_published() -> None:
    client = FakeRetell(existing=True, published=True)
    ids = apply(client, "+15550100999")
    assert ids["agent_version"] == 1 and client.published
    assert [c[0] for c in client.calls] == ["agent.create_version", "llm.update", "agent.update", "agent.publish",
                                            "phone.update", *BACKUP_CREATE]
    phone = next(args for name, args in client.calls if name == "phone.update")
    assert phone[1]["outbound_agents"][0]["agent_version"] == 1


def test_apply_creates_a_non_gemini_backup_agent_and_never_binds_it_to_the_number() -> None:
    client = FakeRetell(existing=True)
    apply(client, "+15550100999")
    backup_llm = [args for name, args in client.calls if name == "llm.create"][-1]
    backup_agent = [args for name, args in client.calls if name == "agent.create"][-1]
    assert backup_llm["model"] == "gpt-4.1-mini" and not backup_llm["model"].startswith("gemini")
    assert backup_llm["begin_message"].startswith("Hello {{contact_name}}, this is Mira")
    assert "{{situation_short}} Please check on {{patient_first_name}} right away." in backup_llm["begin_message"]
    assert [t["type"] for t in backup_llm["general_tools"]] == ["end_call"]
    assert backup_agent["agent_name"] == "Mira Safety Caller (backup)"
    assert sum(1 for name, _ in client.calls if name == "phone.update") == 1


def test_dry_run_prints_the_payload_and_masks_the_number(capsys: pytest.CaptureFixture[str],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETELL_FROM_NUMBER", "+15550100999")
    reload_config()
    assert main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["llm"]["begin_message"] == OPENING and out["agent"]["voicemail_option"]["action"]["text"] == VOICEMAIL
    assert out["phone_number"]["number"] == "***0999"
