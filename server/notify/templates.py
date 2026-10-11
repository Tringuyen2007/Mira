"""Every message the safety path speaks, shows or sends. Fixed text, no AI (§7.2, §8.3, §8.6).

The Retell texts (opening, voicemail, whisper, prompt) are copied word for word from TRI.md §8.3,
the single source for the agent configuration; `server/notify/retell_setup.py` pushes them to Retell.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# ---- Retell agent (§8.3) ------------------------------------------------------------------------

AGENT_NAME = "Mira Safety Caller"

OPENING_MESSAGE = (
    "Hello {{contact_name}}, this is Mira, the home exercise assistant for {{patient_first_name}}. "
    "This is an urgent safety call. {{situation}}"
)

VOICEMAIL_MESSAGE = (
    "Hello {{contact_name}}, this is Mira, the home exercise assistant for {{patient_first_name}}. "
    "Urgent: {{situation_short}} Please call {{patient_first_name}} at {{patient_phone}}. "
    "If you can't reach her, call 911."
)

WHISPER_MESSAGE = "Mira safety alert for {{patient_first_name}}: {{situation_short}}"

AGENT_PROMPT = """\
You are Mira's safety caller. You are calling {{contact_name}}, the emergency contact for {{patient_first_name}}.
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

# Backup agent: a different model provider, used when the main (Gemini) call can't be placed or ends with a
# model/platform error. Short fixed message, same variables.
BACKUP_AGENT_NAME = "Mira Safety Caller (backup)"

BACKUP_OPENING_MESSAGE = (
    "Hello {{contact_name}}, this is Mira, the home exercise assistant for {{patient_first_name}}. "
    "This is an urgent safety call. {{situation_short}} Please check on {{patient_first_name}} right away. "
    "If you can't reach her, call 911."
)

BACKUP_AGENT_PROMPT = """\
You are Mira's backup safety caller. You are calling {{contact_name}}, the emergency contact for {{patient_first_name}}.
You have already said: {{situation_short}}
CURRENT STATUS: {{current_status}}
During the call you may receive messages starting with "UPDATE". They are the newest facts; say them, starting with
"Update:".
Rules:
- Only use the facts above and UPDATE messages. If you don't know something, say Mira can't tell.
- Never guess, diagnose or give medical advice. If they are worried or can't reach her, tell them to call 911.
- Ask whether they can check on {{patient_first_name}} right now; give her phone number ({{patient_phone}}) if asked.
- Once they have said what they will do, say a short goodbye and end the call."""

DEFAULT_CURRENT_STATUS = "No newer information yet."

# §8.6.2 "what Mira can't know", verbatim for the persona's pronoun ("she").
LIMITS_SENTENCE = (
    "Mira only sees body position and movement through its cameras. It can't see injuries, bleeding, "
    "or whether she is conscious, and it never records video."
)

# The variable names the agent texts use (§8.6.3); call_variables must provide every one.
CALL_VARIABLES = ("contact_name", "patient_first_name", "patient_phone", "situation", "situation_short",
                  "current_status", "context", "limits", "therapist_name", "therapist_phone")

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(template: str, variables: Mapping[str, str]) -> str:
    """Substitute {{name}} the way Retell does. Unknown names are left as-is (and caught by `assert_rendered`)."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), template)


def assert_rendered(text: str) -> str:
    if "{{" in text or "}}" in text:
        raise ValueError(f"unrendered variable in: {text!r}")
    return text


def render_opening(variables: Mapping[str, str]) -> dict[str, str]:
    """Dry render of what the contact would hear: opening, voicemail, whisper and the backup agent's opening.
    Raises if any {{…}} remains."""
    return {name: assert_rendered(render(text, variables)) for name, text in
            (("opening", OPENING_MESSAGE), ("voicemail", VOICEMAIL_MESSAGE), ("whisper", WHISPER_MESSAGE),
             ("backup_opening", BACKUP_OPENING_MESSAGE))}


# ---- Patient-facing check-in (§7.2) -------------------------------------------------------------

CHECKIN_CUE_ID = "check_in"
CHECKIN_TEXT = "Are you okay? Give me a thumbs up or raise your hand."
CHECKIN_AUDIO_URL = "/audio/check_in.mp3"
CHECKIN_HAPTIC = "check_in"

# ---- Dashboard / phone alert lines (the `message` of each `alert` event) -------------------------

_KIND_WORDS = {"fall": "Fall detected", "pain": "Severe pain reported", "hr": "Heart rate above limit"}


def alert_message(kind: str, step: str, first_name: str, pronoun_subj: str) -> str:
    what = _KIND_WORDS.get(kind, "Safety alert")
    be = "are" if pronoun_subj == "they" else "is"
    messages = {
        "detected": f"{what}.",
        "checked_in": f"{what}. Asking {first_name} if {pronoun_subj} {be} OK.",
        "resolved": f"{first_name} responded. No call needed.",
        "escalated": f"No response from {first_name}. Calling the emergency contact.",
        "call_registered": "Calling the emergency contact.",
        "call_ongoing": "Emergency contact is on the call.",
        "call_update": "Shared a live update with the emergency contact.",
        "patient_responded": f"{first_name} responded during the call.",
        "call_ended": "Call with the emergency contact ended.",
        "call_not_connected": "The call did not connect.",
        "call_error": "The call failed.",
        "call_retry": "Calling the emergency contact again.",
        "call_failed": "Could not place the call. Check on the patient.",
        "acknowledged": f"The emergency contact will check on {first_name}.",
        "therapist_call": "Calling the therapist.",
        "call_watch_timeout": "Lost track of the call status.",
        "closed": "Incident closed.",
    }
    return messages.get(step, f"{what}: {step.replace('_', ' ')}.")


# ---- Live call updates (§8.6.4); the notifier prefixes "UPDATE <time>: " --------------------------

RESPONSE_PHRASES = {
    "thumbs_up": "just gave a thumbs up",
    "raised_hand": "just raised {poss} hand",
    "im_ok": "just pressed I'm OK on {poss} phone",
}


def response_update(first_name: str, response: str, pronoun_poss: str) -> str:
    phrase = RESPONSE_PHRASES.get(response, "just responded").format(poss=pronoun_poss)
    return f"{first_name} {phrase}."


def posture_update(first_name: str, posture_phrase: str, verb: str, estimated: bool = False) -> str:
    """Estimated postures (from the ring buffer, not the engine) are said as such: the agent must never guess."""
    if estimated:
        return f"{first_name} {'appear' if verb == 'are' else 'appears'} to be {posture_phrase} now."
    return f"{first_name} {verb} now {posture_phrase}."


def moving_update(first_name: str) -> str:
    return f"{first_name} has started moving."


def still_update(first_name: str, verb: str) -> str:
    return f"{first_name} {verb} still not moving and has not responded."


def hr_update(pronoun_poss: str, bpm: int) -> str:
    return f"{pronoun_poss.capitalize()} heart rate is now {bpm}."
