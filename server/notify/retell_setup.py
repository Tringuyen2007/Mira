"""Create or update the Retell "Mira Safety Caller" agent exactly per TRI.md §8.3 (idempotent by name).

    python -m server.notify.retell_setup            # dry run: print the payloads, touch nothing
    python -m server.notify.retell_setup --apply    # create/update, publish, bind the number, verify

Configuring is free; this never places a call. Needs RETELL_API_KEY and RETELL_FROM_NUMBER in the
environment for --apply. Prints RETELL_AGENT_ID and RETELL_AGENT_VERSION for the server's env.

Versions: Retell edits only an agent's latest *draft*. --apply writes the draft (opening a new draft
from the published version first when needed), publishes it, and binds the number's outbound agent to
that exact version number, so a later dashboard edit can never change what an emergency call says
until it is published and bound again.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from server.config import cfg
from server.logging_setup import mask_phone
from server.notify import templates

LANGUAGE = "en-US"
LLM_MODEL = "gemini-3.8-flash"  # the voice agent's model, hosted and billed by Retell (not our Gemini keys)
BACKUP_LLM_MODEL = "gpt-4.1-mini"  # backup agent: a different provider, so a Gemini outage can't stop both
BEGIN_MESSAGE_DELAY_MS = 1000
VOICE_SPEED = 0.95
MAX_CALL_DURATION_MS = 180_000
END_CALL_AFTER_SILENCE_MS = 20_000
DATA_STORAGE_SETTING = "everything"
TRANSFER_TOOL_NAME = "transfer_to_therapist"
END_CALL_TOOL_NAME = "end_call"
END_CALL_DESCRIPTION = (
    "End the call after a short goodbye once the goal is met: the contact has understood what happened, has said "
    "whether they can check on the patient now (or will call 911), and has no more questions. Also end it if the "
    "contact asks to hang up. Never end the call while they are still asking about the patient."
)
VERSION_TITLE = "Mira Safety Caller (TRI.md section 8.3)"
BACKUP_VERSION_TITLE = "Mira Safety Caller backup (non-Gemini)"
# Calm, warm female ElevenLabs voices, in order of preference; the first one the account offers wins.
PREFERRED_VOICES = ("11labs-Grace", "11labs-Marissa", "11labs-Myra", "11labs-Paola", "11labs-Kate")
VOICE_PLACEHOLDER = "<picked from client.voice.list() at --apply>"
LLM_PLACEHOLDER = "<llm_id>"

POST_CALL_ANALYSIS = [
    {"type": "boolean", "name": "will_check_on_patient",
     "description": "True if the contact said they will check on the patient now (go to her, call her, or call 911)."},
    {"type": "boolean", "name": "contact_reached",
     "description": "True if a person (not voicemail) answered and heard the message."},
    {"type": "string", "name": "notes",
     "description": "One short sentence: what the contact said they will do, or why the call did not succeed."},
]


def llm_payload() -> dict[str, Any]:
    return {
        "model": LLM_MODEL,
        "start_speaker": "agent",  # AI speaks first
        "begin_message": templates.OPENING_MESSAGE,  # custom message, spoken word for word
        "general_prompt": templates.AGENT_PROMPT,
        "default_dynamic_variables": {"current_status": templates.DEFAULT_CURRENT_STATUS,
                                      "limits": templates.LIMITS_SENTENCE},
        "general_tools": [{
            "type": "end_call",
            "name": END_CALL_TOOL_NAME,
            "description": END_CALL_DESCRIPTION,
        }, {
            "type": "transfer_call",
            "name": TRANSFER_TOOL_NAME,
            "description": "Warm transfer to the patient's therapist when the contact asks to talk to the therapist.",
            "transfer_destination": {"type": "predefined", "number": "{{therapist_phone}}"},
            "transfer_option": {"type": "warm_transfer",
                                "private_handoff_option": {"type": "static_message",
                                                           "message": templates.WHISPER_MESSAGE}},
        }],
    }


def backup_llm_payload() -> dict[str, Any]:
    """The fallback agent's LLM: a fixed short opening, a minimal prompt and only the end_call tool."""
    return {
        "model": BACKUP_LLM_MODEL,
        "start_speaker": "agent",
        "begin_message": templates.BACKUP_OPENING_MESSAGE,
        "general_prompt": templates.BACKUP_AGENT_PROMPT,
        "default_dynamic_variables": {"current_status": templates.DEFAULT_CURRENT_STATUS},
        "general_tools": [{"type": "end_call", "name": END_CALL_TOOL_NAME, "description": END_CALL_DESCRIPTION}],
    }


def backup_agent_payload(llm_id: str, voice_id: str) -> dict[str, Any]:
    return {**agent_payload(llm_id, voice_id), "agent_name": templates.BACKUP_AGENT_NAME}


