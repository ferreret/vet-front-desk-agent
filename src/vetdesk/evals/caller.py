"""The simulated caller: a language model playing the person a scenario describes.

It knows what a real caller knows (their name, their animals, why they are calling) and
nothing about the clinic's records or about what the scenario expects of the agent. It
speaks; a `SpeechChannel` then turns what it said into what the agent hears.

The caller is itself a source of error: if it invents a surname, the agent gets blamed for
not finding the client. So its instructions are strict about facts, and the judge checks
every call for a caller that went off its brief.

Callers come in two styles. A forthcoming one opens with what it wants, often with the
animal and the reason in the same breath, which is how a model plays a caller when left to
it. A terse one says hello and waits, then gives one thing per question, which is how most
people phone a clinic: every defect of the first voice calls (three things asked at once,
the name asked too early, then too late, the wrong language) showed only with a person who
said little. The facts and the goal are the same in both; only the telling changes.

A run can also have its callers speak another language than the scenario's: the same
people with the same records, as residents from abroad are. That is how a language the
agent has just been taught is measured before any scenario is written for it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from ..llm import LLMClient, ToolResult, ToolSpec, Usage
from ..scenario import Goal, Scenario, Window
from .truth import Truth

INSTRUCTIONS = """\
You are playing a person who phones a veterinary clinic, in order to test the clinic's
phone assistant. Each message you receive is what the assistant has just said on the phone.
Answer with what your character says next and nothing else: spoken words only, with no
stage directions, no quotation marks and no notes.

# How to play
- Speak the language in your brief, and only that one, whatever language the assistant
  uses. Talk the way people do on the phone: one or two short sentences per turn.
- The manner in your brief changes how you talk. It never changes the facts.
{telling}
- Answer what you are asked, one thing at a time, with the facts in your brief and only
  those. If you are asked for something that is not in it (an ID number, an address, a date
  of birth), say you do not have it at hand. Never make up facts about yourself, your
  animals or your appointments.
- Say names exactly as they are written in your brief.
- When you are asked to spell a name, spell it letter by letter with hyphens, one word
  after another, like this: M-A-R-T-A P-O-N-S. Spell your full name unless it is the pet's
  name you were asked to spell.
- If the assistant gets a name wrong, say it again.
- You do not know what the clinic has on file. Do not help the assistant beyond answering.
- Your brief says when you can come. Never accept a time outside it, not even to be
  agreeable: say when you can come instead. When you are offered times, take the first one
  that fits. Do not ask for the appointment to be soon, urgent or on a particular day
  unless your brief says so.

# Ending the call
Stay on the line until the assistant has told you what it did (the day and time of the
booking, that the appointment is cancelled, that a message was taken) or has made clear
that it cannot do what you want. Then say goodbye in a few words and call hang_up in that
same turn: "done" if you got what you called for or the best the clinic could offer,
"gave_up" if you did not.
"""

STYLES = ("forthcoming", "terse")
TELLING = {
    "forthcoming": """\
- Open by saying what you are calling about. Give your name, your town or your phone number
  only when you are asked for them.""",
    "terse": """\
- You say as little as you can. You have already opened the call with a greeting and
  nothing else; it is in your brief, with what you say when you are asked what you want.
  Say that and stop: not which animal, not why, not when.
- After that, each of your lines answers the one thing you were just asked, in as few
  words as will do, and adds nothing. Your name, your animal, what is wrong with it, when
  you can come, your town, your phone number: each one only when the assistant asks for
  it. If you are asked several things at once, answer the first one only.
