"""The public demo: a call from a browser to the same front desk, within a budget.

A browser brings no calling number, and the number is the first clue of who is on the
line: without one every visitor would be a hidden number, and the point of the project,
that the agent knows when it does not know who it is talking to, could not be tried. So a
visitor picks who they call as, one of a few of the made-up clinic's own test callers,
and the call is taken as coming from that caller's phone.

Anybody can open the page, and every minute of a call is paid for. The server gives a pass
for each call and counts them: so many minutes a day in all, so many a call, so many calls
from one address. A call nobody has a pass for is not answered.

A demo call touches nothing a real one would: it gets an appointment book of its own, and
reception is told nothing.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..scenario import Scenario

# Who a visitor can call as: the key the page uses, and the kind of test caller it is.
ROLES = {
    "own": "identity.phone_and_name",  # from the phone on their record
    "hidden": "identity.hidden_number",  # no calling number at all
    "borrowed": "identity.borrowed_phone",  # a client, from another client's phone
    "stranger": "identity.lookalike_not_a_client",  # nobody's record, a name that looks like one
}

# Said to a caller whose demo call has run its time, before the line is closed, and to one
# who has no pass. Written here, in each language: the model has no part in it.
TIME_IS_UP = {
    "es": "Esta llamada de demostración ha llegado a su límite de tiempo. Gracias por "
          "probarla. Adiós.",
    "ca": "Aquesta trucada de demostració ha arribat al límit de temps. Gràcies per "
          "provar-la. Adeu.",
    "en": "This demo call has reached its time limit. Thank you for trying it. Goodbye.",
    "de": "Dieser Demo-Anruf hat sein Zeitlimit erreicht. Danke fürs Ausprobieren. Auf "
          "Wiederhören.",
    "fr": "Cet appel de démonstration a atteint sa limite de temps. Merci de l'avoir "
          "essayé. Au revoir.",
    "it": "Questa chiamata dimostrativa ha raggiunto il limite di tempo. Grazie per averla "
          "provata. Arrivederci.",
    "ru": "Время этого демонстрационного звонка истекло. Спасибо, что попробовали. До "
          "свидания.",
}
NO_PASS = "Esta demostración no está disponible ahora mismo. Adiós."


@dataclass(frozen=True)
class Persona:
    """One of the clinic's made-up callers, as the page shows them to a visitor."""

    key: str
    name: str
    town: str
    pets: tuple[str, ...]
    caller_number: str | None  # the number the call is taken as coming from
    language: str


def personas(scenarios: Iterable[Scenario]) -> tuple[Persona, ...]:
    """The first test caller of each kind the demo offers, in the order of `ROLES`."""
    first: dict[str, Scenario] = {}
    for scenario in scenarios:
        first.setdefault(scenario.category, scenario)
    found = []
    for key, category in ROLES.items():
        if scenario := first.get(category):
            caller = scenario.caller
            found.append(Persona(key, caller.says_name, caller.town,
                                 tuple(pet.name for pet in caller.pets),
                                 scenario.call.caller_number, scenario.language))
    return tuple(found)


class Full(Exception):
    """No call can be started now. `why` is `day` (the day's minutes are spent) or
    `address` (too many calls from this address today)."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


@dataclass
class Pass:
    persona: Persona
    started: datetime
    address: str
    last: datetime | None = None  # the last time the call was heard of; None, never


class Demo:
    """The passes given today and what they have used of the day's minutes."""

    def __init__(self, people: Iterable[Persona], now: Callable[[], datetime] = datetime.now,
                 minutes_a_day: float = 60, minutes_a_call: float = 3,
                 calls_an_address: int = 6) -> None:
        self.people = {persona.key: persona for persona in people}
        self._now = now
        self._day = timedelta(minutes=minutes_a_day)
        self.limit = timedelta(minutes=minutes_a_call)
        self._calls_an_address = calls_an_address
        self._passes: dict[str, Pass] = {}

    def _today(self) -> list[Pass]:
        today = self._now().date()
        self._passes = {token: given for token, given in self._passes.items()
                        if given.started.date() == today}
        return list(self._passes.values())

    def _used(self, given: Pass) -> timedelta:
        """A call still within its time counts whole: it may yet use all of it. After
        that it counts for what it took, and one that was never made for nothing."""
        if self._now() - given.started < self.limit:
            return self.limit
        if given.last is None:
            return timedelta(0)
        took = given.last - given.started + timedelta(seconds=20)  # the last answer, said
        return min(self.limit, max(took, timedelta(seconds=30)))

    def left(self) -> timedelta:
        """What is left of today's minutes."""
        used = sum((self._used(given) for given in self._today()), timedelta(0))
        return max(self._day - used, timedelta(0))

    def start(self, key: str, address: str) -> str:
        """A pass for one call as the persona `key`. KeyError for a persona there is not,
        `Full` when no call can be started."""
        persona = self.people[key]
        if self.left() < self.limit:
            raise Full("day")
        if sum(given.address == address for given in self._today()) >= self._calls_an_address:
            raise Full("address")
        token = secrets.token_urlsafe(16)
        self._passes[token] = Pass(persona, self._now(), address)
        return token

    def call(self, token: str) -> Pass | None:
        """The pass a call was started with, and that it has been heard of now. None for
        a pass never given, or given another day."""
        self._today()
        given = self._passes.get(token)
        if given is not None:
            given.last = self._now()
        return given

    def forget(self, token: str) -> None:
        """A pass given for a call that could not be started after all."""
        self._passes.pop(token, None)

    def over(self, token: str) -> bool:
        """Whether the call has run its time."""
        given = self._passes.get(token)
        return given is None or self._now() - given.started >= self.limit
