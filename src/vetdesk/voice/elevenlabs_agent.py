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
PROMPT = "conversation: {{system__conversation_id}}\ncaller: {{system__caller_id}}"


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
                },
                "first_message": f"Clínica veterinaria {kb.clinic.name}, dígame.",
                "language": "es",
            },
            "tts": {
                "voice_id": os.environ["VETDESK_TTS_VOICE"],
                "model_id": os.environ.get("VETDESK_TTS_MODEL", "eleven_v4_turbo"),
            },
            # ElevenLabs can ask for an answer before it is sure the caller has finished
            # and discard it if they go on ("speculative turn", on by default). Our agent's
            # answers have effects (a booking), so it is asked only when the turn is over.
            "turn": {"turn_timeout": 7, "turn_eagerness": "normal", "speculative_turn": False},
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