def agent_payload(llm_id: str, voice_id: str) -> dict[str, Any]:
    return {
        "agent_name": templates.AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "language": LANGUAGE,
        "handbook_config": {"speech_normalization": True},
        "voice_speed": VOICE_SPEED,
        "begin_message_delay_ms": BEGIN_MESSAGE_DELAY_MS,
        "max_call_duration_ms": MAX_CALL_DURATION_MS,
        "end_call_after_silence_ms": END_CALL_AFTER_SILENCE_MS,
        "ambient_sound": None,
        "voicemail_option": {"action": {"type": "static_text", "text": templates.VOICEMAIL_MESSAGE}},
        "post_call_analysis_data": POST_CALL_ANALYSIS,
        "data_storage_setting": DATA_STORAGE_SETTING,
    }


def phone_payload(agent_id: str, version: int | str) -> dict[str, Any]:
    """Pin the number's outbound agent to the exact published version (not "latest", which is the draft)."""
    return {"outbound_agents": [{"agent_id": agent_id, "weight": 1.0, "agent_version": version}]}


def pick_voice(voices: list[Any]) -> str:
    """A calm female ElevenLabs voice: the first preferred one available, else any female ElevenLabs voice."""
    eleven = [v for v in voices if getattr(v, "provider", None) == "elevenlabs" and getattr(v, "gender", None) == "female"]
    by_id = {v.voice_id: v for v in eleven}
    for voice_id in PREFERRED_VOICES:
        if voice_id in by_id:
            return voice_id
    if not eleven:
        raise RuntimeError("no female ElevenLabs voice available in this Retell account")
    return str(eleven[0].voice_id)


def _find_agent_id(client: Any, name: str) -> str | None:
    key: str | None = None
    while True:
        page = client.agent.list(limit=100, **({"pagination_key": key} if key else {}))
        for item in page.items:
            if item.agent_name == name:
                return str(item.agent_id)
        if not page.has_more or not page.pagination_key:
            return None
        key = page.pagination_key


def _upsert(client: Any, name: str, llm: dict[str, Any], agent_for: Callable[[str], dict[str, Any]],
            title: str) -> tuple[str, str, int, str]:
    """Create or update one LLM + agent draft by name and publish it. Returns (action, agent_id, version, llm_id)."""
    agent_id = _find_agent_id(client, name)
    if agent_id is None:
        llm_id = str(client.llm.create(**llm).llm_id)
        agent_id = str(client.agent.create(**agent_for(llm_id)).agent_id)
        action = "created"
    else:
        agent = client.agent.retrieve(agent_id)
        if getattr(agent, "is_published", False):  # published versions are read-only: open a new draft
            client.agent.create_version(agent_id, base_version=int(agent.version))
            agent = client.agent.retrieve(agent_id)
        llm_id = getattr(agent.response_engine, "llm_id", None)
        if llm_id:
            client.llm.update(llm_id, **llm)
        else:
            llm_id = str(client.llm.create(**llm).llm_id)
        client.agent.update(agent_id, **agent_for(llm_id))
        action = "updated"
    version = int(client.agent.retrieve(agent_id).version)
    client.agent.publish(agent_id, version=version, version_title=title)
    return action, agent_id, version, str(llm_id)


def apply(client: Any, from_number: str) -> dict[str, Any]:
    """Create or update both agents (main + backup), publish them, and bind the main version to the number.
    The backup agent is used per call (override_agent_id), so the number only names the main one."""
    voice_id = pick_voice(list(client.voice.list()))
    action, agent_id, version, llm_id = _upsert(client, templates.AGENT_NAME, llm_payload(),
                                                lambda llm: agent_payload(llm, voice_id), VERSION_TITLE)
    client.phone_number.update(from_number, **phone_payload(agent_id, version))
    _, backup_id, backup_version, backup_llm_id = _upsert(
        client, templates.BACKUP_AGENT_NAME, backup_llm_payload(),
        lambda llm: backup_agent_payload(llm, voice_id), BACKUP_VERSION_TITLE)
    return {"action": action, "agent_id": agent_id, "agent_version": version, "llm_id": llm_id,
            "voice_id": voice_id, "backup_agent_id": backup_id, "backup_agent_version": backup_version,
            "backup_llm_id": backup_llm_id}


