"""The ElevenLabs agent that carries the voice, kept as code.

    uv run python -m vetdesk.voice.elevenlabs_agent --url https://<public address>

Creates the agent the first time and updates it afterwards (the public address changes
whenever the tunnel is restarted). What it sets up is deliberately thin: a voice, a
language, and where to ask what to say. No instructions, no tools, no knowledge base live
at ElevenLabs. Its prompt is two lines that tell our address which call a request belongs
to and who is calling; everything else is `FrontDeskAgent`.

It only ever touches the agent whose id it wrote to .env itself.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from ..kb import load_kb
from .endpoint import KEY_NAME, _load_env

API = "https://api.elevenlabs.io/v1/convai"
AGENT_ID, SECRET_ID = "VETDESK_ELEVENLABS_AGENT_ID", "VETDESK_ELEVENLABS_SECRET_ID"
PROMPT = ("vetdesk-conversation: {{system__conversation_id}}\n"
          "vetdesk-caller: {{system__caller_id}}")


def _call(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"xi-api-key": os.environ["ELEVEN_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"ElevenLabs refused ({error.code}): {error.read().decode()[:600]}") \
            from error


def _remember(name: str, value: str, path: Path = Path(".env")) -> None:
    os.environ[name] = value
    with path.open("a", encoding="utf-8") as env:
        env.write(f"\n{name}={value}\n")


def _transfer() -> dict:
    """The platform's tool for putting a call through, when there is a number to put it
    through to. Our address decides when (a caller wants a person, the clinic is open);
    the platform does the dialling. The number comes from the environment, never from here."""
    number = os.environ.get("VETDESK_TRANSFER_TO", "").strip()
    if not number:
        return {}
    return {"transfer_to_number": {
        "type": "system", "name": "transfer_to_number",
        # What the caller hears is said by our address, before the tool, and the tool
        # waits for it: the platform's own message for the caller is off, or a platform
        # that does say it would say it twice.
        "force_pre_tool_speech": True,
        "params": {"system_tool_type": "transfer_to_number", "enable_client_message": False,
                   "transfers": [{
                       "transfer_destination": {"type": "phone", "phone_number": number},
                       "condition": "Only when asked for by the custom model.",
                       "transfer_type": "conference"}]},
    }}


def config(url: str, secret_id: str) -> dict:
    """The whole of what lives at ElevenLabs."""
    kb = load_kb()
    return {
        "name": f"{kb.clinic.name} front desk (vetdesk)",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": PROMPT,
                    "llm": "custom-llm",
                    "custom_llm": {"url": url.rstrip("/") + "/v1", "model_id": "vetdesk",
                                   "api_key": {"secret_id": secret_id}},
                    # The platform's tool for changing the language it listens and speaks
                    # in. It is meant for the platform's model; here our address calls it,
                    # when the caller's words tell another language (see `endpoint`).
                    "built_in_tools": {
                        "language_detection": {
                            "type": "system", "name": "language_detection",
                            "params": {"system_tool_type": "language_detection",
                                       "only_at_conversation_start": False},
                        },
                        # To hang up. Our address says the goodbye and decides, in code,
                        # that the call is over; only the platform can put the phone down,
                        # and it waits for the goodbye to be said before it does.
                        "end_call": {"type": "system", "name": "end_call",
                                     "force_pre_tool_speech": True,
                                     "params": {"system_tool_type": "end_call"}},
                        **_transfer(),
                    },
                },
                "first_message": f"Clínica veterinaria {kb.clinic.name}, dígame.",
                "language": "es",
            },
            # The phone is answered in Spanish. The other languages are the ones the
            # platform may be told to change to: without them its recogniser, set to
            # Spanish, writes whatever it hears as Spanish. The clinic is on a tourist
            # coast: Catalan, and the visitors' languages.
            "language_presets": {
                code.strip(): {"overrides": {}}
                for code in os.environ.get("VETDESK_LANGUAGES", "ca,en,de,ru,fr,it").split(",")
            },
            "tts": {
                "voice_id": os.environ["VETDESK_TTS_VOICE"],
                "model_id": os.environ.get("VETDESK_TTS_MODEL", "eleven_v4_turbo"),
            },
            # ElevenLabs can ask for an answer before it is sure the caller has finished
            # and discard it if they go on ("speculative turn", on by default). Our agent's
            # answers have effects (a booking), so it is asked only when the turn is over.
            "turn": {
                "turn_timeout": 7, "turn_eagerness": "normal", "speculative_turn": False,
                # Our address can say goodbye to a silent line; only the platform can hang
                # up. Without this a call nobody is on stays open, and is paid for.
                "silence_end_call_timeout": float(os.environ.get("VETDESK_HANG_UP_AFTER", "30")),
                # When our address takes longer than this to answer, the platform says the
                # filler itself, at once. A phrase sent from our side was held back until
                # the answer came (measured on three calls), so it covered nothing. The
                # filler is no word of any language: the agent is set up in Spanish only,
                # and a caller speaking Catalan was already told "un momento, por favor"
                # once. Not generated by the platform's model: no model of theirs is used.
                "soft_timeout_config": {
                    "timeout_seconds": float(os.environ.get("VETDESK_SOFT_TIMEOUT", "2")),
                    "message": "Mmm...",
                    "use_llm_generated_message": False,
                },
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="vetdesk.voice.elevenlabs_agent", description=__doc__)
    parser.add_argument("--url", required=True,
                        help="public address of `vetdesk.voice.endpoint`, e.g. a tunnel's")
    args = parser.parse_args()
    _load_env()
    for needed in ("ELEVEN_API_KEY", "VETDESK_TTS_VOICE", KEY_NAME):
        if not os.environ.get(needed):
            raise SystemExit(f"{needed} is not set in .env")

    if not os.environ.get(SECRET_ID):
        # ElevenLabs sends this key with every request to our address. It is stored on their
        # side as a secret, not in the agent's configuration.
        secret = _call("POST", "/secrets", {"type": "new", "name": "vetdesk-endpoint-key",
                                            "value": os.environ[KEY_NAME]})
        _remember(SECRET_ID, secret["secret_id"])
    settings = config(args.url, os.environ[SECRET_ID])

    if os.environ.get(AGENT_ID):
        agent = _call("PATCH", f"/agents/{os.environ[AGENT_ID]}", settings)
        print(f"updated agent {agent['agent_id']}: it now asks {args.url}")
    else:
        agent = _call("POST", "/agents/create", settings)
        _remember(AGENT_ID, agent["agent_id"])
        print(f"created agent {agent['agent_id']}: it asks {args.url}")


if __name__ == "__main__":
    main()
