"""The same front desk carried by a second voice platform, Vapi, kept as code.

    uv run python -m vetdesk.voice.vapi_agent --url https://<public address>

Asked for to compare platforms: the agent behind is the same, and so is what the platform
is given. A voice, a way of hearing, and where to ask what to say. No instructions, no
knowledge and no model of the platform's are used, and it has no prompt at all: which call
a request belongs to is told by the id the platform sends with it (see `vapi`).

Creates the assistant the first time and updates it afterwards. It only ever touches the
assistant whose id it wrote to .env itself: the account holds others.
"""

from __future__ import annotations

import argparse
import os

from ..agent.prompt import picks_up
from ..kb import load_kb
from .elevenlabs_agent import _remember
from .endpoint import KEY_NAME, _load_env, platform_key
from .vapi import AGENT_ID, END_TOOL, VAPI_KEY, Refused, ask

# The key our address asks of this platform, kept at the platform under an id of its own.
CREDENTIAL_ID = "VETDESK_VAPI_CREDENTIAL_ID"


def _call(method: str, path: str, body: dict | None = None) -> dict:
    try:
        return ask(method, path, os.environ[VAPI_KEY], body)
    except Refused as refused:
        raise SystemExit(f"Vapi refused ({refused})") from refused


def config(url: str, credential: str) -> dict:
    """The whole of what lives at Vapi, but for the key: see `main`."""
    kb = load_kb()
    return {
        "name": f"{kb.clinic.name} demo (vetdesk)",
        "firstMessage": picks_up(kb),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            # Vapi asks `<url>/chat/completions`, in the form of a chat model's API.
            "provider": "custom-llm", "url": url.rstrip("/") + "/vapi", "model": "vetdesk",
            # No prompt: the variables of one written here did not come filled in, and
            # the platform says which call a request is for without it.
            "messages": [],
            # To hang up: only the platform can put the phone down.
            "tools": [{"type": END_TOOL}],
        },
        # The key our address asks of this platform is kept at the platform and named
        # here by its id. Handed over with the assistant instead, it was not the one the
        # platform then sent: it sent another the account already held.
        "credentials": [], "credentialIds": [credential],
        # The first platform's voice and its model for it, so that what is compared is
        # the platforms. With an older model the same voice was heard as a dry one, and
        # the first call by this platform was hung up at the greeting.
        "voice": {"provider": "11labs", "voiceId": os.environ["VETDESK_TTS_VOICE"],
                  "model": os.environ.get("VETDESK_TTS_MODEL", "eleven_v4_turbo")},
        # A recogniser that tells the language by itself: the clinic is on a tourist coast.
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
        # The page tells a visitor that the voice is not recorded. The platform records
        # unless told not to.
        "artifactPlan": {"recordingEnabled": False, "videoRecordingEnabled": False},
        # No model of the platform's reads the call afterwards either.
        "analysisPlan": {"summaryPlan": {"enabled": False},
                         "successEvaluationPlan": {"enabled": False}},
        # A little over our own three minutes, as on the first platform's demo.
        "maxDurationSeconds": int(os.environ.get("VETDESK_DEMO_MAX_SECONDS", "200")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="vetdesk.voice.vapi_agent", description=__doc__)
    parser.add_argument("--url", required=True,
                        help="public address of `vetdesk.voice.endpoint`")
    args = parser.parse_args()
    _load_env()
    for needed in (VAPI_KEY, "VETDESK_TTS_VOICE", KEY_NAME):
        if not os.environ.get(needed):
            raise SystemExit(f"{needed} is not set in .env")
    # Not our own key: the platform shows what it holds to whoever can read it (see
    # `platform_key`). Written again on every run, so that it follows ours when ours changes.
    key = {"provider": "custom-llm", "apiKey": platform_key(os.environ[KEY_NAME], "vapi")}
    if os.environ.get(CREDENTIAL_ID):
        _call("PATCH", f"/credential/{os.environ[CREDENTIAL_ID]}", key)
    else:
        _remember(CREDENTIAL_ID, _call("POST", "/credential", {**key, "name": "vetdesk"})["id"])
    settings = config(args.url, os.environ[CREDENTIAL_ID])
    # Never the address in what is printed: it is not for a log.
    if os.environ.get(AGENT_ID):
        assistant = _call("PATCH", f"/assistant/{os.environ[AGENT_ID]}", settings)
        print(f"updated assistant {assistant['id']}")
    else:
        assistant = _call("POST", "/assistant", settings)
        _remember(AGENT_ID, assistant["id"])
        print(f"created assistant {assistant['id']}, kept in .env as {AGENT_ID}")


if __name__ == "__main__":
    main()
