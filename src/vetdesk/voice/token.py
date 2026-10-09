"""Keys and passes made here, with nothing but the standard library.

Two kinds. A key for a voice platform to ask our address with, made from ours so that ours
is never handed over. And a signed pass (a JWT), which is how both Vapi and LiveKit want
to be told that somebody may start or join one call: signed with a secret that never
leaves the server, and good for a short while.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


def platform_key(key: str, platform: str) -> str:
    """The key a voice platform is given, made from ours and good for its route only.

    Vapi hands back, to whoever can read the assistant, the key it was given for our
    address. Ours opens the route real calls come by, so it is not the one handed over:
    this one cannot be turned back into it, and needs no setting of its own.
    """
    return hmac.new(key.encode(), f"vetdesk:{platform}".encode(), hashlib.sha256).hexdigest()


def _part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def signed(claims: dict, secret: str) -> str:
    """`claims` as a pass signed with `secret`: a JWT, HS256."""
    said = ".".join(_part(json.dumps(part, separators=(",", ":")).encode())
                    for part in ({"alg": "HS256", "typ": "JWT"}, claims))
    return said + "." + _part(hmac.new(secret.encode(), said.encode(), hashlib.sha256).digest())
