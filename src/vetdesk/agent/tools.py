"""The agent's tools, and the privacy barrier in front of them.

The model decides what to say; it does not decide what it is allowed to know. Every tool
that reads or writes a client's data checks, in code, that the identity resolver has
confirmed the caller. Until then no tool result contains a client's name, pets or
appointments, so there is nothing for the model to leak.

There is deliberately no tool to transfer a call: the agent cannot do it, so it must not
be able to promise it. `take_message` is what it has instead.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from ..identity import Evidence, IdentityResolver, Resolution
from ..identity.matching import SOUNDS_SAME, pet_grade
from ..identity.spelling import spelled_words, was_spelled
from ..kb import KnowledgeBase
from ..kb.model import WEEKDAYS_ES
from ..legacy.models import Client, Clinic
from ..legacy.normalize import fold, parse_phones
from ..llm import ToolCall, ToolResult, ToolSpec
from ..scheduling import Agenda, AgendaError, Appointment
from .spoken import say_ca, say_es


class ToolError(Exception):
    """A tool could not do what was asked. The message is written for the model."""


@dataclass(frozen=True)
class ToolEvent:
    name: str
    arguments: dict
    result: dict
    is_error: bool


@dataclass(frozen=True)
class Message:
    text: str
    contact_name: str
    contact_phone: str | None
    client_code: int | None


@dataclass
class CallSession:
    """What is known and what has been done during one call."""

    caller_number: str | None
    evidence: Evidence
    resolution: Resolution | None = None
    client: Client | None = None  # set once, when the resolver confirms the caller
    # What the caller has said, as far as it backs a claim of the model's: the words they
    # spelled out, and in how many of their lines each word came up.
    spelled: list[str] = field(default_factory=list)
    lines_with: Counter = field(default_factory=Counter)
    events: list[ToolEvent] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


NOT_CONFIRMED = (
    "The caller is not confirmed, so no client data is available. Use identify_client "
    "first. If it cannot confirm them, do not use this tool: offer an unverified booking "
    "or a message instead."
)
NOT_SPELLED = (
    "name_spelled is true, but that is not the name the caller spelled letter by letter. "
    "{spelled} Pass the name exactly as it was spelled, every word of it. If they spelled "
    "only part of it, or nothing, ask them to spell their full name."
)
NOT_REPEATED = (
    "pet_confirmed is true, but the caller has neither spelled that name nor said it "
    "twice. Ask them to repeat or spell the pet's name."
)
ASK = {
    "client_name": "Ask for their first name and both surnames.",
    "full_name": "One surname is not enough. Ask for both surnames.",
    "confirm_name": "The name was not understood well enough to use. Ask them to spell "
    "their full name, then call identify_client again with name_spelled=true.",
    "pet_name": "Ask for the name of one of their pets.",
    "confirm_pet": "The pet's name was not understood well enough. Ask them to repeat or "
    "spell it, then call identify_client again with pet_confirmed=true.",
    "town": "Ask which town they live in.",
}
UNCONFIRMED = (
    "There is nothing more to ask: the caller cannot be confirmed on this call. Do not "
    "mention any client data and do not say why. You can still answer general questions, "
    "book an appointment under the name and phone they give you, or take a message."
)
NOT_A_CLIENT = (
    "Nobody on file has this name. Treat the caller as not a client: no client data. You "
    "can still answer general questions, book an appointment under the name and phone "
    "they give you, or take a message. If they later spell their name, call "
    "identify_client again with name_spelled=true."
)

MAX_OFFERED = 6  # free times handed to the model at once
DAYS_OFFERED = 3  # days they are spread over

_NULLABLE_TEXT = {"type": ["string", "null"]}


def _schema(properties: dict) -> dict:
    # Strict schemas: every argument is listed and required; optional ones are nullable.
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SPECS = [
    ToolSpec(
        "identify_client",
        "Work out who is calling. Call it every time the caller gives you their name, a "
        "pet's name or their town, passing everything as you heard it; pass null for what "
        "you do not have. It tells you whether the caller is confirmed and, if not, the one "
        "thing to ask next. Never guess: only this tool can confirm a caller.",
        _schema({
            "name": {**_NULLABLE_TEXT, "description": "First name and surnames, as heard."},
            "name_spelled": {"type": "boolean",
                             "description": "True only if the caller spelled the name out."},
            "pet_name": {**_NULLABLE_TEXT, "description": "Name of one of their pets."},
            "pet_confirmed": {"type": "boolean",
                              "description": "True only if they repeated or spelled it."},
            "town": {**_NULLABLE_TEXT, "description": "The town they say they live in."},
        }),
    ),
    ToolSpec(
        "get_pets",
        "The confirmed caller's animals. Fails if the caller is not confirmed.",
        _schema({}),
    ),
    ToolSpec(
        "get_availability",
        "Free appointment times between two dates. Needs no identification.",
        _schema({
            "date_from": {"type": "string", "description": "First day, YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Last day, YYYY-MM-DD."},
            "part_of_day": {"type": "string", "enum": ["morning", "afternoon", "any"]},
        }),
    ),
    ToolSpec(
        "book_appointment",
        "Book one of the times returned by get_availability. For a confirmed caller the "
        "booking goes on their record. For anyone else it is taken under the name and "
        "phone they give (both required) and flagged for reception to verify.",
        _schema({
            "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, from get_availability."},
            "reason": {"type": "string", "description": "Why the animal is coming, briefly."},
            "pet_name": {"type": "string"},
            "contact_name": {**_NULLABLE_TEXT, "description": "Only for unconfirmed callers."},
            "contact_phone": {**_NULLABLE_TEXT, "description": "Only for unconfirmed callers."},
        }),
    ),
    ToolSpec(
        "list_appointments",
        "The confirmed caller's upcoming appointments. Fails if the caller is not confirmed.",
        _schema({}),
    ),
    ToolSpec(
        "cancel_appointment",
        "Cancel one of the confirmed caller's own appointments.",
        _schema({"appointment_id": {"type": "string"}}),
    ),
    ToolSpec(
        "reschedule_appointment",
        "Move one of the confirmed caller's own appointments to a free time.",
        _schema({
            "appointment_id": {"type": "string"},
            "new_start": {"type": "string", "description": "YYYY-MM-DDTHH:MM."},
        }),
    ),
    ToolSpec(
        "take_message",
        "Leave a message for reception, who will call back. Use it when the caller wants a "
        "person, or needs something you cannot do or do not know.",
        _schema({
            "message": {"type": "string", "description": "What the caller needs, in a sentence."},
            "contact_name": {"type": "string"},
            "contact_phone": {**_NULLABLE_TEXT,
                              "description": "Where to call back, if not the calling number."},
        }),
    ),
]


class Toolbox:
    def __init__(
        self,
        clinic: Clinic,
        kb: KnowledgeBase,
        agenda: Agenda,
        now: Callable[[], datetime],
        caller_number: str | None,
    ) -> None:
        self.clinic, self.kb, self.agenda, self.now = clinic, kb, agenda, now
        self.resolver = IdentityResolver(clinic)
        self.session = CallSession(caller_number, Evidence(caller_number=caller_number))

    def heard(self, text: str) -> None:
        """Take note of what the caller has just said, before the model answers it."""
        self.session.spelled += spelled_words(text)
        self.session.lines_with.update(set(fold(text).split()))

    def _vouched_for(self, name: str | None, spelled: bool, pet: str | None,
                     repeated: bool) -> None:
        """Refuse a claim the caller's own words do not back.

        `name_spelled` and `pet_confirmed` tell the resolver to stop doubting a name. The
        model sets them, and measured over 82 calls it vouched for names it had put back
        together wrong ("Rossellón" for R-O-S-S-E-L-L-Ó). So the claim is checked here,
        against what the caller actually said.
        """
        if spelled and name and not was_spelled(name, self.session.spelled):
            said = ", ".join(self.session.spelled)
            raise ToolError(NOT_SPELLED.format(
                spelled=f"They spelled: {said}." if said else "They have spelled nothing."))
        if repeated and pet:
            said_twice = all(self.session.lines_with[w] >= 2 for w in fold(pet).split())
            if not (said_twice or was_spelled(pet, self.session.spelled)):
                raise ToolError(NOT_REPEATED)

    def run(self, call: ToolCall) -> ToolResult:
        handler = getattr(self, f"_{call.name}", None)
        try:
            if handler is None:
                raise ToolError(f"there is no tool called {call.name}")
            result, failed = handler(**call.arguments), False
        except ToolError as error:
            result, failed = {"error": str(error)}, True
        except TypeError as error:  # the model sent arguments the tool does not take
            result, failed = {"error": f"bad arguments: {error}"}, True
        self.session.events.append(ToolEvent(call.name, call.arguments, result, failed))
        return ToolResult(call.id, json.dumps(result, ensure_ascii=False), failed)

    # --- identity -------------------------------------------------------------------------------

    def _identify_client(
        self,
        name: str | None,
        name_spelled: bool,
        pet_name: str | None,
        pet_confirmed: bool,
        town: str | None,
    ) -> dict:
        session = self.session
        if session.client is None:
            self._vouched_for(name, name_spelled, pet_name, pet_confirmed)
            evidence = session.evidence
            if name:
                same = name == evidence.client_name and evidence.name_verified
                evidence = replace(evidence, client_name=name, name_verified=name_spelled or same)
            if pet_name:
                same = pet_name == evidence.pet_name and evidence.pet_verified
                evidence = replace(evidence, pet_name=pet_name, pet_verified=pet_confirmed or same)
            if town:
                evidence = replace(evidence, town=town)
            session.evidence = evidence
            session.resolution = self.resolver.resolve(evidence)
            if session.resolution.decision == "resolved":
                session.client = session.resolution.client
        if session.client is not None:
            return {
                "status": "confirmed",
                "client_name": _display(session.client),
                "instructions": "The caller is confirmed. Their data and appointments are "
                "now available through the other tools.",
            }
        resolution = session.resolution
        if resolution.decision == "not_found":
            return {"status": "not_a_client", "instructions": NOT_A_CLIENT}
        if resolution.ask_for:
            return {"status": "need_more", "ask_for": resolution.ask_for,
                    "instructions": ASK[resolution.ask_for]}
        return {"status": "unconfirmed", "instructions": UNCONFIRMED}

    def _confirmed(self) -> Client:
        if self.session.client is None:
            raise ToolError(NOT_CONFIRMED)
        return self.session.client

    def _get_pets(self) -> dict:
        client = self._confirmed()
        animals = self.clinic.animals_of(client.code)
        # An animal filed under a name two clients share may be the other client's. It is
        # not handed to the model at all: telling it "do not bring these up" would make
        # the barrier a matter of prompt again.
        pets = [
            {"name": a.name, "species": a.species, "breed": a.breed, "deceased": a.deceased}
            for a in animals if len(a.owner_codes) == 1
        ]
        result: dict = {"pets": pets}
        if len(pets) < len(animals):
            result["note"] = (
                f"{len(animals) - len(pets)} more on file cannot be told apart from another "
                "client's animals, so they are not listed. If the caller names an animal "
                "that is not listed, that is fine: book it under the name they give."
            )
        return result

    # --- agenda ---------------------------------------------------------------------------------

    def _get_availability(self, date_from: str, date_to: str, part_of_day: str) -> dict:
        first, last = _date(date_from), _date(date_to)
        if last < first or (last - first).days > 31:
            raise ToolError("ask for a range of at most one month, with date_to after date_from")
        if part_of_day not in ("morning", "afternoon", "any"):
            raise ToolError("part_of_day must be morning, afternoon or any")
        slots = self.agenda.free_slots(first, last, part_of_day, limit=10_000)
        if not slots:
            return {"slots": [], "note": "Nothing free in that range. Offer other days."}
        if len(slots) <= MAX_OFFERED:
            return {"slots": [_when(slot) for slot in slots],
                    "note": "These are all the free times in that range."}
        # A sample spread over the first days. Handed the six earliest times, all on one
        # day, a model told callers that the rest of the week was full.
        by_day: dict[date, list[datetime]] = {}
        for slot in slots:
            by_day.setdefault(slot.date(), []).append(slot)
        days = list(by_day)[:DAYS_OFFERED]
        per_day = MAX_OFFERED // len(days)
        sample = [slot for day in days
                  for slot in by_day[day][::max(1, len(by_day[day]) // per_day)][:per_day]]
        return {
            "slots": [_when(slot) for slot in sample],
            "free_days": [f"{WEEKDAYS_ES[day.weekday()]} {day.isoformat()}" for day in by_day],
            "note": "A sample: offer two or three of these. Every day in free_days has more "
                    "free times than are shown, so never say a day or the week is full "
                    "because it is not in the sample. To see one day, ask again for that day.",
        }

    def _book_appointment(
        self,
        start: str,
        reason: str,
        pet_name: str,
        contact_name: str | None,
        contact_phone: str | None,
    ) -> dict:
        when, client = _moment(start), self.session.client
        try:
            if client is not None:
                names = [a.name for a in self.clinic.animals_of(client.code)]
                grade, matched = pet_grade(pet_name, names, verified=False)
                known = matched if grade >= SOUNDS_SAME else None
                animal = next(
                    (a for a in self.clinic.animals_of(client.code) if a.name == known), None
                )
                booked = self.agenda.book(
                    when, reason, known or pet_name,
                    client_code=client.code, animal_code=animal.code if animal else None,
                )
            else:
                if not contact_name or not contact_phone:
                    raise ToolError(
                        "This caller is not confirmed: contact_name and contact_phone are "
                        "required. Ask for them, repeat the phone back, and call again."
                    )
                phones, _ = parse_phones(contact_phone)
                if not phones:
                    raise ToolError("contact_phone is not a valid phone number. Ask again.")
                booked = self.agenda.book(
                    when, reason, pet_name, contact_name=contact_name, contact_phone=phones[0]
                )
        except AgendaError as error:
            raise ToolError(f"{error}. Check get_availability and offer another time.") from error
        result = {"status": "booked", **_summary(booked)}
        if not booked.verified:
            result["note"] = ("Booked under the caller's word. Tell them reception will "
                              "confirm the details when they arrive or by phone.")
        return result

    def _own(self, appointment_id: str) -> Appointment:
        client = self._confirmed()
        appointment = self.agenda.get(appointment_id)
        if appointment is None or appointment.client_code != client.code \
                or appointment.status != "booked":
            # The same answer whether it does not exist or belongs to someone else.
            raise ToolError("This caller has no such appointment. Use list_appointments.")
        return appointment

    def _list_appointments(self) -> dict:
        client = self._confirmed()
        return {"appointments": [_summary(a) for a in self.agenda.for_client(client.code)]}

    def _cancel_appointment(self, appointment_id: str) -> dict:
        self._own(appointment_id)
        return {"status": "cancelled", **_summary(self.agenda.cancel(appointment_id))}

    def _reschedule_appointment(self, appointment_id: str, new_start: str) -> dict:
        self._own(appointment_id)
        try:
            moved = self.agenda.reschedule(appointment_id, _moment(new_start))
        except AgendaError as error:
            raise ToolError(f"{error}. Check get_availability and offer another time.") from error
        return {"status": "rescheduled", **_summary(moved)}

    # --- handoff --------------------------------------------------------------------------------

    def _take_message(self, message: str, contact_name: str, contact_phone: str | None) -> dict:
        phones, _ = parse_phones(contact_phone or "")
        phone = phones[0] if phones else self.session.caller_number
        if phone is None:
            raise ToolError("There is no number to call back: the caller ID is hidden. "
                            "Ask for a phone number, repeat it back, and call again.")
        client = self.session.client
        self.session.messages.append(
            Message(message, contact_name, phone, client.code if client else None)
        )
        return {"status": "message_taken",
                "instructions": "Tell the caller reception will call them back. Do not "
                "promise when, and do not say you are transferring the call."}


def _display(client: Client) -> str:
    name = client.name.display() if client.name else client.raw_name
    return name.title() if name.isupper() else name


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ToolError(f"{text!r} is not a date in YYYY-MM-DD format") from error


def _moment(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError as error:
        raise ToolError(f"{text!r} is not a time in YYYY-MM-DDTHH:MM format") from error


def _when(moment: datetime) -> dict:
    # `start` is for the tools; say_es and say_ca are for the caller's ears.
    return {"start": moment.strftime("%Y-%m-%dT%H:%M"), "say_es": say_es(moment),
            "say_ca": say_ca(moment)}


def _summary(appointment: Appointment) -> dict:
    return {"appointment_id": appointment.appointment_id, **_when(appointment.start),
            "pet_name": appointment.pet_name, "reason": appointment.reason}
