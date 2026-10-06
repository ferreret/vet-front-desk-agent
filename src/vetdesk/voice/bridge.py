"""From the agent's blocking loop to a stream of speech.

`Call.say` runs the model and its tools and hands over the answer piece by piece through a
callback. A voice pipeline wants an async stream it can start speaking from at once. This
runs the call on a worker thread and yields each piece as it arrives.

No voice library is imported here, so it is tested without one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable

from ..agent.agent import Call, Turn

log = logging.getLogger("vetdesk.voice")

# Said when the caller has been waiting a while and has heard nothing: on the phone, silence
# is a dead line. By the clock, not by what the agent is doing. Said before every tool, a
# caller heard it six times in one call; said only before the agenda, the turn that works
# out who is calling left four seconds of silence.
WAIT_BEFORE_PHRASE = float(os.environ.get("VETDESK_WAIT_PHRASE_AFTER", "1.5"))
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


# A recogniser goes on listening after it has handed a line over, and a moment later may
# hand the same line over again, written differently: "Hola, buen día." and then "Hola,
# bon dia."; "655623964." and then the nine digits as words. The platform drops the answer
# it was getting and asks again. Seen in four turns of ten on a call in Catalan, 0.4 to
# 1.0 seconds apart. Asked for the same turn again within this many seconds, the words are
# the same line heard twice, however they are written. The second writing is the one the
# platform keeps, so it is the one answered when the first answer did nothing but talk;
# when the first answer ran a tool (a booking), that answer stands and is repeated.
HEARD_AGAIN_WITHIN = 3.0


class Line:
    """One call on a voice line. It answers one thing at a time, and each thing once."""

    def __init__(self, call: Call, patience: float | None = WAIT_BEFORE_PHRASE,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.call = call
        self._clock = clock
        # Seconds of nothing said before the waiting phrase is. None: never, for a platform
        # that fills its own silences.
        self.patience = patience
        # A caller can talk over the agent, and the pipeline then asks for a new answer
        # while the turn it dropped is still running its tools. The conversation with the
        # model cannot take two turns at once, so the new one waits for the old to finish.
        self._busy = asyncio.Lock()
        # What was heard and answered in each numbered turn. Voice platforms ask again for
        # an answer they already have: after an interruption, on a retry, or because they
        # resend the whole conversation with every request. Answering twice would run the
        # tools twice, and a booking is not something to make twice. With the moment each
        # was first asked for, to tell a line heard again from a new one.
        self._answered: dict[int, tuple[str, list[str], float]] = {}

    async def answer(
        self,
        heard: str,
        language: str | None = None,
        on_turn: Callable[[Turn], None] | None = None,
        turn: int | None = None,
    ) -> AsyncIterator[str]:
        """What the agent says to `heard`, piece by piece, as soon as each piece exists.

        `turn` numbers the caller's lines, when the platform can tell: asked again for the
        same line of the same turn, the agent repeats what it said instead of redoing it.
        The same turn written differently a moment later is the same line heard again (see
        `HEARD_AGAIN_WITHIN`): answered as a new line, the agent took "Once y media" for a
        caller who had not answered the question it had just asked about "Once i mitja".
        The first answer is taken back and the line answered as now written, unless that
        answer ran a tool: then it is repeated.

        `language` is for a platform that reports what its recogniser heard. Without it the
        stock phrases follow the language the call itself has worked out from the caller's
        words: a platform that says nothing must not mean Spanish for everybody.
        """
        loop = asyncio.get_running_loop()
        pieces: asyncio.Queue[str | None] = asyncio.Queue()
        said: list[str] = []

        def say(piece: str) -> None:
            said.append(piece)  # kept whole even if the listener leaves half-way
            loop.call_soon_threadsafe(pieces.put_nowait, piece)

        def work() -> None:
            try:
                answer = self.call.say(heard, say)
                if on_turn:
                    loop.call_soon_threadsafe(on_turn, answer)
            except Exception:  # the model or the network failed: say so, do not go silent
                say(TROUBLE[language or self.call.language])
            finally:
                loop.call_soon_threadsafe(pieces.put_nowait, None)

        await self._busy.acquire()
        before = self._answered.get(turn) if turn is not None else None
        same_words = before is not None and before[0] == heard
        again = same_words or (
            before is not None and self._clock() - before[2] <= HEARD_AGAIN_WITHIN)
        # The lock is held, so the first answer has finished and can be taken back whole.
        if again and (same_words or not self.call.take_back()):
            self._busy.release()
            if not same_words:
                log.info("turn %d heard again as %r: a tool ran on %r, so that answer stands",
                         turn, heard, before[0])
            for piece in before[1]:
                yield piece
            return
        if again:
            log.info("turn %d heard again as %r: %r and its answer taken back",
                     turn, heard, before[0])
        if turn is not None:
            self._answered[turn] = (heard, said, self._clock())
        # Released when the turn ends, not when the listener leaves: an interrupted answer
        # stops being heard at once, but its turn runs on to keep the conversation whole.
        loop.run_in_executor(None, work).add_done_callback(lambda _: self._busy.release())
        try:
            piece = await asyncio.wait_for(pieces.get(), self.patience)
        except TimeoutError:
            # Nothing yet. If the first words are not already on their way, ask for a moment,
            # in the language the call has worked out by now. The space after it is what
            # lets a voice start on the phrase without waiting for the next word.
            piece = WAITING[language or self.call.language] + " "
            if not said:
                said.append(piece)
                yield piece
            piece = await pieces.get()
        while piece is not None:
            yield piece
            piece = await pieces.get()
