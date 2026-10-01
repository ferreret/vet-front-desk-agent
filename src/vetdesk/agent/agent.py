"""The front-desk agent: a language model, a set of tools, and the loop between them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..kb import KnowledgeBase
from ..legacy.models import Clinic
from ..llm import Conversation, LLMClient, LLMError, ToolResult, Usage
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


class Call:
    def __init__(self, conversation: Conversation, toolbox: Toolbox, greeting: str) -> None:
        self._conversation = conversation
        self._toolbox = toolbox
        self.greeting = greeting

    @property
    def session(self) -> CallSession:
        return self._toolbox.session

    def say(self, text: str) -> Turn:
        """The caller says something; the agent answers, using its tools as needed."""
        events_before = len(self.session.events)
        reply = self._conversation.send_user(text)
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
            reply = self._conversation.send_tool_results(results)
            spoken.append(reply.text)
            usage, requests = usage + reply.usage, requests + 1

        answer = " ".join(part for part in spoken if part)
        if reply.stop == "refusal":
            answer = CANNOT_HELP
        elif reply.stop != "end" or not answer:
            # Cut off, or still asking for tools after too many rounds: never go silent.
            answer = answer or DID_NOT_FOLLOW
        return Turn(answer, tuple(self.session.events[events_before:]), usage, requests)


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