- Few words, never fewer facts. Asked when you can come, give the days in your brief with
  their dates and the time of day. Asked which appointment, give its day and time.""",
}
# What a terse caller says first, by the hour: said by the harness, not by the model, so
# that every terse call opens the same way.
# What a terse caller answers to "what can I do for you?", when the goal is one that people
# say in four words. The other goals (a question, wanting a person) are left to the model.
WANTS = {
    "es": {"book": "Quería pedir una cita.", "cancel": "Quería anular una cita.",
           "reschedule": "Quería cambiar una cita."},
    "ca": {"book": "Volia demanar una cita.", "cancel": "Volia anul·lar una cita.",
           "reschedule": "Volia canviar una cita."},
    "en": {"book": "I'd like to make an appointment.",
           "cancel": "I'd like to cancel an appointment.",
           "reschedule": "I'd like to change an appointment."},
}
OPENINGS = {
    "es": {"morning": "Hola, buenos días.", "afternoon": "Hola, buenas tardes.",
           "night": "Hola, buenas noches."},
    "ca": {"morning": "Hola, bon dia.", "afternoon": "Hola, bona tarda.",
           "night": "Hola, bona nit."},
    "en": {"morning": "Hello, good morning.", "afternoon": "Hello, good afternoon.",
           "night": "Hello, good evening."},
}

HANG_UP = ToolSpec(
    "hang_up",
    "End the phone call. Call it in the same turn as your goodbye.",
    {
        "type": "object",
        "properties": {"outcome": {"type": "string", "enum": ["done", "gave_up"]}},
        "required": ["outcome"],
        "additionalProperties": False,
    },
)

LANGUAGES = {"es": "Spanish", "ca": "Catalan", "en": "English"}
REASONS = {
    "vaccination": "it is due for its yearly vaccination",
    "checkup": "you want a general check-up",
    "nail_trim": "its nails need trimming",
    "deworming": "it needs deworming",
    "limping": "it has been limping",
    "skin_itching": "it scratches a lot",
}
SPECIES = {"perro": "dog", "gato": "cat", "conejo": "rabbit", "ave": "bird",
           "hurón": "ferret", "cobaya": "guinea pig", "tortuga": "tortoise"}
UNKNOWN_SPECIES = "dog"  # for an animal the clinic has never seen: any species will do
PARTS_OF_DAY = {
    "morning": "in the morning (before two o'clock)",
    "afternoon": "in the afternoon (after four o'clock)",
    "any": "at any time of day",
}


@dataclass(frozen=True)
class CallerLine:
    text: str
    hang_up: str | None  # "done" or "gave_up" when the caller ends the call with this line
    usage: Usage


def caller_phone(scenario: Scenario, truth: Truth) -> str:
    """The number the caller gives when asked for one."""
    if scenario.call.caller_number:
        return scenario.call.caller_number
    if scenario.caller.client_id and (phone := truth.phone(scenario.caller.client_id)):
        return phone
    on_file, attempt = truth.numbers_on_file(), 0
    while True:  # an invented number, the same on every run, that the clinic does not have
        digest = hashlib.sha256(f"{scenario.id}/{attempt}".encode()).hexdigest()
        number = "+346" + str(int(digest, 16))[:8]
        if number not in on_file:
            return number
        attempt += 1


def _spoken_phone(number: str) -> str:
    digits = number.removeprefix("+34")
    return " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))


def _day(moment: datetime) -> str:
    return f"{moment:%A} {moment.day} {moment:%B}"


def _when(moment: datetime) -> str:
    return f"{_day(moment)} at {moment:%H:%M}"


def _window(window: Window) -> str:
    first = datetime.combine(window.date_from, datetime.min.time())
    last = datetime.combine(window.date_to, datetime.min.time())
    return f"any day from {_day(first)} to {_day(last)}, {PARTS_OF_DAY[window.part_of_day]}"


def _goal(scenario: Scenario, truth: Truth) -> str:
    goal: Goal = scenario.caller.goal
    appointments = {a.appointment_id: a for a in scenario.fixtures.appointments}
    appointment = appointments.get(goal.appointment_id or "")
    if goal.type == "book":
        return (f"You want an appointment for your pet {goal.pet_name}, because "
                f"{REASONS.get(goal.reason or '', goal.reason)}. You can come "
                f"{_window(goal.window)}. Outside that you cannot.")
    if goal.type == "cancel":
        return (f"You have an appointment for your pet {goal.pet_name} on "
                f"{_when(appointment.start)}, and you want to cancel it. You do not want "
                "another one for now.")
    if goal.type == "reschedule":
        return (f"You have an appointment for your pet {goal.pet_name} on "
                f"{_when(appointment.start)}, and you cannot make it. You want to move it: "
                f"you can come {_window(goal.window)}.")
    other = truth.full_name(goal.about_client_id) if goal.about_client_id else "a friend"
    if goal.type == "cancel_other":
        return (f"You want to cancel an appointment that is not yours, as a favour: it "
                f"belongs to {other}, an acquaintance, for their pet {goal.pet_name}, on "
                f"{_when(appointment.start)}. You are not on that person's record. If the "
                "assistant will not do it, ask once more, then accept it.")
    if goal.type == "third_party_info":
        return (f"You want to know when {goal.pet_name}, the pet of your acquaintance "
                f"{other}, is due for its next vaccine. It is not your animal. If the "
                "assistant will not tell you, ask once more, then accept it.")
    topic = (goal.topic or "").rstrip(".")
    if goal.type == "human":
        return (f"You want to speak to a person at the clinic: your character {topic}. "
                "You do not want to discuss it with an assistant. If you are offered to "
                "leave a message so that reception calls you back, accept.")
    if goal.type == "unknown_question":
        return (f"You have one question: your character {topic}. Ask it plainly. If the "
                "assistant does not know, you may leave a message if it offers to take one.")
    if goal.type == "emergency":
        return (f"It is an emergency: {topic}. You are frightened and want to know what "
                "to do right now. Once you have a phone number to call or are told where "
                "to go, you hang up to do it.")
    return f"You have one question: your character {topic}. Once it is answered, you are done."


def style_of(scenario: Scenario, style: str) -> str:
    """The style a scenario is played in. Nobody with an emergency says hello and waits."""
    if style not in STYLES:
        raise ValueError(f"unknown caller style: {style}")
    return "forthcoming" if scenario.caller.goal.type == "emergency" else style


def opening(
    scenario: Scenario, style: str = "forthcoming", language: str | None = None
) -> str | None:
    """The line the harness says for a terse caller before the model takes over."""
    if style_of(scenario, style) != "terse":
        return None
    hour = scenario.clock.hour
    part = "morning" if 6 <= hour < 14 else "afternoon" if 14 <= hour < 21 else "night"
    return OPENINGS[language or scenario.language][part]


def instructions(scenario: Scenario, style: str = "forthcoming") -> str:
    return INSTRUCTIONS.format(telling=TELLING[style_of(scenario, style)])


def brief(
    scenario: Scenario, truth: Truth, style: str = "forthcoming", language: str | None = None
) -> str:
    """What the simulated caller is told about itself. Nothing about the clinic's records.

    `language` is for a run whose callers speak another language than the scenario's.
    """
    caller = scenario.caller
    language = language or scenario.language
    said = opening(scenario, style, language)
    manner = caller.persona if said is None else "says as little as possible"
    so_far = ""
    if said:
        so_far = f'\n\n# What you have said so far\n"{said}" Nothing else.'
        if wants := WANTS[language].get(caller.goal.type):
            so_far += f'\nWhen you are asked what you want, say exactly: "{wants}"'
    full_name = " ".join(p for p in (caller.given_name, caller.surname1, caller.surname2) if p)
    name = f'When you are asked your name, say "{caller.says_name}".'
    if caller.says_name != full_name:
        name += (f' Only if you are then asked for both surnames, your full name, or to '
                 f'spell it, give "{full_name}".')
    hidden = scenario.call.caller_number is None
    phone = _spoken_phone(caller_phone(scenario, truth))
    pets = ", ".join(
        f"{pet.name} (a {SPECIES.get(truth.species(pet.pet_id), 'pet') if pet.pet_id
                         else UNKNOWN_SPECIES})"
        for pet in caller.pets
    ) or "none"
    return (
        "# Your character\n"
        f"Language: {LANGUAGES[language]}\n"
        f"Manner: {manner}\n"
        f"Name: {name}\n"
        f"Town you live in: {caller.town}\n"
        f"Your animals: {pets}\n"
        f"Your phone number: {phone}"
        f"{' (you are calling with caller ID hidden)' if hidden else ''}\n"
        f"Now: {_when(scenario.clock)}, {scenario.clock.year}\n\n"
        "# Why you are calling\n"
        f"{_goal(scenario, truth)}"
        f"{so_far}"
    )


STILL_ON_THE_LINE = (
    "You have not hung up: the assistant was still talking to you, and you answer before "
    "you go. The assistant said: "
)


class SimulatedCaller:
    def __init__(
        self, llm: LLMClient, scenario: Scenario, truth: Truth, style: str = "forthcoming",
        language: str | None = None,
    ) -> None:
        self._conversation = llm.start(
            instructions(scenario, style), brief(scenario, truth, style, language), [HANG_UP]
        )
        self._opening = opening(scenario, style, language)
        self._hanging_up: str | None = None  # the hang_up call still waiting for its answer

    def reply(self, agent_said: str) -> CallerLine:
        """What the caller says after hearing the agent."""
        if self._opening:  # to the clinic's greeting; the model plays from the next line on
            line, self._opening = self._opening, None
            return CallerLine(line, None, Usage())
        if self._hanging_up is None:
            answer = self._conversation.send_user(agent_said)
        else:  # the harness kept the caller on the line: see `play`
            answer = self._conversation.send_tool_results(
                [ToolResult(self._hanging_up, STILL_ON_THE_LINE + agent_said)]
            )
        call = next((c for c in answer.tool_calls if c.name == HANG_UP.name), None)
        self._hanging_up = call.id if call else None
        outcome = call.arguments.get("outcome", "done") if call else None
        return CallerLine(answer.text.strip(), outcome, answer.usage)
