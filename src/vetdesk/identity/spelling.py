"""Names spelled aloud: the one kind of evidence that speech recognition does not garble.

A spelled word arrives as letters joined by hyphens. Simulated callers write "M-I-Q-U-E-L
R-O-S-S-E-L-L-Ó", one word after another. A real recogniser (ElevenLabs Scribe, tried on
2026-10-02 with a synthesized caller saying "eme, i, cu, u, e, ele, erre, o...") delivers
"M-I-Q-U-E-L-R-O-S-S-E-L-L-O": the same hyphens, but the words run together and the accent
is gone. So a name counts as spelled when its letters, in order, are among the letters the
caller spelled, whatever the word breaks and accents.
"""

from __future__ import annotations

import re

from ..legacy.normalize import fold

SPELLED_WORD = re.compile(r"(?<![\w-])(?:[^\W\d_]-)+[^\W\d_](?![\w-])")


def spelled_words(text: str) -> list[str]:
    """The runs of letters a caller spelled out in `text`, each put back together."""
    return [match.group().replace("-", "") for match in SPELLED_WORD.finditer(text)]


def was_spelled(name: str, spelled: list[str]) -> bool:
    """Whether every letter of `name` is, in order, in what the caller spelled.

    `spelled` is what `spelled_words` found in the caller's lines, oldest first.
    """
    letters = "".join(fold(word).replace(" ", "") for word in spelled)
    wanted = fold(name).replace(" ", "")
    return bool(wanted) and wanted in letters
