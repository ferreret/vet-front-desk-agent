"""The front-desk agent: a language model, a set of tools, and the loop between them."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .. import notices
from ..kb import KnowledgeBase
from ..kb.model import spoken_phone
from ..language import spoken_language
from ..legacy.models import Clinic
from ..llm import Conversation, LLMClient, LLMError, OnText, ToolResult, Usage
from ..scheduling import Agenda
from .prompt import (
    LANGUAGE_NAMES,
    call_context,
    greeting,
    in_an_emergency,
    set_phrases,
    system_prompt,
)
from .tools import SPECS, CallSession, Toolbox, ToolEvent

MAX_TOOL_ROUNDS = 6

# What a model says to buy time, in the languages the agent speaks. A turn that ends on
# one of these, with no question and little else, has promised something and done nothing.
_WAITING = re.compile(
    r"\b(un momento?|un moment|un segundo|un segon|un instante?|un instant|enseguida|"
    r"de seguida|ahora mismo|ara mateix|(lo|ho) (miro|compruebo|comprovo|consulto)|"
    r"d[eé]jeme|deixi'm|perm[ií]tame|permeti'm|(one|just a) (moment|second)|hold on|"
    r"bear with me|let me (check|see|look|have a look)|i(('| wi)ll| will) (check|look))\b",
    re.IGNORECASE)
STILL_WAITING = ("(Note from the phone system, not from the caller: you said you would look "
                 "something up and stopped. The caller is waiting in silence. Do it now with "
                 "your tools and give them the answer, without apologising.)")


# The most one turn may say, in characters. The longest answer measured over 650 is about
# 450. A model once filled a turn with "Let me check the schedule. Let me look at the
# available times." until its token limit, some three minutes of speech: past this the
# rest is not said, and the model is told to get on with it.
MAX_SPOKEN = 700


def left_waiting(text: str) -> bool:
    """Whether what the model said is only a request to wait."""
    return len(text.split()) <= 8 and "?" not in text and bool(_WAITING.search(text))

# Said when the model cannot produce a turn, in the language of the call.
DID_NOT_FOLLOW = {"es": "Perdone, no le he entendido bien. ¿Me lo puede repetir?",
                  "ca": "Perdoni, no l'he entès bé. M'ho pot repetir?",
                  "en": "Sorry, I didn't catch that. Could you say it again?",
                  "de": "Entschuldigung, das habe ich nicht verstanden. Können Sie es bitte "
                        "wiederholen?",
                  "fr": "Excusez-moi, je n'ai pas bien compris. Pouvez-vous répéter ?",
                  "it": "Mi scusi, non ho capito bene. Può ripetere?",
                  "ru": "Извините, я не расслышала. Повторите, пожалуйста."}
TOO_MANY_STEPS = '{"error": "Too many steps in one turn. Answer the caller now."}'
CANNOT_HELP = {"es": "Perdone, con eso no le puedo ayudar por teléfono. Si quiere, tomo nota "
                     "y recepción le llama.",
               "ca": "Perdoni, amb això no el puc ajudar per telèfon. Si vol, en prenc nota i "
                     "recepció li trucarà.",
               "en": "Sorry, I can't help with that over the phone. If you like, I'll take a "
                     "note and reception will call you.",
               "de": "Entschuldigung, dabei kann ich Ihnen am Telefon nicht helfen. Wenn Sie "
                     "möchten, notiere ich es und die Rezeption ruft Sie an.",
               "fr": "Excusez-moi, je ne peux pas vous aider pour cela par téléphone. Si vous "
                     "voulez, je prends note et la réception vous appellera.",
               "it": "Mi scusi, per questo non posso aiutarla al telefono. Se vuole, prendo "
                     "nota e la reception la chiamerà.",
               "ru": "Извините, с этим я не могу помочь по телефону. Если хотите, я запишу, "
                     "и вам позвонят из регистратуры."}
# Told to the model when the caller's words show which language they speak and it is not
# the one the call was going on in. Left to itself, a model greeted in Catalan went on in
# Spanish in a third of its answers.
# How many of a caller's first lines may change the language on a single telling word.
SETTLING_LINES = 2
STILL_IN = {
    code: f"(Note from the phone system, not from the caller: the call is in {name}.)"
    for code, name in LANGUAGE_NAMES.items()
}
LANGUAGE_NOTE = {
    code: f"(Note from the phone system, not from the caller: the caller is speaking {name}. "
          f"Answer in {name} from now on, every sentence, until they change language. "
          f"{set_phrases(code)})"
    for code, name in LANGUAGE_NAMES.items()
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
    def __init__(
        self, conversation: Conversation, toolbox: Toolbox, greeting: str,
        emergency: Callable[[str], str] = lambda language: "",
        languages: tuple[str, ...] = tuple(LANGUAGE_NAMES),
        emergency_phone: str = "",
    ) -> None:
        self._conversation = conversation
        self._toolbox = toolbox
        self.greeting = greeting
        # What to say in an emergency on this call, by language: it depends on the hour.
        self._emergency = emergency
        self._languages = languages  # the ones the clinic's front desk speaks
        # The emergency number as it is said: an answer that gives it is an emergency, and
        # reception is told, once a call.
        self._emergency_phone = emergency_phone
        self._emergency_told = False
        # The language the call is going on in: the clinic answers the phone in Spanish, and
        # the caller's own words change it.
        self.language = "es"
        # How things stood before the caller's last line, while that line can still be
        # taken back: see `take_back`.
        self._before_last: tuple[int, str, int] | None = None
        self._lines = 0  # how many lines the caller has said

    @property
    def session(self) -> CallSession:
        return self._toolbox.session

    def hears(self, text: str) -> str:
        """The language the call will be in once the caller has said `text`.

        A caller's first lines settle it: there a single telling word is enough, as in a
        greeting. After that it takes two, so that a name, a town or a word two languages
        share does not carry the call off into another language.
        """
        told = spoken_language(text, least=1 if self._lines < SETTLING_LINES else 2)
        return told if told in self._languages else self.language

    def say(self, text: str, on_text: OnText | None = None) -> Turn:
        """The caller says something; the agent answers, using its tools as needed.

        `on_text` receives the answer piece by piece as the model writes it, so what the
        agent says before running a tool ("un momento, lo miro") is heard before the tool
        runs, not after.
        """
        self._before_last = None
        before = (self._conversation.mark(), self.language, self._lines)
        self._toolbox.heard(text)
        asked = text
        language = self.hears(text)
        self._lines += 1
        if language != self.language:
            self.language = language
            asked = f"{text}\n\n{LANGUAGE_NOTE[language]}"
            if urgent := self._emergency(language):
                asked = f"{asked[:-1]} {urgent})"
        elif language != "es" and spoken_language(text) is None:
            # A line that tells no language: a name, a town, a number. Seen in French and
            # in Russian: given "Maria Ma Sala" and nothing else, the model answered in
            # Italian. It is reminded which language the call is in. Not in Spanish, the
            # language the call starts in and the instructions say so.
            asked = f"{text}\n\n{STILL_IN[language]}"
        self.session.language = self.language  # what the tools hand over to be said
        events_before = len(self.session.events)
        latencies: list[float] = []
        turn_started = time.perf_counter()
        first_words: float | None = None
        said_something = new_request = False
        room = MAX_SPOKEN  # characters this turn may still say

        def heard(piece: str) -> None:
            nonlocal first_words, said_something, new_request, room
            room -= len(piece)
            if room < 0:
                return  # running on: see MAX_SPOKEN
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

        def kept(text: str) -> str:
            """What was said of a reply that ran on: up to its last whole sentence."""
            if len(text) <= MAX_SPOKEN:
                return text
            cut = text[:MAX_SPOKEN]
            return cut[:max(cut.rfind(mark) for mark in ".?!") + 1] or cut

        use_tools()
        ran_on = room < 0 and not reply.tool_calls
        if ran_on:
            spoken[-1], room = kept(spoken[-1]), MAX_SPOKEN
        if (reply.stop == "end" and left_waiting(reply.text)) or ran_on:
            # "Un momento, lo miro", and the turn is over with nothing looked up. Measured:
            # one model did it in 11 of 435 answers. On the phone that is a dead line until
            # the caller speaks again, so the model is told once to do what it said. The
            # note is not the caller's: it is no evidence of who they are.
            reply = timed(self._conversation.send_user, STILL_WAITING)
            spoken.append(reply.text)
            usage, requests = usage + reply.usage, requests + 1
            use_tools()

        answer = " ".join(kept(part) for part in spoken if part)
        fallback = (CANNOT_HELP[self.language] if reply.stop == "refusal"
                    else "" if answer else DID_NOT_FOLLOW[self.language])
        if fallback:  # refused, cut off or empty: never go silent
            new_request = True
            heard(fallback)
            answer = fallback
        events = tuple(self.session.events[events_before:])
        if self._emergency_phone and self._emergency_phone in answer \
                and not self._emergency_told:
            self._emergency_told = True
            self.session.notices.append(notices.emergency(
                self._toolbox.now(), self.session.caller_number, text))
        if not events:
            self._before_last = before
        return Turn(answer, events, usage, requests, tuple(latencies), first_words)

    def take_back(self) -> bool:
        """Undo the caller's last line and the answer to it, as if neither had been said.

        For a voice line whose recogniser hands a line over and, a moment later, hands it
        over again written better: "Hola, buen día." and then "Hola, bon dia.". Only a turn
        that ran no tool can be taken back, and only once: a booking made stays made, and
        then the answer already given is the one to repeat. False when it cannot be done.
        """
        if self._before_last is None:
            return False
        mark, self.language, self._lines = self._before_last
        self._conversation.rewind(mark)
        self._before_last = None
        return True


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
        return Call(self._llm.start(self._system, context, SPECS), toolbox, hello,
                    lambda language: in_an_emergency(self._kb, now, language),
                    tuple(self._kb.clinic.languages), spoken_phone(self._kb.emergency.phone))
