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
from datetime import date, datetime, timedelta

from .. import notices
from ..identity import Evidence, IdentityResolver, Resolution
from ..identity.matching import SOUNDS_SAME, pet_grade
from ..identity.resolver import MISTYPED_ON_FILE
from ..identity.spelling import SPELLED_WORD, spelled_words, was_spelled
from ..kb import KnowledgeBase
from ..kb.model import WEEKDAYS_ES
from ..legacy.models import Client, Clinic
from ..legacy.normalize import fold, fold_any, osa_distance, parse_phones
from ..llm import ToolCall, ToolResult, ToolSpec
from ..scheduling import Agenda, AgendaError, Appointment
from ..spoken import say
from .prompt import THROUGH


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
    # The language the call is going on in: what the tools hand over to be said (a day and
    # a time in words) is in it.
    language: str = "es"
    # What the caller has said, as far as it backs a claim of the model's: the words they
    # spelled out, and in how many of their lines each word came up.
    spelled: list[str] = field(default_factory=list)
    lines_with: Counter = field(default_factory=Counter)
    # Every word the caller has said, in whatever alphabet: what a reason for a visit is
    # checked against. The names above are compared in Latin letters, as they are on file.
    said: set[str] = field(default_factory=set)
    # How many lines the caller has said, and at which of them each of their appointments
    # was read out to the model: one is only cancelled or moved after the caller has
    # heard which it is and has spoken again.
    lines: int = 0
    told: dict[str, int] = field(default_factory=dict)
    # What reception has to hear about this call: see `notices`. Whoever carries the call
    # takes them from here and sends them on.
    notices: list[notices.Notice] = field(default_factory=list)
    # Set when the call is to be put through to a person: the line for whoever picks up.
    # Whoever carries the call does the putting through; here it is only asked for.
    transfer: str | None = None
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
NOT_SAID = {
    "name": "The caller has not said that name ({missing} was never said). Pass the name "
    "exactly as you heard it, even if it looks misheard: do not correct it. If it may be "
    "wrong, ask them to spell it.",
    "pet_name": "The caller has not said that pet's name ({missing} was never said). Pass "
    "it exactly as you heard it, without correcting it, or ask them to repeat or spell it.",
    "town": "The caller has not said that town. Pass a town only as the caller said it, "
    "and null until they have. If the town is needed, ask which town they live in.",
}
NO_REASON = (
    "The caller has not said what the visit is for: nothing in '{reason}' is in their own "
    "words. Do not make a reason up. If they did tell you, call again with what they said, "
    "in their words. If they did not, ask them now what is wrong or what the visit is for, "
    "and book once they have answered."
)
# Words that say nothing about why an animal is coming: asking for the appointment, and
# naming the animal. A reason made only of these, or of words the caller never used, is
# the model's own ("Revisió", "Visita general"), not the caller's.
_NOT_A_REASON = frozenset("""
    cita citas visita visitas consulta hora turno animal animals animales mascota mascotes
    mascotas para per pel pels amb con una uns unes unos unas del dels els les los las que
    perro perra perrito perrita gato gata gatito gatita conejo coneja hamster huron cobaya
    ave pajaro tortuga loro gos gossa gosset gosseta gat gatet gateta conill conilla fura
    ocell cobaia quiero queria vull volia voldria pedir demanar reservar agendar necesita
    necessita tiene general appointment visit dog cat rabbit pets want would like need book
    the for with and termin besuch tier haustier hund katze mein meine meinen mochte brauche
    einen fur und mit rendez vous visite chien chat mon voudrais besoin pour avec
    appuntamento visita cane gatto mio vorrei prenotare
    запись прием визит собака собаки собаку кошка кошки кошку питомец питомца для моей
    моего моя мой хочу хотел хотела записаться""".split())
_STEM = 4  # "vacunarlo" and "vacunación" are the same reason: the first letters decide


def _stems(words) -> set[str]:
    return {word[:_STEM] for word in words if len(word) >= 3 and word not in _NOT_A_REASON}