def _get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
    return obj


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def verify(client: Any, ids: dict[str, Any], from_number: str) -> list[tuple[str, Any, Any, bool]]:
    """Retrieve the published agent version, its LLM version and the number; compare every §8.3 field.

    Returns (field, want, got, ok)."""
    agent = client.agent.retrieve(ids["agent_id"], version=ids["agent_version"])
    llm_version = _get(agent, "response_engine.version")
    llm = client.llm.retrieve(ids["llm_id"], **({"version": int(llm_version)} if llm_version is not None else {}))
    number = client.phone_number.retrieve(from_number)
    want_llm = llm_payload()
    want_agent = agent_payload(ids["llm_id"], ids["voice_id"])
    rows: list[tuple[str, Any, Any, bool]] = []

    def check(name: str, want: Any, got: Any) -> None:
        rows.append((name, want, got, want == got))

    for key in ("model", "start_speaker", "begin_message", "general_prompt", "default_dynamic_variables"):
        check(f"llm.{key}", want_llm[key], _plain(getattr(llm, key, None)))
    tools = _plain(getattr(llm, "general_tools", None)) or []
    transfer = next((t for t in tools if t.get("name") == TRANSFER_TOOL_NAME), {})
    end_call = next((t for t in tools if t.get("name") == END_CALL_TOOL_NAME), {})
    check("llm.end_call", {"type": "end_call", "description": END_CALL_DESCRIPTION},
          {k: end_call.get(k) for k in ("type", "description")})
    want_transfer = next(t for t in want_llm["general_tools"] if t["name"] == TRANSFER_TOOL_NAME)
    check("llm.transfer.destination", want_transfer["transfer_destination"],
          {k: v for k, v in (transfer.get("transfer_destination") or {}).items() if k in ("type", "number")})
    check("llm.transfer.option.type", "warm_transfer", _get(transfer, "transfer_option.type"))
    check("llm.transfer.whisper", templates.WHISPER_MESSAGE, _get(transfer, "transfer_option.private_handoff_option.message"))
    check("agent.version", ids["agent_version"], getattr(agent, "version", None))
    check("agent.is_published", True, getattr(agent, "is_published", None))
    check("agent.agent_name", want_agent["agent_name"], agent.agent_name)
    check("agent.response_engine.llm_id", ids["llm_id"], _get(agent, "response_engine.llm_id"))
    check("agent.voice_id", ids["voice_id"], agent.voice_id)
    for key in ("language", "voice_speed", "begin_message_delay_ms", "max_call_duration_ms",
                "end_call_after_silence_ms", "ambient_sound", "data_storage_setting"):
        check(f"agent.{key}", want_agent[key], getattr(agent, key, None))
    check("agent.handbook_config.speech_normalization", True, _get(agent, "handbook_config.speech_normalization"))
    check("agent.voicemail_option.action", want_agent["voicemail_option"]["action"],
          {k: v for k, v in (_plain(_get(agent, "voicemail_option.action")) or {}).items() if k in ("type", "text")})
    analysis = _plain(getattr(agent, "post_call_analysis_data", None)) or []
    check("agent.post_call_analysis_data", [(f["name"], f["type"]) for f in POST_CALL_ANALYSIS],
          [(f.get("name"), f.get("type")) for f in analysis if f.get("type") != "system-presets"])
    outbound = _plain(getattr(number, "outbound_agents", None)) or []
    check("phone_number.outbound_agents", phone_payload(ids["agent_id"], ids["agent_version"])["outbound_agents"],
          [{"agent_id": a.get("agent_id"), "weight": a.get("weight"), "agent_version": a.get("agent_version")}
           for a in outbound])
    if ids.get("backup_agent_id"):
        backup = client.agent.retrieve(ids["backup_agent_id"], version=ids["backup_agent_version"])
        backup_llm_version = _get(backup, "response_engine.version")
        backup_llm = client.llm.retrieve(ids["backup_llm_id"], **({"version": int(backup_llm_version)}
                                                                  if backup_llm_version is not None else {}))
        want_backup = backup_llm_payload()
        for key in ("model", "start_speaker", "begin_message", "general_prompt"):
            check(f"backup.llm.{key}", want_backup[key], _plain(getattr(backup_llm, key, None)))
        check("backup.agent.is_published", True, getattr(backup, "is_published", None))
        check("backup.agent.agent_name", templates.BACKUP_AGENT_NAME, backup.agent_name)
        check("backup.agent.voicemail_option.action", want_agent["voicemail_option"]["action"],
              {k: v for k, v in (_plain(_get(backup, "voicemail_option.action")) or {}).items()
               if k in ("type", "text")})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="create/update in Retell (otherwise dry run)")
    args = parser.parse_args(argv)
    from_number = cfg.RETELL_FROM_NUMBER or ""
    if not args.apply:
        print(json.dumps({"llm": llm_payload(), "agent": agent_payload(LLM_PLACEHOLDER, VOICE_PLACEHOLDER),
                          "backup_llm": backup_llm_payload(),
                          "phone_number": {"number": mask_phone(from_number) or "<RETELL_FROM_NUMBER>",
                                           **phone_payload("<agent_id>", "<published version>")}}, indent=2))
        print("\ndry run: nothing was sent. Re-run with --apply to configure Retell.", file=sys.stderr)
        return 0
    if not cfg.RETELL_API_KEY or not from_number:
        print("RETELL_API_KEY and RETELL_FROM_NUMBER must be set for --apply", file=sys.stderr)
        return 2
    from retell import Retell

    client = Retell(api_key=cfg.RETELL_API_KEY)
    ids = apply(client, from_number)
    rows = verify(client, ids, from_number)
    print(json.dumps(ids, indent=2))
    for name, want, got, ok in rows:
        print(f"{'OK ' if ok else 'BAD'} {name}" + ("" if ok else f"\n    want: {want!r}\n    got:  {got!r}"))
    bad = [r for r in rows if not r[3]]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} fields verified. Set RETELL_AGENT_ID={ids['agent_id']} "
          f"RETELL_AGENT_VERSION={ids['agent_version']} RETELL_BACKUP_AGENT_ID={ids['backup_agent_id']} "
          f"RETELL_BACKUP_AGENT_VERSION={ids['backup_agent_version']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
