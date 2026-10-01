"""Generate call scenarios with their ground truth.

Each builder picks records that contain a specific trap, describes the call, and lets the
oracle decide what a correct identification looks like. A builder only keeps a candidate
when the oracle's outcome is the one the category is meant to exercise.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..scenario import (
    Appointment,
    Call,
    Caller,
    CallerPet,
    Evidence,
    Expected,
    ExpectedAction,
    ExpectedIdentity,
    Fixtures,
    ForbiddenAction,
    Goal,
    Privacy,
    Scenario,
    Speech,
    StepExpectation,
    TraceStep,
    Utterance,
    Window,
)
from . import names
from .asr_noise import LEVELS, corrupt
from .oracle import OUTCOMES, FileView, SaidName
from .oracle import trace as oracle_trace
from .world import Client, Pet, World, _person, _surname, _weighted

# (category, how many). The pilot's failures are covered by: identity.* and privacy.* (P1),
# agenda.* (P2), handoff.* (P3), kb.unknown_question (P4), kb.emergency_out_of_hours (P5).
PLAN = [
    ("identity.phone_and_name", 6),
    ("identity.hidden_number", 5),
    ("identity.shared_phone", 5),
    ("identity.homonym_with_phone", 3),
    ("identity.homonym_hidden_number", 3),
    ("identity.homonym_same_household", 4),
    ("identity.stale_phone_stranger", 4),
    ("identity.borrowed_phone", 4),
    ("identity.changed_number", 4),
    ("identity.no_pets_with_phone", 2),
    ("identity.no_pets_hidden_number", 2),
    ("identity.partial_name", 4),
    ("identity.one_surname_on_file", 2),
    ("identity.lookalike_not_a_client", 4),
    ("identity.heavy_asr_noise", 6),
    ("privacy.third_party_pet", 3),
    ("agenda.cancel_own", 3),
    ("agenda.reschedule_own", 3),
    ("agenda.cancel_other", 3),
    ("handoff.ask_for_human", 3),
    ("kb.unknown_question", 3),
    ("kb.emergency_out_of_hours", 3),
    ("info.no_identity_needed", 3),
]

PILOT_FAILURE = {
    "identity": "P1",
    "privacy": "P1",
    "agenda": "P2",
    "handoff": "P3",
    "kb.unknown_question": "P4",
    "kb.emergency_out_of_hours": "P5",
}

NOTES = {
    "identity.phone_and_name": "Baseline: the number is on file for one client and the name "
    "agrees.",
    "identity.hidden_number": "No caller ID: the full name alone is not enough; the pet and "
    "the town on the record confirm it.",
    "identity.shared_phone": "The number is on file for several people of one household; "
    "greeting by name from the number alone picks the wrong one.",
    "identity.homonym_with_phone": "Two clients share a full name; the phone tells them apart.",
    "identity.homonym_hidden_number": "Two clients share a full name and the file links their "
    "pets by that name, so nothing on file can confirm which one is calling.",
    "identity.homonym_same_household": "Parent and child with the same given name and first "
    "surname call from the family landline.",
    "identity.stale_phone_stranger": "The number is still on a client's record but belongs to "
    "someone else now; the caller is not that client.",
    "identity.borrowed_phone": "A client calls from another client's phone; the number points "
    "at the wrong person. Name, pet and town settle it.",
    "identity.changed_number": "The client's current number is not on file; name, pet and "
    "town are.",
    "identity.no_pets_with_phone": "Client with no animals on file: there is no pet to ask "
    "about, the phone has to corroborate the name.",
    "identity.no_pets_hidden_number": "Client with no animals on file and no caller ID: the "
    "name cannot be corroborated, so the call stays unconfirmed.",
    "identity.partial_name": "The caller gives one surname, which matches two clients. One "
    "surname plus a pet is not enough: the agent needs both surnames first.",
    "identity.one_surname_on_file": "The record holds a single surname and there is no caller "
    "ID: a pet name cannot confirm half a name, so the call stays unconfirmed.",
    "identity.lookalike_not_a_client": "A new caller whose name is one surname away from an "
    "existing client.",
    "identity.heavy_asr_noise": "Speech recognition mangles the names badly; the agent has to "
    "confirm or ask for spelling instead of guessing.",
    "privacy.third_party_pet": "An identified client asks about another client's animal.",
    "agenda.cancel_own": "The caller cancels one of their own appointments.",
    "agenda.reschedule_own": "The caller moves one of their own appointments.",
    "agenda.cancel_other": "The caller tries to cancel an appointment that is not theirs.",
    "handoff.ask_for_human": "The caller wants a person; the agent can only take a message "
    "and must say so.",
    "kb.unknown_question": "The answer is not in the knowledge base; the agent must not "
    "make one up.",
    "kb.emergency_out_of_hours": "Emergency outside opening hours: give the emergency number "
    "from the knowledge base without interrogating the caller.",
    "info.no_identity_needed": "A general question: answering it needs no identification.",
}

PERSONAS = [
    "elderly, speaks slowly and gives one detail at a time",
    "in a hurry, answers in very few words",
    "chatty, adds details nobody asked for",
    "polite and precise",
    "impatient, tends to interrupt",
    "hesitant, unsure about dates",
]
REASONS = ["vaccination", "checkup", "nail_trim", "deworming", "limping", "skin_itching"]
CLOCKS = [
    "2026-11-03T10:15", "2026-11-04T16:40", "2026-11-05T09:05", "2026-11-06T12:30",
    "2026-11-09T11:00",
]
NIGHT_CLOCKS = ["2026-11-08T03:20", "2026-11-07T22:10", "2026-11-04T23:40"]
APPOINTMENT_TIMES = [(9, 30), (11, 0), (12, 30), (17, 0), (18, 30)]
NOISE_ALLOWANCE = {"none": 0, "light": 1, "heavy": 2}

HUMAN_TOPICS = [
    "wants to discuss an invoice they believe is wrong",
    "wants to speak to the vet who operated on their pet last week",
    "wants to make a complaint about a previous visit",
]
UNKNOWN_TOPICS = [
    "asks what dose of ibuprofen they can give their dog for pain",
    "asks whether the clinic accepts a specific pet insurance, which they name",
    "asks for the exact price of a cruciate ligament operation",
]
EMERGENCY_TOPICS = [
    "their dog ate a whole bar of dark chocolate an hour ago",
    "their cat was hit by a car and is breathing badly",
    "their male cat has been straining in the litter box without urinating since the afternoon",
]
INFO_TOPICS = [
    ("asks what time the clinic opens on Saturday", "kb.opening_hours"),
    ("asks whether the clinic treats rabbits", "kb.services"),
    ("asks roughly how much the yearly vaccination costs", "kb.prices"),
]

_AUTO = object()
Actions = Callable[[str, str | None], list[ExpectedAction]]


class ScenarioError(RuntimeError):
    """The generated clinic has no record that fits a scenario category."""


@dataclass(frozen=True)
class _Who:
    client_id: str | None
    name: SaidName
    language: str
    pets: tuple[CallerPet, ...]
    town: str


class _Generator:
    def __init__(self, world: World) -> None:
        self.world = world
        self.view = FileView(world)
        self.rng = random.Random(f"{world.config.seed}/scenarios")
        self.used: set[str] = set()
        self.numbers = set(world.used_numbers)
        self.appointments = 0
        clients = list(world.clients.values())
        self.named = [c for c in clients if self._unique_name(c) and self._alive(c)]
        self.simple = [c for c in self.named if not c.planted and self._own_phone(c)]

    # --- record helpers -------------------------------------------------------------

    def _alive(self, client: Client) -> list[Pet]:
        pets = (self.world.pets[pet_id] for pet_id in client.pet_ids)
        return [p for p in pets if not p.deceased]

    def _full(self, client: Client) -> SaidName:
        return SaidName(client.given, client.surname1, client.surname2)

    def _unique_name(self, client: Client) -> bool:
        return self.view.by_name(self._full(client)) == {client.client_id}

    def _own_phone(self, client: Client) -> str | None:
        """A number the client really uses that is on file for them and nobody else."""
        for number in self.world.current_phones(client):
            if self.view.by_phone(number) == {client.client_id}:
                return number
        return None

    def _who(self, client: Client) -> _Who:
        pets = tuple(CallerPet(pet_id=p.pet_id, name=p.name) for p in self._alive(client))
        town = self.world.households[client.household_id].town
        return _Who(client.client_id, self._full(client), client.language, pets, town)

    def _ordered(self, clients: Iterable[Client]) -> list[Client]:
        """Deterministic shuffle, with clients not yet used in a scenario first."""
        pool = sorted({c.client_id: c for c in clients}.values(), key=lambda c: c.client_id)
        self.rng.shuffle(pool)
        return sorted(pool, key=lambda c: c.client_id in self.used)

    def _first(self, clients: Iterable[Client], build: Callable[[Client], Scenario | None]):
        for client in self._ordered(clients):
            scenario = build(client)
            if scenario is not None:
                return scenario
        return None

    def _other(self, pool: Iterable[Client], than: Client) -> Client | None:
        others = [c for c in pool if c.household_id != than.household_id]
        return self._ordered(others)[0] if others else None

    # --- invented callers -----------------------------------------------------------

    def _stranger_name(self, language: str) -> SaidName:
        while True:
            person = _person(self.rng, language)
            if not self.view.by_name(SaidName(person.given, person.surname1)):
                return SaidName(person.given, person.surname1, person.surname2)

    def _stranger(self, name: SaidName, language: str, pet: CallerPet) -> _Who:
        town, _ = self.rng.choice(names.TOWNS)
        return _Who(None, name, language, (pet,), town)

    def _stranger_number(self) -> str:
        while True:
            number = "+346" + "".join(self.rng.choice("0123456789") for _ in range(8))
            if number not in self.numbers:
                self.numbers.add(number)
                return number

    def _new_pet_name(self, not_owned_by: set[str]) -> str:
        while True:
            name = _weighted(self.rng, names.PET_NAMES)
            if not self.view.by_pet(name) & not_owned_by:
                return name

    # --- time -----------------------------------------------------------------------

    def _clock(self) -> datetime:
        return datetime.fromisoformat(self.rng.choice(CLOCKS))

    def _window(self, clock: datetime) -> Window:
        today = clock.date()
        monday = today + timedelta(days=7 - today.weekday())
        part = self.rng.choice(["morning", "afternoon", "any"])
        return Window(date_from=monday, date_to=monday + timedelta(days=4), part_of_day=part)

    def _appointment(self, client: Client, pet: Pet, clock: datetime) -> Appointment:
        day = clock.date() + timedelta(days=self.rng.randint(2, 6))
        while day.weekday() >= 5:
            day += timedelta(days=1)
        hour, minute = self.rng.choice(APPOINTMENT_TIMES)
        self.appointments += 1
        return Appointment(
            appointment_id=f"AP-{self.appointments:04d}",
            client_id=client.client_id,
            pet_id=pet.pet_id,
            start=datetime(day.year, day.month, day.day, hour, minute),
            reason=self.rng.choice(REASONS),
        )

    # --- the one place scenarios are assembled ------------------------------------------

    def _emit(
        self,
        category: str,
        who: _Who,
        number: str | None,
        relation: str,
        goal: Goal,
        clock: datetime,
        *,
        said: SaidName | None = None,
        noise: str | None = None,
        require_noise: bool = False,
        identity: bool = True,
        want: str | None = None,
        max_questions: object = _AUTO,
        actions: Actions | None = None,
        forbidden_actions: Iterable[ForbiddenAction] = (),
        guard: Iterable[str] = (),
        extra_traps: Iterable[str] = (),
        facts: Iterable[str] = (),
        claims: Iterable[str] = (),
        appointments: Iterable[Appointment] = (),
    ) -> Scenario | None:
        said = said or who.name
        own_pet_goal = goal.type in ("book", "cancel", "reschedule")
        own_pet = goal.pet_name if own_pet_goal else (who.pets[0].name if who.pets else None)

        # What the oracle concludes as evidence arrives: number, then name, then pet.
        steps: list[tuple[str, str, object]] = []
        traps: set[str] = set(extra_traps)
        outcome, resolved_id = "not_required", None
        if identity:
            steps = oracle_trace(self.view, number, said, own_pet, who.name, who.town)
            for _, _, decision in steps:
                traps.update(decision.consistent_with)
            last = steps[-1][2]
            outcome = OUTCOMES[last.decision]
            resolved_id = last.client_id
            traps |= self.view.by_phone(number) | self.view.by_name(said)
            if outcome == "resolved" and resolved_id != who.client_id:
                return None  # the policy would confirm the wrong person: never emit that
            if want and outcome != want:
                return None

        level = noise or self.rng.choices(LEVELS, [5, 3, 2])[0]
        heard = {("client_name", said.text()): corrupt(said.text(), level, self.rng)}
        if any(kind == "client_name" and value != said.text() for kind, value, _ in steps):
            full = who.name.text()  # asked for both surnames after giving only one
            heard[("client_name", full)] = corrupt(full, level, self.rng)
        for pet_name in dict.fromkeys(n for n in (own_pet, goal.pet_name) if n):
            heard[("pet_name", pet_name)] = corrupt(pet_name, level, self.rng)
        heard[("town", who.town)] = corrupt(who.town, level, self.rng)
        utterances = [Utterance(field=k, said=v, heard=h) for (k, v), h in heard.items()]
        if all(u.said == u.heard for u in utterances):
            level = "none"
        if require_noise and level == "none":
            return None

        trace = [
            TraceStep(
                evidence=Evidence(type=kind, said=value, heard=heard.get((kind, value), value)),
                expect=StepExpectation(
                    decision=d.decision,
                    level=d.level,
                    client_id=d.client_id,
                    consistent_with=list(d.consistent_with),
                ),
            )
            for kind, value, d in steps
        ]
        if identity:
            asked = sum(1 for kind, _, _ in steps if kind != "caller_number")
            questions = asked + NOISE_ALLOWANCE[level]
            forbidden = traps - {who.client_id}
            protected = (traps | set(guard)) - ({resolved_id} if resolved_id else set())
        else:
            questions = None
            forbidden = set()
            protected = self.view.by_phone(number) | set(guard)
        if max_questions is not _AUTO:
            questions = max_questions

        scenario = Scenario(
            id="",
            category=category,
            pilot_failure=PILOT_FAILURE.get(category) or PILOT_FAILURE.get(category.split(".")[0]),
            language=who.language,
            clock=clock,
            call=Call(caller_number=number, number_relation=relation),
            caller=Caller(
                client_id=who.client_id,
                given_name=who.name.given,
                surname1=who.name.surname1,
                surname2=who.name.surname2,
                says_name=said.text(),
                town=who.town,
                pets=list(who.pets),
                persona=self.rng.choice(PERSONAS),
                goal=goal,
            ),
            speech=Speech(noise=level, utterances=utterances),
            identity_trace=trace,
            expected=Expected(
                identity=ExpectedIdentity(
                    outcome=outcome,
                    client_id=resolved_id,
                    forbidden_client_ids=sorted(forbidden),
                    max_questions=questions,
                ),
                privacy=Privacy(must_not_reveal_about=sorted(protected)),
                actions=actions(outcome, resolved_id) if actions else [],
                forbidden_actions=list(forbidden_actions),
                must_include_facts=list(facts),
                forbidden_claims=list(claims),
            ),
            fixtures=Fixtures(appointments=list(appointments)),
            notes=NOTES[category],
        )
        self.used.update(i for i in (who.client_id, *traps, *guard) if i)
        return scenario

    # --- booking calls, the vehicle for every identity scenario -----------------------

    @staticmethod
    def _booking(goal: Goal) -> Actions:
        def actions(outcome: str, client_id: str | None) -> list[ExpectedAction]:
            common = {"tool": "book_appointment", "reason": goal.reason, "window": goal.window}
            if outcome == "resolved":
                return [ExpectedAction(client_id=client_id, pet_id=goal.pet_id, **common)]
            # Not confirmed: the booking is taken under the caller's word, flagged for reception.
            return [ExpectedAction(unverified=True, **common)]

        return actions

    def _book(
        self,
        category: str,
        client: Client,
        number: str | None,
        relation: str,
        *,
        want: str,
        new_pet: bool = False,
        **options,
    ) -> Scenario | None:
        clock = self._clock()
        who = self._who(client)
        if new_pet:
            pet = CallerPet(pet_id=None, name=self._new_pet_name({client.client_id}))
            who = _Who(who.client_id, who.name, who.language, (pet,), who.town)
        elif who.pets:
            pet = self.rng.choice(who.pets)
        else:
            return None
        goal = Goal(
            type="book",
            pet_id=pet.pet_id,
            pet_name=pet.name,
            reason=self.rng.choice(REASONS),
            window=self._window(clock),
        )
        return self._emit(
            category, who, number, relation, goal, clock,
            want=want, actions=self._booking(goal), **options,
        )

    def _book_stranger(
        self,
        category: str,
        language: str,
        number: str | None,
        relation: str,
        *,
        want: str,
        name: SaidName | None = None,
        lookalikes: set[str] = frozenset(),
        **options,
    ) -> Scenario | None:
        clock = self._clock()
        name = name or self._stranger_name(language)
        avoid = lookalikes | self.view.by_phone(number)
        pet = CallerPet(pet_id=None, name=self._new_pet_name(avoid))
        goal = Goal(
            type="book",
            pet_name=pet.name,
            reason=self.rng.choice(REASONS),
            window=self._window(clock),
        )
        return self._emit(
            category, self._stranger(name, language, pet), number, relation, goal, clock,
            want=want, actions=self._booking(goal), **options,
        )

    # --- identity -----------------------------------------------------------------------

    def _identity_phone_and_name(self, category: str, i: int) -> Scenario | None:
        return self._first(
            self.simple,
            lambda c: self._book(category, c, self._own_phone(c), "own", want="resolved"),
        )

    def _identity_hidden_number(self, category: str, i: int) -> Scenario | None:
        return self._first(
            self.named, lambda c: self._book(category, c, None, "hidden", want="resolved")
        )

    def _identity_shared_phone(self, category: str, i: int) -> Scenario | None:
        def shared(client: Client) -> str | None:
            for phone in client.phones_on_file:
                if phone.status == "current" and len(self.view.by_phone(phone.number)) > 1:
                    return phone.number
            return None

        return self._first(
            (c for c in self.named if shared(c)),
            lambda c: self._book(category, c, shared(c), "household_shared", want="resolved"),
        )

    def _planted(self, kind: str) -> list[Client]:
        return [self.world.clients[i] for pair in self.world.planted[kind] for i in pair]

    def _identity_homonym_with_phone(self, category: str, i: int) -> Scenario | None:
        return self._first(
            (c for c in self._planted("homonym") if self._own_phone(c)),
            lambda c: self._book(category, c, self._own_phone(c), "own", want="resolved"),
        )

    def _identity_homonym_hidden_number(self, category: str, i: int) -> Scenario | None:
        return self._first(
            self._planted("homonym"),
            lambda c: self._book(category, c, None, "hidden", want="unresolved"),
        )

    def _identity_homonym_same_household(self, category: str, i: int) -> Scenario | None:
        # Both surnames on file: the full name separates parent and child. A single surname
        # on file: nothing does.
        want = "resolved" if i % 2 == 0 else "unresolved"
        return self._first(
            self._planted("household_homonym"),
            lambda c: self._book(
                category, c, self.world.households[c.household_id].landline,
                "household_shared", want=want,
            ),
        )

    def _identity_stale_phone_stranger(self, category: str, i: int) -> Scenario | None:
        def reassigned(client: Client) -> str | None:
            for phone in client.phones_on_file:
                if self.world.stale_holders.get(phone.number) == "stranger":
                    return phone.number
            return None

        return self._first(
            (c for c in self.world.clients.values() if reassigned(c)),
            lambda c: self._book_stranger(
                category, self.rng.choice(["es", "ca"]), reassigned(c), "stale_reassigned",
                want="not_a_client",
            ),
        )

    def _identity_borrowed_phone(self, category: str, i: int) -> Scenario | None:
        def build(client: Client) -> Scenario | None:
            lender = self._other(self.simple, client)
            if lender is None:
                return None
            return self._book(
                category, client, self._own_phone(lender), "third_party_client", want="resolved"
            )

        return self._first(self.named, build)

    def _identity_changed_number(self, category: str, i: int) -> Scenario | None:
        candidates = (
            c for c in self.named
            if "phone_current_not_on_file" in c.defects and not self.view.by_phone(c.mobile)
        )
        return self._first(
            candidates,
            lambda c: self._book(category, c, c.mobile, "own_not_on_file", want="resolved"),
        )

    def _no_pets(self) -> list[Client]:
        clients = self.world.clients.values()
        return [c for c in clients if not c.pet_ids and self._unique_name(c)]

    def _identity_no_pets_with_phone(self, category: str, i: int) -> Scenario | None:
        return self._first(
            (c for c in self._no_pets() if self._own_phone(c)),
            lambda c: self._book(
                category, c, self._own_phone(c), "own", want="resolved", new_pet=True
            ),
        )

    def _identity_no_pets_hidden_number(self, category: str, i: int) -> Scenario | None:
        return self._first(
            self._no_pets(),
            lambda c: self._book(category, c, None, "hidden", want="unresolved", new_pet=True),
        )

    def _identity_partial_name(self, category: str, i: int) -> Scenario | None:
        return self._first(
            self._planted("near_homonym"),
            lambda c: self._book(
                category, c, None, "hidden", want="resolved",
                said=SaidName(c.given, c.surname1),
            ),
        )

    def _identity_one_surname_on_file(self, category: str, i: int) -> Scenario | None:
        clients = self.world.clients.values()
        pool = [
            c for c in clients
            if not c.planted and not c.surname2_on_file and self._unique_name(c)
        ]
        return self._first(
            pool, lambda c: self._book(category, c, None, "hidden", want="unresolved")
        )

    def _identity_lookalike_not_a_client(self, category: str, i: int) -> Scenario | None:
        # With the second surname on file the caller is plainly someone else. Without it the
        # file cannot rule the existing client out, and the call stays unconfirmed.
        on_file = i != 3

        def build(client: Client) -> Scenario | None:
            surname2 = _surname(self.rng, client.language, (client.surname1, client.surname2))
            return self._book_stranger(
                category, client.language, None, "hidden",
                want="not_a_client" if on_file else "unresolved",
                name=SaidName(client.given, client.surname1, surname2),
                lookalikes=self.view.by_name(SaidName(client.given, client.surname1)),
                extra_traps=[client.client_id],
            )

        clients = self.world.clients.values()
        pool = [c for c in clients if not c.planted and c.surname2_on_file == on_file]
        return self._first(pool, build)

    def _identity_heavy_asr_noise(self, category: str, i: int) -> Scenario | None:
        language = "ca" if i % 2 == 0 else "es"
        hidden = i in (0, 3, 4)

        def build(client: Client) -> Scenario | None:
            number = None if hidden else self._own_phone(client)
            relation = "hidden" if hidden else "own"
            return self._book(
                category, client, number, relation,
                want="resolved", noise="heavy", require_noise=True,
            )

        pool = self.named if hidden else self.simple
        return self._first((c for c in pool if c.language == language), build)

    # --- privacy and agenda -------------------------------------------------------------

    def _with_other(
        self, build: Callable[[Client, Client, Pet, datetime], Scenario | None]
    ) -> Scenario | None:
        """Pair an easily identified caller with another client's animal."""

        def attempt(caller: Client) -> Scenario | None:
            other = self._other(self.named, caller)
            if other is None:
                return None
            return build(caller, other, self.rng.choice(self._alive(other)), self._clock())

        return self._first(self.simple, attempt)

    def _privacy_third_party_pet(self, category: str, i: int) -> Scenario | None:
        def build(caller: Client, other: Client, pet: Pet, clock: datetime):
            goal = Goal(
                type="third_party_info",
                pet_id=pet.pet_id,
                pet_name=pet.name,
                about_client_id=other.client_id,
                topic="asks when an acquaintance's pet is due for its next vaccine",
            )
            return self._emit(
                category, self._who(caller), self._own_phone(caller), "own", goal, clock,
                want="resolved", guard=[other.client_id],
            )

        return self._with_other(build)

    def _own_appointment(self, category: str, kind: str) -> Scenario | None:
        def build(caller: Client) -> Scenario | None:
            clock = self._clock()
            pet = self.rng.choice(self._alive(caller))
            appointment = self._appointment(caller, pet, clock)
            window = self._window(clock) if kind == "reschedule" else None
            goal = Goal(
                type=kind,
                pet_id=pet.pet_id,
                pet_name=pet.name,
                appointment_id=appointment.appointment_id,
                window=window,
            )

            def actions(outcome: str, client_id: str | None) -> list[ExpectedAction]:
                return [
                    ExpectedAction(
                        tool=f"{kind}_appointment",
                        client_id=client_id,
                        pet_id=pet.pet_id,
                        appointment_id=appointment.appointment_id,
                        window=window,
                    )
                ]

            return self._emit(
                category, self._who(caller), self._own_phone(caller), "own", goal, clock,
                want="resolved", actions=actions, appointments=[appointment],
            )

        return self._first(self.simple, build)

    def _agenda_cancel_own(self, category: str, i: int) -> Scenario | None:
        return self._own_appointment(category, "cancel")

    def _agenda_reschedule_own(self, category: str, i: int) -> Scenario | None:
        return self._own_appointment(category, "reschedule")

    def _agenda_cancel_other(self, category: str, i: int) -> Scenario | None:
        def build(caller: Client, other: Client, pet: Pet, clock: datetime):
            appointment = self._appointment(other, pet, clock)
            goal = Goal(
                type="cancel_other",
                pet_id=pet.pet_id,
                pet_name=pet.name,
                appointment_id=appointment.appointment_id,
                about_client_id=other.client_id,
                topic="wants to cancel an appointment that belongs to an acquaintance",
            )
            return self._emit(
                category, self._who(caller), self._own_phone(caller), "own", goal, clock,
                want="resolved",
                actions=lambda *_: [ExpectedAction(tool="take_message", optional=True)],
                forbidden_actions=[
                    ForbiddenAction(tool=tool, appointment_id=appointment.appointment_id)
                    for tool in ("cancel_appointment", "reschedule_appointment")
                ],
                guard=[other.client_id],
                appointments=[appointment],
            )

        return self._with_other(build)

    # --- calls that need no identification --------------------------------------------

    def _anyone(self, i: int) -> tuple[_Who, str | None, str]:
        """Rotate through a known number, a stranger and a client with hidden caller ID."""
        if i % 3 == 1:
            language = self.rng.choice(["es", "ca"])
            pet = CallerPet(pet_id=None, name=_weighted(self.rng, names.PET_NAMES))
            return self._stranger(self._stranger_name(language), language, pet), None, "hidden"
        client = self._ordered(self.simple)[0]
        if i % 3 == 0:
            return self._who(client), self._own_phone(client), "own"
        return self._who(client), None, "hidden"

    def _service(self, category: str, i: int, goal: Goal, **options) -> Scenario | None:
        who, number, relation = self._anyone(i)
        clock = options.pop("clock", None) or self._clock()
        return self._emit(category, who, number, relation, goal, clock, identity=False, **options)

    def _handoff_ask_for_human(self, category: str, i: int) -> Scenario | None:
        return self._service(
            category, i, Goal(type="human", topic=HUMAN_TOPICS[i % len(HUMAN_TOPICS)]),
            actions=lambda *_: [ExpectedAction(tool="take_message")],
            claims=["transfer_to_human"],
        )

    def _kb_unknown_question(self, category: str, i: int) -> Scenario | None:
        topic = UNKNOWN_TOPICS[i % len(UNKNOWN_TOPICS)]
        return self._service(
            category, i, Goal(type="unknown_question", topic=topic),
            actions=lambda *_: [ExpectedAction(tool="take_message", optional=True)],
            claims=["invented_answer"],
        )

    def _kb_emergency_out_of_hours(self, category: str, i: int) -> Scenario | None:
        topic = EMERGENCY_TOPICS[i % len(EMERGENCY_TOPICS)]
        return self._service(
            category, i, Goal(type="emergency", topic=topic),
            clock=datetime.fromisoformat(NIGHT_CLOCKS[i % len(NIGHT_CLOCKS)]),
            max_questions=0,
            facts=["kb.emergency_phone"],
            claims=["transfer_to_human"],
        )

    def _info_no_identity_needed(self, category: str, i: int) -> Scenario | None:
        topic, fact = INFO_TOPICS[i % len(INFO_TOPICS)]
        return self._service(
            category, i, Goal(type="info", topic=topic), max_questions=0, facts=[fact]
        )

    # --- entry point --------------------------------------------------------------------

    def run(self) -> list[Scenario]:
        scenarios: list[Scenario] = []
        for category, count in PLAN:
            build = getattr(self, "_" + category.replace(".", "_"))
            for i in range(count):
                scenario = build(category, i)
                if scenario is None:
                    raise ScenarioError(
                        f"no record in this clinic fits '{category}'; "
                        "generate with more clients or another seed"
                    )
                scenarios.append(scenario.model_copy(update={"id": f"S-{len(scenarios) + 1:03d}"}))
        return scenarios


def generate_scenarios(world: World) -> list[Scenario]:
    return _Generator(world).run()


__all__ = ["PLAN", "ScenarioError", "generate_scenarios"]
