"""A call from the demo's page carried by LiveKit: what our server does for it.

The third way to carry the same front desk, asked for to compare. Here nobody else's
platform holds the call: LiveKit moves the sound, a program of ours listens and speaks
(`livekit_app`), and that program asks this server what to say (`desk_client`).

A visitor's browser joins a room, and to join it needs a pass signed with the LiveKit
project's secret, which only this server holds. The pass is for one room, named here and
never by the browser, and it asks for our program to be sent into it. The room's name is
what ties each request to the pass of the demo the call was started with.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .token import signed

URL, KEY, SECRET = "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"
# The name our program takes its calls under: a room is sent it only by asking for it.
AGENT = "vetdesk"


def way_in(url: str, key: str, secret: str, seconds: int = 120,
           now: Callable[[], float] = time.time) -> Callable[[str], dict]:
    """How a browser gets into one room: where to connect, and a pass good for `seconds`
    to do it in. The room closes soon after it empties."""

    def join(room: str) -> dict:
        issued = int(now())
        claims = {
            "iss": key, "sub": "visitor", "nbf": issued, "exp": issued + seconds,
            # To talk and to listen in that one room, and nothing else in the project.
            "video": {"room": room, "roomJoin": True, "canPublish": True,
                      "canSubscribe": True, "canPublishData": False},
            "roomConfig": {"agents": [{"agentName": AGENT}],
                           "emptyTimeout": 30, "departureTimeout": 5},
        }
        return {"url": url, "token": signed(claims, secret)}

    return join
