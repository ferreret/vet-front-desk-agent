"""Names spelled aloud: the one kind of evidence that speech recognition does not garble.

For now a spelled word arrives as letters joined by hyphens ("L-L-U-L-L"), which is how the
text channel and the simulated callers write it. A real recogniser will deliver spelling
its own way; this is the function to adapt when the voice layer arrives.
"""

from __future__ import annotations

import re

SPELLED_WORD = re.compile(r"(?<![\w-])(?:[^\W\d_]-)+[^\W\d_](?![\w-])")


def spelled_words(text: str) -> list[str]:
    """The words a caller spelled out in `text`, put back together."""
    return [match.group().replace("-", "") for match in SPELLED_WORD.finditer(text)]
