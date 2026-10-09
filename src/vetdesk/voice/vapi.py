"""A call from the demo's page, carried by the second voice platform, Vapi.

On the first platform our server asks for the address of each browser call and hands it to
the page. Vapi has no such thing: a browser call is started with a key of the account that
is meant to sit in the page, for anybody to read and to start calls with. So the page is
given none. Our server starts the call itself, and the platform's browser library is told
to ask our server instead of Vapi (its documented way of keeping keys off a page).

The account's private key does not start browser calls. What does is a permit made from it:
a token signed with that key, good for a minute and for this one assistant, which never
leaves the server.

Started here, a call is known by the id Vapi gives it before anybody has spoken, and that
id is what ties each of its requests to the pass of the demo it was started with. Nothing
the browser says is believed about which call is which.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

log = logging.getLogger("vetdesk.vapi")

API = "https://api.vapi.ai"
VAPI_KEY, AGENT_ID = "VAPI_API_KEY", "VETDESK_VAPI_AGENT_ID"
# The name Vapi gives its tool for hanging up.
END_TOOL = "endCall"
# What the browser library reads of a call it has had started, and nothing else of what
# Vapi says of it: the rest (the account, ways to listen in and to steer the call) is not
# for a page.
FOR_THE_PAGE = ("id", "webCallUrl", "transport")


class Refused(Exception):
    """Vapi would not: its status and what it said."""


def ask(method: str, path: str, key: str, body: dict | None = None, timeout: float = 30) -> dict:
    request = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        # Named: the platform's front door turns away a client that does not say what it is.
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "vetdesk/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise Refused(f"{error.code}: {error.read().decode()[:900]}") from error


def say_and_hang_up(control: str, text: str) -> bool:
    """Have a call say `text` and end when it has been said. Whether Vapi took the order.

    Vapi's tool for hanging up ends the call the moment it is asked to, over whatever is
    still being said: on the first calls a farewell handed over ahead of it was cut short.
    Each call has an address to steer it by, sent with every request for it, and one of
    the things it takes is this. Nothing is sent to an address that is not Vapi's own.
    """
    where = urllib.parse.urlparse(control)
    if where.scheme != "https" or not (where.hostname or "").endswith(".vapi.ai"):
        log.warning("the address to steer the call by is not the platform's: nothing sent")
        return False
    request = urllib.request.Request(
        control, method="POST",
        data=json.dumps({"type": "say", "content": text, "endCallAfterSpoken": True}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "vetdesk/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except OSError as error:
        log.warning("the call could not be told to say goodbye and end (%s)",
                    type(error).__name__)
        return False


def _part(value: dict) -> str:
    packed = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(packed).rstrip(b"=").decode()


def permit(private_key: str, account: str, assistant: str, seconds: int = 60,
           now: Callable[[], float] = time.time) -> str:
    """A token to start one browser call of this assistant with, good for `seconds`.

    Signed with the account's private key, as Vapi asks, and marked for the one thing a
    page is allowed: starting a call, of this assistant and of no other made up on the spot.
    """
    issued = int(now())
    claims = {"orgId": account, "iat": issued, "exp": issued + seconds,
              "token": {"tag": "public",
                        "restrictions": {"enabled": True, "allowedAssistantIds": [assistant],
                                         "allowTransientAssistant": False}}}
    signed = _part({"alg": "HS256", "typ": "JWT"}) + "." + _part(claims)
    mark = hmac.new(private_key.encode(), signed.encode(), hashlib.sha256).digest()
    return signed + "." + base64.urlsafe_b64encode(mark).rstrip(b"=").decode()


def starter(private_key: str, assistant: str,
            asked: Callable[..., dict] = ask) -> Callable[[], dict]:
    """How a browser call of our assistant is started: what Vapi says of the new call."""
    account: list[str] = []  # whose the assistant is, asked once

    def start() -> dict:
        if not account:
            account.append(asked("GET", f"/assistant/{assistant}", private_key)["orgId"])
        return asked("POST", "/call/web", permit(private_key, account[0], assistant),
                     {"assistantId": assistant}, timeout=10)

    return start