OWN_NUMBER = (
    "That is one of the clinic's own numbers, not the caller's. Ask the caller for a phone "
    "number where reception can reach them, repeat it back, and call again."
)
NOT_FROM_THEIR_PHONE = (
    "Appointments can only be cancelled or moved on a call from a phone on the caller's "
    "record, and this call is not. Tell them you cannot do it from this number, and offer "
    "to take a message so that reception calls them back (take_message)."
)
ASK = {
    "client_name": "Ask for their first name and both surnames.",
    "full_name": "One surname is not enough. Ask for both surnames.",
    "confirm_name": "The name was not understood well enough to use. Ask them to spell "
    "their full name, then call identify_client again with name_spelled=true.",
    "pet_name": "Ask for the name of one of their pets.",
    "confirm_pet": "The pet's name was not understood well enough. Ask them to repeat or "
    "spell it, then call identify_client again with what they say.",
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
            "pet_name": {**_NULLABLE_TEXT,
                         "description": "Name of one of their pets, as heard."},
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
        "Cancel one of the confirmed caller's own appointments. Only on a call from a phone "
        "on their record.",
        _schema({"appointment_id": {"type": "string"}}),
    ),
    ToolSpec(
        "reschedule_appointment",
        "Move one of the confirmed caller's own appointments to a free time. Only on a call "
        "from a phone on their record.",
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
        can_transfer: bool = False,
    ) -> None:
        self.clinic, self.kb, self.agenda, self.now = clinic, kb, agenda, now
        self.can_transfer = can_transfer  # whether a person can take this call right now
        self.resolver = IdentityResolver(clinic)
        self.session = CallSession(caller_number, Evidence(caller_number=caller_number))

    def heard(self, text: str) -> None:
        """Take note of what the caller has just said, before the model answers it."""
        self.session.spelled += spelled_words(text)
        self.session.lines_with.update(set(fold(text).split()))
        self.session.said.update(fold_any(text).split())
        self.session.lines += 1

    def _vouched_for(self, name: str | None, spelled: bool, pet: str | None,
                     town: str | None) -> None:
        """Refuse a claim the caller's own words do not back.

        `name_spelled` tells the resolver to stop doubting a name. The model sets it, and
        measured over 82 calls it vouched for names it had put back together wrong
        ("Rossellón" for R-O-S-S-E-L-L-Ó). So the claim is checked here, against what the
        caller actually said.

        The evidence itself is checked too: a name, a pet's name or a town counts only in
        the caller's own words. Two models have been measured filling the town in with the
        clinic's own, taken from its address, and one turning a misheard "Yoaquín" into
        "Joaquín", which the resolver then took for a name heard clearly.
        """
        if spelled and name and not was_spelled(name, self.session.spelled):
            said = ", ".join(self.session.spelled)
            raise ToolError(NOT_SPELLED.format(
                spelled=f"They spelled: {said}." if said else "They have spelled nothing."))
        spelt = {fold(word) for word in self.session.spelled}
        for which, value in (("name", name), ("pet_name", pet), ("town", town)):
            if not value or was_spelled(value, self.session.spelled):
                continue
            missing = [w for w in fold(value).split()
                       if not self.session.lines_with[w] and w not in spelt]
            if missing:
                raise ToolError(NOT_SAID[which].format(missing=", ".join(missing)))

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
        # Not every provider sends a field it has nothing for. Left out means not said,
        # which is the safe reading; an error here was what made a model fill in the town.
        name: str | None = None,
        name_spelled: bool = False,
        pet_name: str | None = None,
        town: str | None = None,
    ) -> dict:
        session = self.session
        if session.client is None:
            name, pet_name, town = given(name), given(pet_name), given(town)
            if name and pet_name and set(fold(pet_name).split()) <= set(fold(name).split()) \
                    and not all(session.lines_with[w] >= 2 for w in fold(pet_name).split()):
                # The caller's own given name handed over as their pet's ("Carme Llull",
                # pet "Carme"). Said once, it was the person's: kept as a pet's name, the
                # caller was never asked for one and could not be confirmed.
                pet_name = None
            if name:
                # A model may hand the spelling over as it came: "X-I-S-C-A R-U-I-Z".
                name = SPELLED_WORD.sub(lambda letters: letters.group().replace("-", ""), name)
                name_spelled = name_spelled or was_spelled(name, session.spelled)
            self._vouched_for(name, name_spelled, pet_name, town)
            # Whether a pet's name was repeated or spelled is read off the caller's words,
            # not asked of the model: one model said yes on first hearing in half its calls.
            pet_confirmed = bool(pet_name) and (
                all(session.lines_with[w] >= 2 for w in fold(pet_name).split())
                or was_spelled(pet_name, session.spelled))
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
                if session.resolution.why.startswith(MISTYPED_ON_FILE):
                    # Identified all the same; the clinic is told, to mend the record.
                    notice = notices.record_mistyped(session.client.raw_name,
                                                     evidence.client_name)
                    if notice not in session.notices:
                        session.notices.append(notice)
            elif session.resolution.decision == "not_found" and evidence.name_verified:
                self._misspelt_on_file(evidence.client_name)
        if session.client is not None:
            instructions = ("The caller is confirmed. Their data and appointments are now "
                            "available through the other tools.")
            if not self._on_their_phone():
                instructions += (" This call is not from a phone on their record, so their "
                                 "appointments cannot be cancelled or moved on it: if they "
                                 "ask for that, take a message for reception instead.")
            return {"status": "confirmed", "client_name": _display(session.client),
                    "instructions": instructions}
        resolution = session.resolution
        if resolution.decision == "not_found":
            return {"status": "not_a_client", "instructions": NOT_A_CLIENT}
        if resolution.ask_for:
            return {"status": "need_more", "ask_for": resolution.ask_for,
                    "instructions": ASK[resolution.ask_for]}
        return {"status": "unconfirmed", "instructions": UNCONFIRMED}

    def _misspelt_on_file(self, spelled: str) -> None:
        """Tell reception when a record on the calling number is one letter from a name.

        A caller spells "Diego Esteve Planas" from the phone of "Esteve Planas, Deigo". The
        resolver does not forgive a given name a letter (a brother is a letter away too),
        so the caller is served as nobody on file. But the clinic can be told that the
        record looks misspelt. The model is told nothing: it is no evidence of who calls.
        """
        said = fold(spelled).split()
        for client in self.clinic.clients_by_phone(self.session.caller_number):
            name = client.name
            if name is None or len(said) < 2:
                continue
            on_file = [fold(part) for part in (name.given, name.surname1, name.surname2) if part]
            apart = [osa_distance(a, b, 1) for a, b in zip(said, on_file, strict=False)]
            if len(said) == len(on_file) and sorted(apart) == [0] * (len(apart) - 1) + [1]:
                notice = notices.record(client.raw_name, spelled, self.session.caller_number)
                if notice not in self.session.notices:
                    self.session.notices.append(notice)

    def _client_name(self) -> str | None:
        return self.session.client.raw_name if self.session.client else None

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
        today = self.now().date()
        if last < today:
            # Measured: a model asked for last year's dates, was told nothing was free, and
            # told a caller the week was full. An empty answer is not the place to hide that.
            raise ToolError(
                f"Those dates are in the past. Today is {WEEKDAYS_ES[today.weekday()]} "
                f"{today.isoformat()}: ask again with the dates the caller means, this year.")
        slots = self.agenda.free_slots(first, last, part_of_day, limit=10_000)
        # A day asked for on which the clinic is closed. Without this the model saw a day
        # with no free time and no reason for it; offered a Monday that was a holiday, the
        # day was simply missing, and a caller who asked for it was told nothing.
        days = (first + timedelta(days=n) for n in range((last - first).days + 1))
        closed = [f"{WEEKDAYS_ES[day.weekday()]} {day.isoformat()}: {found.name}"
                  for day in days if (found := self.kb.closed_on(day)) and day >= today]
        shut = ({"closed": closed, "closed_note": "The clinic is closed on these days. If the "
                 "caller asked for one of them, tell them so before offering others."}
                if closed else {})
        if not slots:
            return {"slots": [], "note": "Nothing free in that range. Offer other days.", **shut}
        if len(slots) <= MAX_OFFERED:
            return {"slots": [_when(slot, self.session.language) for slot in slots],
                    "note": "These are all the free times in that range.", **shut}
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
            "slots": [_when(slot, self.session.language) for slot in sample],
            "free_days": [f"{WEEKDAYS_ES[day.weekday()]} {day.isoformat()}" for day in by_day],
            "note": "A sample: offer two or three of these. Every day in free_days has more "
                    "free times than are shown, so never say a day or the week is full "
                    "because it is not in the sample. To see one day, ask again for that day.",
            **shut,
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
        self._reason_given(reason, pet_name)
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
                phone = self._callback(contact_phone)
                if phone is None:
                    raise ToolError("contact_phone is not a valid phone number. Ask again.")
                booked = self.agenda.book(
                    when, reason, pet_name, contact_name=contact_name, contact_phone=phone
                )
        except AgendaError as error:
            raise ToolError(f"{error}. Check get_availability and offer another time.") from error
        self.session.notices.append(notices.booked(booked, self._client_name()))
        result = {"status": "booked", **self._summary(booked)}
        if not booked.verified:
            result["note"] = ("Booked under the caller's word. Tell them reception will "
                              "confirm the details when they arrive or by phone.")
        return result

    def _summary(self, appointment: Appointment) -> dict:
        return {"appointment_id": appointment.appointment_id,
                **_when(appointment.start, self.session.language),
                "pet_name": appointment.pet_name, "reason": appointment.reason}

    def _reason_given(self, reason: str, pet_name: str) -> None:
        """Refuse a reason for the visit that the caller never gave.

        Heard on a call: asked what the visit was for, the caller corrected the kind of
        animal instead ("no és un gosset, és un hàmster"). The model took that for an
        answer, went on, and booked with a reason of its own. Repeated by text, every
        booking made without the caller's reason carried an invented one: "Revisió",
        "Visita general". Reception reads that reason, so it has to be the caller's: one
        word of it, at least, that they said and that is neither the animal nor the asking.
        """
        animal = set(fold_any(pet_name).split())
        said = _stems(word for word in self.session.said if word not in animal)
        if not _stems(word for word in fold_any(reason).split() if word not in animal) & said:
            raise ToolError(NO_REASON.format(reason=reason))

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
        upcoming = self.agenda.for_client(client.code)
        for appointment in upcoming:
            self.session.told.setdefault(appointment.appointment_id, self.session.lines)
        return {"appointments": [self._summary(a) for a in upcoming]}

    def _on_their_phone(self) -> bool:
        """Whether the call comes from a phone on the confirmed caller's record."""
        client = self.session.client
        on_phone = self.clinic.clients_by_phone(self.session.caller_number)
        return client is not None and any(c.code == client.code for c in on_phone)

    def _may_change(self, appointment_id: str) -> None:
        """Cancelling and moving need the phone as well as the name.

        A name, a pet and a town are things a friend knows. Measured: a caller said the
        appointment was a friend's, the model passed the friend's details as the caller's,
        the resolver confirmed the friend and the appointment was cancelled. What cannot be
        undone therefore asks for something the caller has, not only something they know.
        """
        self._confirmed()
        if not self._on_their_phone():
            raise ToolError(NOT_FROM_THEIR_PHONE)
        self._own(appointment_id)

    def _heard_which(self, appointment_id: str, doing: str) -> None:
        """Refuse to cancel or move an appointment the caller has not been told about.

        Heard on the first call from a real phone: "quería anular una cita", a name, and
        the one appointment on the record was cancelled in the same breath. It was the
        right one; with two on the record, or a caller who meant to move it, it would not
        have been. The caller hears which appointment it is, and says so, first.
        """
        if self.session.told.get(appointment_id, self.session.lines) >= self.session.lines:
            raise ToolError(
                f"Not yet. First tell the caller which appointment this is, with its day and "
                f"time and the animal, and ask whether that is the one to {doing}. Call this "
                f"tool again once they have said yes.")

    def _cancel_appointment(self, appointment_id: str) -> dict:
        self._may_change(appointment_id)
        self._heard_which(appointment_id, "cancel")
        gone = self.agenda.cancel(appointment_id)
        self.session.notices.append(notices.cancelled(gone, self._client_name()))
        return {"status": "cancelled", **self._summary(gone)}

    def _reschedule_appointment(self, appointment_id: str, new_start: str) -> dict:
        self._may_change(appointment_id)
        self._heard_which(appointment_id, "move")
        before = self.agenda.get(appointment_id).start
        try:
            moved = self.agenda.reschedule(appointment_id, _moment(new_start))
        except AgendaError as error:
            raise ToolError(f"{error}. Check get_availability and offer another time.") from error
        self.session.notices.append(notices.moved(moved, before, self._client_name()))
        return {"status": "rescheduled", **self._summary(moved)}

    # --- handoff --------------------------------------------------------------------------------

    def _callback(self, contact_phone: str | None) -> str | None:
        """The number given to reach the caller on, unless it is the clinic's own.

        Measured: a caller on a hidden number left a message, the model filled in the
        clinic's phone as theirs and told them reception would call back.
        """
        phones, _ = parse_phones(contact_phone or "")
        if phones and phones[0] in (self.kb.clinic.phone, self.kb.emergency.phone):
            raise ToolError(OWN_NUMBER)
        return phones[0] if phones else None

    def _transfer_to_reception(self, summary: str) -> dict:
        if not self.can_transfer:
            raise ToolError("There is no tool called transfer_to_reception on this call: "
                            "nobody can take it. Say the set phrase for when they want a "
                            "person and take a message.")
        session = self.session
        if session.transfer is None:
            session.transfer = summary
            session.notices.append(notices.put_through(
                summary, self._client_name(), session.caller_number))
        return {"status": "putting_through", "say": THROUGH[session.language],
                "instructions": "The call is being put through now. Say exactly what `say` "
                                "holds, and nothing else."}

    def _take_message(self, message: str, contact_name: str, contact_phone: str | None) -> dict:
        phone = self._callback(contact_phone) or self.session.caller_number
        if phone is None:
            raise ToolError("There is no number to call back: the caller ID is hidden. "
                            "Ask for a phone number, repeat it back, and call again.")
        client = self.session.client
        self.session.messages.append(
            Message(message, contact_name, phone, client.code if client else None)
        )
        self.session.notices.append(
            notices.message(message, contact_name, phone, self._client_name()))
        return {"status": "message_taken",
                "instructions": "Tell the caller reception will call them back. Do not "
                "promise when, and do not say you are transferring the call."}


# Offered to the model only on a call that can be put through: see `Toolbox.can_transfer`.
TRANSFER = ToolSpec(
    "transfer_to_reception",
    "Put the caller through to a person at the clinic. Only when the caller wants to speak "
    "to a person. The call leaves you: there is nothing more to do afterwards.",
    _schema({
        "summary": {"type": "string",
                    "description": "One line for whoever picks up, in Spanish: who is "
                                   "calling, if they said, and what they want."},
    }),
)


def given(value: str | None) -> str | None:
    """What a model passed for a field, with its ways of saying "nothing" read as nothing."""
    if value is None or value.strip().lower() in ("", "null", "none"):
        return None
    return value.strip()


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


def _when(moment: datetime, language: str) -> dict:
    # `start` is for the tools; `say` is for the caller's ears, in the language of the call.
    # One language and not all of them: seven ways of saying each of six times is a long
    # answer to read, and a model once picked the Spanish words for a caller in Catalan.
    return {"start": moment.strftime("%Y-%m-%dT%H:%M"), "say": say(moment, language)}
