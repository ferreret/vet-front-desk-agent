"""The agent behind a chat-completions address, for a voice platform that brings its own
ears and mouth.

ElevenLabs Agents can be told to use a "custom LLM": an address that speaks the OpenAI
chat-completions format. ElevenLabs then does the listening, the turn-taking, the
interruptions, the voice and the phone line, and asks this address what to say. What
answers here is `FrontDeskAgent`, the agent the evaluation harness measures: its prompt,
its tools and its privacy barrier, not a model with a prompt in somebody's dashboard.

    uv run python -m vetdesk.voice.endpoint            # listens on port 8013

The platform resends the whole conversation with every request; the agent keeps its own.
So each request is matched to its call, and only the caller's last line is taken from it.
The agent set up on the platform needs nothing but these two lines as its prompt, which
tell this address which call a request belongs to and who is calling:

    conversation: {{system__conversation_id}}
    caller: {{system__caller_id}}

The address has to be reachable from the internet, so every request must carry the key in
VETDESK_ENDPOINT_KEY as a bearer token. Without the key nothing is answered.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aiohttp import web

from ..agent import FrontDeskAgent
from ..agent.agent import Call
from ..kb import load_kb
from ..legacy import LegacySqliteSource
from ..legacy.normalize import parse_phones
from ..llm import create_client
from ..scheduling import SqliteAgenda
from .bridge import Line

log = logging.getLogger("vetdesk.endpoint")

KEY_NAME = "VETDESK_ENDPOINT_KEY"
IDLE_SECONDS = 30 * 60  # a call nobody has asked about for this long is over
_CONVERSATION = re.compile(r"^\s*conversation:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_CALLER = re.compile(r"^\s*caller:\s*(.*)$", re.MULTILINE | re.IGNORECASE)


def _text(content) -> str:
    """A message's text, whether it came as a string or as a list of parts."""
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


class Switchboard:
    """Which call each request belongs to. One `Line` per conversation."""

    def __init__(self, start_call: Callable[[str | None], Call],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._start_call, self._clock = start_call, clock
        self._lines: dict[str, tuple[Line, float]] = {}

    def line(self, messages: list[dict]) -> Line:
        system = " \n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
        found = _CONVERSATION.search(system)
        if found and "{{" not in found.group(1):
            conversation = found.group(1)
        else:
            # No id from the platform: the opening of a conversation never changes, so it
            # serves as one. Two calls that open with the same words would be confused,
            # which is why the two prompt lines above matter.
            opening = json.dumps(messages[:2], sort_keys=True, ensure_ascii=False)
            conversation = "opening-" + hashlib.sha256(opening.encode()).hexdigest()[:16]
        now = self._clock()
        self._lines = {k: v for k, v in self._lines.items() if now - v[1] < IDLE_SECONDS}
        if conversation not in self._lines:
            caller = _CALLER.search(system)
            numbers, _ = parse_phones(caller.group(1) if caller else "")
            number = numbers[0] if numbers else None
            log.info("call %s from %s", conversation, number or "a hidden number")
            self._lines[conversation] = (Line(self._start_call(number)), now)
        line, _ = self._lines[conversation]
        self._lines[conversation] = (line, now)
        return line


def _chunk(request_id: str, model: str, delta: dict, finish: str | None = None) -> bytes:
    body = {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


def build_app(switchboard: Switchboard, key: str) -> web.Application:
    async def chat_completions(request: web.Request) -> web.StreamResponse:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not key or not hmac.compare_digest(given.encode(), key.encode()):
            return web.json_response({"error": {"message": "invalid key"}}, status=401)
        try:
            body = await request.json()
            messages = list(body["messages"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": {"message": "expected chat messages"}},
                                     status=400)
        line = switchboard.line(messages)
        said = [_text(m.get("content")) for m in messages if m.get("role") == "user"]
        request_id = "chatcmpl-" + secrets.token_hex(8)
        model = str(body.get("model", "vetdesk"))

        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                               "Cache-Control": "no-cache"})
        await response.prepare(request)
        await response.write(_chunk(request_id, model, {"role": "assistant", "content": ""}))
        if not said:  # asked to open the call: the agent's own greeting
            await response.write(_chunk(request_id, model, {"content": line.call.greeting}))
        else:
            log.info("caller: %s", said[-1])
            answer = []
            async for piece in line.answer(said[-1], turn=len(said)):
                answer.append(piece)
                await response.write(_chunk(request_id, model, {"content": piece}))
            log.info("agent: %s", "".join(answer))
        await response.write(_chunk(request_id, model, {}, "stop"))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_post("/chat/completions", chat_completions)
    app.router.add_get("/health", health)
    return app


def _load_env(path: Path = Path(".env")) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name and not name.startswith("#"):
                os.environ.setdefault(name.strip(), value.strip().strip("'\""))


def _key(path: Path = Path(".env")) -> str:
    """The key requests must carry. Made on first use and kept in .env, never printed."""
    if not os.environ.get(KEY_NAME):
        os.environ[KEY_NAME] = secrets.token_urlsafe(32)
        with path.open("a", encoding="utf-8") as env:
            env.write(f"\n{KEY_NAME}={os.environ[KEY_NAME]}\n")
        print(f"A new key was written to {path} as {KEY_NAME}.")
    return os.environ[KEY_NAME]


def main() -> None:
    parser = argparse.ArgumentParser(prog="vetdesk.voice.endpoint", description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("VETDESK_DATA", "data")))
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on; a tunnel reaches it from outside")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    _load_env()
    kb = load_kb()
    clinic = LegacySqliteSource(args.data / "clinic.db").load()
    front_desk = FrontDeskAgent(create_client(), clinic, kb, SqliteAgenda(kb, datetime.now))
    app = build_app(Switchboard(front_desk.start_call), _key())
    print(f"The front desk answers at http://{args.host}:{args.port}/v1/chat/completions")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
