"""From the agent's blocking loop to a stream of speech.

`Call.say` runs the model and its tools and hands over the answer piece by piece through a
callback. A voice pipeline wants an async stream it can start speaking from at once. This
runs the call on a worker thread and yields each piece as it arrives.

No voice library is imported here, so it is tested without one.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from ..agent.agent import Call, Turn

# Said before a tool when the model itself says nothing: on the phone, silence is a dead line.
WAITING = {
    "es": "Un momento, por favor.",
    "ca": "Un moment, si us plau.",
    "en": "One moment, please.",
    "fr": "Un instant, s'il vous plaît.",
    "de": "Einen Moment, bitte.",
    "nl": "Een ogenblik, alstublieft.",
    "it": "Un momento, per favore.",
}
# Said when the model cannot be reached. The caller must never be left with nothing.
TROUBLE = {
    "es": "Perdone, he tenido un problema. ¿Me lo puede repetir?",
    "ca": "Perdoni, he tingut un problema. M'ho pot repetir?",
    "en": "Sorry, I had a problem. Could you say that again?",
    "fr": "Excusez-moi, j'ai eu un problème. Pouvez-vous répéter ?",
    "de": "Entschuldigung, es gab ein Problem. Können Sie das bitte wiederholen?",
    "nl": "Excuseer, er ging iets mis. Kunt u dat herhalen?",
    "it": "Mi scusi, ho avuto un problema. Può ripetere?",
}
# What speech recognition may call each language: two-letter and three-letter codes.
_CODES = {"ca": "ca", "cat": "ca", "en": "en", "eng": "en", "fr": "fr", "fra": "fr",
          "fre": "fr", "de": "de", "deu": "de", "ger": "de", "nl": "nl", "nld": "nl",
          "dut": "nl", "it": "it", "ita": "it"}


def language_of(code: str | None) -> str:
    """The language for the agent's own stock phrases, from what recognition reports
    ('ca', 'cat', 'en-GB'). Spanish when in doubt: it is the clinic's language."""
    return _CODES.get((code or "").lower().split("-")[0], "es")


class Line:
    """One call on a voice line. It answers one thing at a time, and each thing once."""

    def __init__(self, call: Call) -> None:
        self.call = call
        # A caller can talk over the agent, and the pipeline then asks for a new answer
        # while the turn it dropped is still running its tools. The conversation with the
        # model cannot take two turns at once, so the new one waits for the old to finish.
        self._busy = asyncio.Lock()
        # What was heard and answered in each numbered turn. Voice platforms ask again for
        # an answer they already have: after an interruption, on a retry, or because they
        # resend the whole conversation with every request. Answering twice would run the
        # tools twice, and a booking is not something to make twice.
        self._answered: dict[int, tuple[str, list[str]]] = {}

    async def answer(
        self,
        heard: str,
        language: str = "es",
        on_turn: Callable[[Turn], None] | None = None,
        turn: int | None = None,
    ) -> AsyncIterator[str]:
        """What the agent says to `heard`, piece by piece, as soon as each piece exists.

        `turn` numbers the caller's lines, when the platform can tell: asked again for the
        same line of the same turn, the agent repeats what it said instead of redoing it.
        """
        loop = asyncio.get_running_loop()
        pieces: asyncio.Queue[str | None] = asyncio.Queue()
        said: list[str] = []

        def say(piece: str) -> None:
            said.append(piece)  # kept whole even if the listener leaves half-way
            loop.call_soon_threadsafe(pieces.put_nowait, piece)

        def work() -> None:
            try:
                answer = self.call.say(heard, say, waiting_phrase=WAITING[language])
                if on_turn:
                    loop.call_soon_threadsafe(on_turn, answer)
            except Exception:  # the model or the network failed: say so, do not go silent
                say(TROUBLE[language])
            finally:
                loop.call_soon_threadsafe(pieces.put_nowait, None)

        await self._busy.acquire()
        before = self._answered.get(turn) if turn is not None else None
        if before is not None and before[0] == heard:
            self._busy.release()
            for piece in before[1]:
                yield piece
            return
        if turn is not None:
            self._answered[turn] = (heard, said)
        # Released when the turn ends, not when the listener leaves: an interrupted answer
        # stops being heard at once, but its turn runs on to keep the conversation whole.
        loop.run_in_executor(None, work).add_done_callback(lambda _: self._busy.release())
        while (piece := await pieces.get()) is not None:
            yield piece
