"""Simulated speech recognition between the simulated caller and the agent.

The caller says names as they are; the agent gets what the scenario says a recogniser would
deliver. A whole name is replaced as the scenario recorded it, and a word of a name said on
its own ("Llull, like I said") is replaced by what it turned into. Everything else passes
untouched, and so does a name spelled out letter by letter: spelling is reliable, hearing
is not. That is the reason the agent asks for it.
"""

from __future__ import annotations

import re

from ..scenario import Speech

# A word spelled aloud, as the simulated caller is told to write it: "L-L-U-L-L".
SPELLED_WORD = re.compile(r"(?<![\w-])(?:[^\W\d_]-)+[^\W\d_](?![\w-])")


def spelled_words(text: str) -> list[str]:
    """The words a caller spelled out in `text`, put back together."""
    return [match.group().replace("-", "") for match in SPELLED_WORD.finditer(text)]


class SpeechChannel:
    def __init__(self, speech: Speech) -> None:
        self._phrases: dict[str, str] = {}
        self._words: dict[str, str] = {}
        for utterance in speech.utterances:
            if utterance.said != utterance.heard:
                self._phrases.setdefault(utterance.said.lower(), utterance.heard)
            # Word by word, for when the caller says only part of a name. A trailing word
            # the recogniser swallowed has no counterpart and is left as said.
            for said, heard in zip(utterance.said.split(), utterance.heard.split(), strict=False):
                if said != heard:
                    self._words.setdefault(said, heard)
        alternatives = sorted(self._phrases, key=len, reverse=True) + sorted(self._words)
        # One pass, longest first: what has been replaced is never looked at again.
        self._pattern = re.compile(
            "|".join(rf"(?<!\w){re.escape(a)}(?!\w)" for a in alternatives), re.IGNORECASE
        ) if alternatives else None

    def hear(self, said: str) -> str:
        """What the agent receives when the caller says `said`."""
        if self._pattern is None:
            return said

        def heard(match: re.Match) -> str:
            text = match.group()
            phrase = self._phrases.get(text.lower())
            # Words are matched as written: "Mas" the surname, not "mas" the word.
            return phrase if phrase is not None else self._words.get(text, text)

        return self._pattern.sub(heard, said)
