"""The front-desk agent: a language model, a set of tools, and the loop between them."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ..kb import KnowledgeBase
from ..language import spoken_language
from ..legacy.models import Clinic
from ..llm import Conversation, LLMClient, LLMError, OnText, ToolResult, Usage
from ..scheduling import Agenda
from .prompt import call_context, greeting, system_prompt
from .tools import SPECS, CallSession, Toolbox, ToolEvent

MAX_TOOL_ROUNDS = 6

# What a model says to buy time, in the two languages the agent speaks. A turn that ends on
# one of these, with no question and little else, has promised something and done nothing.
_WAITING = re.compile(
    r"\b(un momento?|un moment|un segundo|un segon|un instante?|un instant|enseguida|"
    r"de seguida|ahora mismo|ara mateix|(lo|ho) (miro|compruebo|comprovo|consulto)|"
    r"d[eé]jeme|deixi'm|perm[ií]tame|permeti'm)\b", re.IGNORECASE)
STILL_WAITING = ("(Note from the phone system, not from the caller: you said you would look "
                 "something up and stopped. The caller is waiting in silence. Do it now with "
                 "your tools and give them the answer, without apologising.)")


def left_waiting(text: str) -> bool:
    """Whether what the model said is only a request to wait."""
    return len(text.split()) <= 8 and "?" not in text and bool(_WAITING.search(text))

# Said when the model cannot produce a turn, in the language of the call.
DID_NOT_FOLLOW = {"es": "Perdone, no le he entendido bien. ¿Me lo puede repetir?",
                  "ca": "Perdoni, no l'he entès bé. M'ho pot repetir?"}
TOO_MANY_STEPS = '{"error": "Too many steps in one turn. Answer the caller now."}'
CANNOT_HELP = {"es": "Perdone, con eso no le puedo ayudar por teléfono. Si quiere, tomo nota "
                     "y recepción le llama.",
               "ca": "Perdoni, amb això no el puc ajudar per telèfon. Si vol, en prenc nota i "
                     "recepció li trucarà."}
# Told to the model when the caller's words show which language they speak and it is not
# the one the call was going on in. Left to itself, a model greeted in Catalan went on in
# Spanish in a third of its answers.
LANGUAGE_NOTE = {
    "ca": "(Note from the phone system, not from the caller: the caller is speaking Catalan. "
          "Answer in Catalan from now on, every sentence, until they change language.)",
    "es": "(Note from the phone system, not from the caller: the caller is speaking Spanish. "
          "Answer in Spanish from now on, every sentence, until they change language.)",
}


@dataclass(frozen=True)
class Turn:
    """What the agent says in answer to the caller, and what it did to get there."""

    text: str
    events: tuple[ToolEvent, ...] = ()
    usage: Usage = field(default_factory=Usage)
    requests: int = 0
    # Seconds the model took on each request of this turn. On the phone their sum is the
    # silence the caller hears, so it is measured from the start.
    latencies: tuple[float, ...] = ()
    # Seconds until the agent's first words were available: what the caller perceives.
    first_words: float | None = None

    @property
    def seconds(self) -> float:
        return sum(self.latencies)


class Call:
    def __init__(self, conversation: Conversation, toolbox: Toolbox, greeting: str) -> None:
        self._conversation = conversation
        self._toolbox = toolbox
        self.greeting = greeting
        # The language the call is going on in: the clinic answers the phone in Spanish, and
        # the caller's own words change it.
        self.language = "es"

    @property
    def session(self) -> CallSession:
        return self._toolbox.session

    def say(
        self, text: str, on_text: OnText | None = None,
        waiting_phrase: str | Mapping[str, str] | None = None,
    ) -> Turn:
        """The caller says something; the agent answers, using its tools as needed.

        `on_text` receives the answer piece by piece as the model writes it, so what the
        agent says before running a tool ("un momento, lo miro") is heard before the tool
        runs, not after.

        `waiting_phrase` is said when the model reaches for a tool without a word. Measured
        over 82 calls, it does so in a third of the turns that use a tool, and those are the
        four-second silences. On a voice line the caller hears this instead. Given one phrase
        per language, the one for the caller's language is said.
        """
        self._toolbox.heard(text)
        asked = text
        language = spoken_language(text)
        if language and language != self.language:
            self.language = language
            asked = f"{text}\n\n{LANGUAGE_NOTE[language]}"
        if isinstance(waiting_phrase, Mapping):
            waiting_phrase = waiting_phrase.get(self.language)
        events_before = len(self.session.events)
        latencies: list[float] = []
        turn_started = time.perf_counter()
        first_words: float | None = None
        said_something = new_request = False

        def heard(piece: str) -> None:
            nonlocal first_words, said_something, new_request
            if first_words is None:
                first_words = time.perf_counter() - turn_started
            if on_text:
                on_text(" " + piece if said_something and new_request else piece)
            said_something, new_request = True, False

        def timed(send, payload):
            nonlocal new_request
            new_request = True
            started = time.perf_counter()
            answer = send(payload, heard)
            latencies.append(time.perf_counter() - started)
            return answer

        reply = timed(self._conversation.send_user, asked)
        spoken, usage, requests = [reply.text], reply.usage, 1
        if reply.tool_calls and not reply.text and waiting_phrase:
            new_request = True
            heard(waiting_phrase)
            spoken.append(waiting_phrase)

        def use_tools() -> None:
            nonlocal reply, usage, requests
            while reply.tool_calls:
                if requests <= MAX_TOOL_ROUNDS:
                    results = [self._toolbox.run(call) for call in reply.tool_calls]
                elif requests == MAX_TOOL_ROUNDS + 1:
                    # Too many rounds for one turn: answer the calls without running them,
                    # so the conversation stays well formed, and make the model speak.
                    results = [ToolResult(call.id, TOO_MANY_STEPS, True)
                               for call in reply.tool_calls]
                else:
                    raise LLMError("the model keeps calling tools instead of answering")
                reply = timed(self._conversation.send_tool_results, results)
                spoken.append(reply.text)
                usage, requests = usage + reply.usage, requests + 1

        use_tools()
        if reply.stop == "end" and left_waiting(reply.text):
            # "Un momento, lo miro", and the turn is over with nothing looked up. Measured:
            # one model did it in 11 of 435 answers. On the phone that is a dead line until
            # the caller speaks again, so the model is told once to do what it said. The
            # note is not the caller's: it is no evidence of who they are.
            reply = timed(self._conversation.send_user, STILL_WAITING)
            spoken.append(reply.text)
            usage, requests = usage + reply.usage, requests + 1
            use_tools()

        answer = " ".join(part for part in spoken if part)
        fallback = (CANNOT_HELP[self.language] if reply.stop == "refusal"
                    else "" if answer else DID_NOT_FOLLOW[self.language])
        if fallback:  # refused, cut off or empty: never go silent
            new_request = True
            heard(fallback)
            answer = fallback
        events = tuple(self.session.events[events_before:])
        return Turn(answer, events, usage, requests, tuple(latencies), first_words)


class FrontDeskAgent:
    def __init__(
        self,
        llm: LLMClient,
        clinic: Clinic,
        kb: KnowledgeBase,
        agenda: Agenda,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._llm, self._clinic, self._kb, self._agenda, self._now = llm, clinic, kb, agenda, now
        self._system = system_prompt(kb)

    def start_call(self, caller_number: str | None) -> Call:
        """Pick up the phone. `caller_number` is None when the caller ID is hidden."""
        now = self._now()
        hello = greeting(self._kb, now)
        toolbox = Toolbox(self._clinic, self._kb, self._agenda, self._now, caller_number)
        context = call_context(self._kb, now, caller_number, hello)
        return Call(self._llm.start(self._system, context, SPECS), toolbox, hello)
