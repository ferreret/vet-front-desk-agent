"""The same front desk carried by a second voice platform, Vapi, kept as code.

    uv run python -m vetdesk.voice.vapi_agent --url https://<public address>

Asked for to compare platforms: the agent behind is the same, and so is what the platform
is given. A voice, a way of hearing, and where to ask what to say. No instructions, no
knowledge and no model of the platform's are used; its prompt is three lines that tell our
address which call a request belongs to.

Creates the assistant the first time and updates it afterwards. It only ever touches the
assistant whose id it wrote to .env itself: the account holds others.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

from ..agent.prompt import picks_up
from ..kb import load_kb
from .elevenlabs_agent import _remember
from .endpoint import KEY_NAME, _load_env, platform_key

API = "https://api.vapi.ai"
VAPI_KEY, AGENT_ID = "VAPI_API_KEY", "VETDESK_VAPI_AGENT_ID"
# The key our address asks of this platform, kept at the platform under an id of its own.
CREDENTIAL_ID = "VETDESK_VAPI_CREDENTIAL_ID"
# Which call a request belongs to, who is calling, and the pass of a call made from the
# demo's page: the same three lines our address reads from the first platform.
PROMPT = ("vetdesk-conversation: vapi-{{call.id}}\n"
          "vetdesk-caller: {{customer.number}}\n"
          "vetdesk-demo: {{demo_pass}}")


def _call(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        # Named: the platform's front door turns away a client that does not say what it is.
        headers={"Authorization": f"Bearer {os.environ[VAPI_KEY]}",
                 "Content-Type": "application/json", "User-Agent": "vetdesk/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Vapi refused ({error.code}): {error.read().decode()[:900]}") \
            from error


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
            "messages": [{"role": "system", "content": PROMPT}],
            # To hang up: only the platform can put the phone down.
            "tools": [{"type": "endCall"}],
        },
        # The key our address asks of this platform is kept at the platform and named
        # here by its id. Handed over with the assistant instead, it was not the one the
        # platform then sent: it sent another the account already held.
        "credentials": [], "credentialIds": [credential],
        "voice": {"provider": "11labs", "voiceId": os.environ["VETDESK_TTS_VOICE"],
                  "model": os.environ.get("VETDESK_VAPI_TTS_MODEL", "eleven_turbo_v2_5")},
        # A recogniser that tells the language by itself: the clinic is on a tourist coast.
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
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
