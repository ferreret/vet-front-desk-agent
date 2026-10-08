"""The ElevenLabs agent that carries the voice, kept as code.

    uv run python -m vetdesk.voice.elevenlabs_agent --url https://<public address>

Creates the agent the first time and updates it afterwards (the public address changes
whenever the tunnel is restarted). What it sets up is deliberately thin: a voice, a
language, and where to ask what to say. No instructions, no tools, no knowledge base live
at ElevenLabs. Its prompt is two lines that tell our address which call a request belongs
to and who is calling; everything else is `FrontDeskAgent`.

With `--demo` it sets up a second agent, the public demo's, for calls from a browser:
the same voice and the same address, nobody to put a call through to, and limits of its
own (see `demo_settings`). The phone's agent is left as it is.

It only ever touches the agents whose ids it wrote to .env itself.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from ..agent.prompt import picks_up
from ..kb import load_kb
from .endpoint import KEY_NAME, _load_env

API = "https://api.elevenlabs.io/v1/convai"
AGENT_ID, SECRET_ID = "VETDESK_ELEVENLABS_AGENT_ID", "VETDESK_ELEVENLABS_SECRET_ID"
PROMPT = ("vetdesk-conversation: {{system__conversation_id}}\n"
          "vetdesk-caller: {{system__caller_id}}")
# The demo's agent says which pass the call was started with, not who is calling: a
# browser has no number, and our address knows whose number the pass stands for.
DEMO_AGENT_ID = "VETDESK_ELEVENLABS_DEMO_AGENT_ID"
DEMO_PROMPT = ("vetdesk-conversation: {{system__conversation_id}}\n"
               "vetdesk-demo: {{demo_pass}}")


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


def demo_settings(settings: dict) -> dict:
    """The phone agent's settings, turned into the public demo's.

    Anybody can open the demo's page, so the limits are set in three places: our address
    counts the minutes and closes a call that has run its time, and the platform is told
    the same, in case ours ever fails to.
    """
    kb = load_kb()
    conversation = settings["conversation_config"]
    agent = conversation["agent"]
    agent["prompt"]["prompt"] = DEMO_PROMPT
    agent["prompt"]["built_in_tools"].pop("transfer_to_number", None)  # nobody to pass it to
    # With no pass the line still reaches our address, which says so and closes it.
    agent["dynamic_variables"] = {"dynamic_variable_placeholders": {"demo_pass": ""}}
    # A little over our own three minutes: ours says goodbye first, this cuts if it did not.
    conversation["conversation"] = {
        "max_duration_seconds": int(os.environ.get("VETDESK_DEMO_MAX_SECONDS", "200"))}
    return {
        "name": f"{kb.clinic.name} demo (vetdesk)",
        "conversation_config": conversation,
        "platform_settings": {
            # A call can only be started with an address our server asked for.
            "auth": {"enable_auth": True},
            # Two visitors at once and so many calls a day, and never at the higher price
            # the platform charges for calls beyond its limit.
            "call_limits": {
                "agent_concurrency_limit": 2,
                "daily_limit": int(os.environ.get("VETDESK_DEMO_CALLS_A_DAY", "60")),
                "bursting_enabled": False},
            # A visitor's voice is not kept, and what they said not for long.
            "privacy": {"record_voice": False, "retention_days": 30},
        },
    }


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
                "first_message": picks_up(kb),
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
                # Three seconds and not two: at two it was said in 25 turns of 70 on the
                # demo's first calls, 8 of 9 in one of them, and the first person to try
                # it found it tiresome. At three it covers only the long silences.
                "soft_timeout_config": {
                    "timeout_seconds": float(os.environ.get("VETDESK_SOFT_TIMEOUT", "3")),
                    "message": "Mmm...",
                    "use_llm_generated_message": False,
                },
            },
            # A voice near the caller (a radio, somebody talking in the room) was taken for
            # the caller's own line for fifteen seconds on one call, and cut the agent's
            # answer four times. The platform's filter for voices in the background.
            "vad": {"background_voice_detection": True},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="vetdesk.voice.elevenlabs_agent", description=__doc__)
    parser.add_argument("--url", required=True,
                        help="public address of `vetdesk.voice.endpoint`, e.g. a tunnel's")
    parser.add_argument("--demo", action="store_true",
                        help="set up the public demo's agent instead of the phone's")
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
    name = AGENT_ID
    if args.demo:
        settings, name = demo_settings(settings), DEMO_AGENT_ID

    # Never the address in what is printed: it is not for a log.
    if os.environ.get(name):
        agent = _call("PATCH", f"/agents/{os.environ[name]}", settings)
        print(f"updated agent {agent['agent_id']}")
    else:
        agent = _call("POST", "/agents/create", settings)
        _remember(name, agent["agent_id"])
        print(f"created agent {agent['agent_id']}, kept in .env as {name}")


if __name__ == "__main__":
    main()
