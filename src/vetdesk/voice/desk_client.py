"""Asking the front desk from another program, the way a voice platform does.

LiveKit's side of a call is a program of ours, and it could hold the agent itself. It asks
our server instead, at an address like the ones the voice platforms ask: the passes of the
demo, the record of the call, the time a call may last and when to hang up are all decided
there, once, for every way a call can come in. This program is left with ears and a mouth.

No voice library is imported here, so it is tested without one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp

from .token import platform_key

# Where the front desk answers, for a program on the same machine, and the name of this
# way of carrying a call: what its key is made for and what its route is called.
DESK_URL, PLATFORM = "VETDESK_DESK_URL", "livekit"
END_TOOL = "end_call"  # the same name as on the first platform


@dataclass
class Asked:
    """What came with an answer besides its words."""

    hang_up: bool = False  # the call is over once the answer has been said


async def ask(http: aiohttp.ClientSession, desk: str, key: str, call: str,
              messages: list[dict], asked: Asked,
              phone: str | None = None) -> AsyncIterator[str]:
    """What the front desk says to the conversation so far, piece by piece. `key` is our
    own; what is sent is the one made from it for this route. `call` is the room.
    `phone` is for a call that came in over the phone line: the number it came from, or
    "" when that is hidden. None for a call from a browser."""
    body = {"model": "vetdesk", "stream": True, "messages": messages,
            "call": {"id": call, **({} if phone is None else {"phone": phone})},
            "tools": [{"type": "function", "function": {"name": END_TOOL}}]}
    headers = {"Authorization": f"Bearer {platform_key(key, PLATFORM)}"}
    url = desk.rstrip("/") + f"/{PLATFORM}/chat/completions"
    async with http.post(url, json=body, headers=headers) as response:
        response.raise_for_status()
        async for raw in response.content:
            line = raw.decode().strip()
            if not line.startswith("data: {"):
                continue
            delta = json.loads(line[6:])["choices"][0]["delta"]
            if delta.get("content"):
                yield delta["content"]
            if any((used.get("function") or {}).get("name") == END_TOOL
                   for used in delta.get("tool_calls") or []):
                asked.hang_up = True
