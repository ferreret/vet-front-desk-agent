"""The front-desk agent: a language model, a set of tools, and the loop between them."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..kb import KnowledgeBase
from ..legacy.models import Clinic
from ..llm import Conversation, LLMClient, LLMError, OnText, ToolResult, Usage
from ..scheduling import Agenda
from .prompt import call_context, greeting, system_prompt
from .tools import SPECS, CallSession, Toolbox, ToolEvent

MAX_TOOL_ROUNDS = 6

# Said when the model cannot produce a turn. Spanish, the clinic's default language.
DID_NOT_FOLLOW = "Perdone, no le he entendido bien. ¿Me lo puede repetir?"
TOO_MANY_STEPS = '{"error": "Too many steps in one turn. Answer the caller now."}'
CANNOT_HELP = ("Perdone, con eso no le puedo ayudar por teléfono. Si quiere, tomo nota y "
               "recepción le llama.")


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

    @property
    def session(self) -> CallSession:
        return self._toolbox.session

    def say(self, text: str, on_text: OnText | None = None) -> Turn:
        """The caller says something; the agent answers, using its tools as needed.

        `on_text` receives the answer piece by piece as the model writes it, so what the
        agent says before running a tool ("un momento, lo miro") is heard before the tool
        runs, not after.
        """
        self._toolbox.heard(text)
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

        reply = timed(self._conversation.send_user, text)
        spoken, usage, requests = [reply.text], reply.usage, 1
        while reply.tool_calls:
            if requests <= MAX_TOOL_ROUNDS:
                results = [self._toolbox.run(call) for call in reply.tool_calls]
            elif requests == MAX_TOOL_ROUNDS + 1:
                # Too many rounds for one turn: answer the calls without running them, so
                # the conversation stays well formed, and make the model speak.
                results = [ToolResult(call.id, TOO_MANY_STEPS, True) for call in reply.tool_calls]
            else:
                raise LLMError("the model keeps calling tools instead of answering")
            reply = timed(self._conversation.send_tool_results, results)
            spoken.append(reply.text)
            usage, requests = usage + reply.usage, requests + 1

        answer = " ".join(part for part in spoken if part)
        fallback = CANNOT_HELP if reply.stop == "refusal" else "" if answer else DID_NOT_FOLLOW
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
