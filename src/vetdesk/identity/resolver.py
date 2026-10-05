"""Identity resolution: who is calling, how sure are we, and what to ask next.

The resolver never guesses. It returns one of three decisions:

* `resolved`: one client is confirmed. Their data may be read and written.
* `ask`: not enough evidence yet. `ask_for` says what would help; when it is None there is
  nothing left to ask and the caller stays unconfirmed.
* `not_found`: the name matches nobody on file. Treat the caller as not a client.

A client is confirmed when their NAME matches the record and ONE MORE FACTOR corroborates
it, leaving a single candidate. The factor is the calling number being on their record, or
a pet name linked to them. How much the name is worth depends on how much of it could be
compared (rules learnt from measuring, not from intuition):

* Full name, both surnames given and on file: the phone confirms it. A pet confirms it only
  together with the town on the record, because two different people do share a full name
  and a pet name now and then.
* One surname given but the record holds two: nothing confirms it. Ask for both surnames.
  Relatives are often namesakes and borrow each other's phones.
* The record itself holds a single surname: only the phone confirms it, and only when
  nobody else with that surname shares the number. A pet name cannot confirm half a name.

The calling number alone never confirms anybody. A number on somebody else's record rules
the pet and the town out: from another client's phone, nobody is confirmed.

Names that only resemble a record (a likely speech-recognition error) never count until
the caller has confirmed or spelled them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..legacy.models import Client, Clinic
from ..legacy.normalize import fold, osa_distance
from .matching import (
    EXACT,
    GRADE_NAMES,
    NO_MATCH,
    SIMILAR,
    SOUNDS_SAME,
    TYPO,
    SpokenName,
    heard_grade,
    name_grade,
    one_typo_apart,
    parse_spoken_name,
    pet_grade,
    spelled_grade,
)
from .phonetics import phonetic_key

Decision = Literal["ask", "resolved", "not_found"]
Level = Literal["none", "probable", "confirmed"]
AskFor = Literal["client_name", "confirm_name", "full_name", "pet_name", "confirm_pet", "town"]


@dataclass(frozen=True)
class Evidence:
    """Everything known about the caller so far. Build it up as the call goes on."""

    caller_number: str | None = None
    client_name: str | None = None
    name_verified: bool = False  # the caller confirmed or spelled their name
    pet_name: str | None = None
    pet_verified: bool = False
    town: str | None = None


@dataclass(frozen=True)
class Candidate:
    client: Client
    name_grade: int
    phone_on_file: bool
    full_name: bool = False  # both surnames were given and compared with the record
    pet_grade: int = NO_MATCH
    pet_matched: str | None = None
    town_matches: bool = False

    def reasons(self, verified: bool) -> list[str]:
        reasons = []
        if self.name_grade:
            label = "one typo away" if verified and self.name_grade == TYPO else \
                GRADE_NAMES[self.name_grade]
            reasons.append(f"name {label}: {self.client.raw_name!r}")
        if self.phone_on_file:
            reasons.append("calling number is on this record")
        if self.pet_grade:
            reasons.append(f"pet {GRADE_NAMES[self.pet_grade]}: {self.pet_matched!r}")
        if self.town_matches:
            reasons.append(f"town matches: {self.client.town!r}")
        return reasons


@dataclass(frozen=True)
class Resolution:
    decision: Decision
    level: Level
    client: Client | None
    candidates: tuple[Candidate, ...]
    ask_for: AskFor | None
    why: str


def _one_word(place: str) -> str:
    """A place name as a single word, so it is compared whole and by sound."""
    return "".join(place.lower().split())


class IdentityResolver:
    def __init__(self, clinic: Clinic) -> None:
        self.clinic = clinic
        # Clients grouped by first surname, so a lookup grades each distinct surname once
        # instead of every client.
        self._by_surname: dict[str, list[Client]] = {}
        for client in clinic.clients.values():
            if client.name is not None:
                self._by_surname.setdefault(client.name.surname1.lower(), []).append(client)
        towns = {_one_word(c.town) for c in clinic.clients.values() if c.town}
        self._towns = {town: phonetic_key(town) for town in sorted(towns)}

    def resolve(self, evidence: Evidence) -> Resolution:
        on_phone = {c.code for c in self.clinic.clients_by_phone(evidence.caller_number)}

        said = parse_spoken_name(evidence.client_name or "")
        if said is None:
            candidates = tuple(
                Candidate(self.clinic.clients[code], NO_MATCH, True) for code in sorted(on_phone)
            )
            why = "the calling number is on file, which is a hint and not a confirmation" \
                if on_phone else "nothing is known about the caller yet"
            if evidence.client_name:
                why = "a first name alone is not enough to look anybody up"
            return Resolution("ask", "probable" if on_phone else "none", None, candidates,
                              "client_name", why)

        matches = []
        surname_grade = spelled_grade if evidence.name_verified else heard_grade
        for surname, clients in self._by_surname.items():
            if not surname_grade(said.surname1, surname):
                continue
            for client in clients:
                grade = name_grade(said, client.name, evidence.name_verified)
                if grade:
                    matches.append(self._candidate(client, said, grade, on_phone, evidence))
        if not matches:
            return Resolution("not_found", "none", None, (), None,
                              "no client on file has this name")

        strong = [m for m in matches if m.name_grade >= SOUNDS_SAME]
        weak = [m for m in matches if m.name_grade == SIMILAR]
        if not evidence.name_verified:
            if not strong:
                return self._ask(weak, "confirm_name",
                                 "the name only resembles names on file: confirm or spell it")
            if any(m.phone_on_file or m.pet_grade >= SOUNDS_SAME for m in weak):
                return self._ask(strong + weak, "confirm_name",
                                 "another client with a similar name fits the other evidence")
        elif any(m.name_grade == EXACT for m in strong):
            strong = [m for m in strong if m.name_grade == EXACT]

        # Narrow down with each corroborating factor that actually selects somebody.
        pool, by_the_phone = strong, False
        with_phone = [m for m in pool if m.phone_on_file]
        if with_phone:
            pool = with_phone
            by_the_phone = all(self._phone_confirms(m, on_phone) for m in with_phone)
        elif on_phone:
            # The number is on somebody else's record. A client on a borrowed phone and an
            # acquaintance giving that client's name, pet and town bring the same evidence,
            # so neither is confirmed: what they know is not enough from another's phone.
            return self._ask(pool, None, "the calling number is on another client's record")
        with_pet = [m for m in pool if m.pet_grade >= SOUNDS_SAME]
        if with_pet:
            pool = with_pet
        by_the_pet = len(pool) == 1 and bool(with_pet) and pool[0].full_name
        if len(pool) == 1 and (by_the_phone or (by_the_pet and pool[0].town_matches)):
            (winner,) = pool
            return Resolution("resolved", "confirmed", winner.client, (winner,), None,
                              "; ".join(winner.reasons(evidence.name_verified)))
        if by_the_pet:
            if not evidence.town:
                return self._ask(pool, "town", "name and pet agree: the town will settle it")
            return self._ask(pool, None, "the town does not match the record")

        if said.surname2 is None and any(m.client.name.surname2 for m in pool):
            return self._ask(pool, "full_name",
                             "one surname is not enough here: ask for both surnames")
        if not evidence.pet_name:
            return self._ask(pool, "pet_name", "the name needs one more factor to corroborate it")
        if not with_pet and not evidence.pet_verified:
            if any(m.pet_grade == SIMILAR for m in pool):
                return self._ask(pool, "confirm_pet",
                                 "the pet name only resembles one on file: confirm it")
        if len(pool) > 1:
            why = "several clients fit and nothing on file tells them apart"
        elif with_pet:
            why = "the record holds a single surname, so a pet name cannot confirm it"
        else:
            why = "nothing corroborates the name"
        return self._ask(pool, None, why)

    def _candidate(
        self, client: Client, said: SpokenName, grade: int, on_phone: set[int],
        evidence: Evidence,
    ) -> Candidate:
        pet, matched = NO_MATCH, None
        if evidence.pet_name:
            names = [a.name for a in self.clinic.animals_of(client.code)]
            pet, matched = pet_grade(evidence.pet_name, names, evidence.pet_verified)
        full_name = bool(said.surname2 and client.name.surname2)
        town = bool(client.town) and self._town_heard(evidence.town) == _one_word(client.town)
        return Candidate(client, grade, client.code in on_phone, full_name, pet, matched, town)

    def _town_heard(self, heard: str | None) -> str | None:
        """Which of the towns on file the caller most likely said, if any.

        Towns come from a short known list, so a badly heard one can still be recognised
        as long as it is clearly closer to one town than to the others."""
        if not heard:
            return None
        key = phonetic_key(_one_word(heard))
        scored = sorted(
            ((1 - osa_distance(key, town_key, 99) / max(len(key), len(town_key)), town)
             for town, town_key in self._towns.items()),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.6:
            return None
        if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.15:
            return None
        return scored[0][1]

    def _phone_confirms(self, match: Candidate, on_phone: set[int]) -> bool:
        if match.client.name.surname2:
            return match.full_name
        return not self._namesake_risk(match.client, on_phone)

    def _namesake_risk(self, client: Client, on_phone: set[int]) -> bool:
        """Somebody else on this number carries the same first surname: a family, where
        namesakes are common. The number then only counts with both surnames matched."""
        surname = fold(client.name.surname1)
        for code in on_phone - {client.code}:
            other = self.clinic.clients[code].name
            if other is None:
                return True
            if fold(other.surname1) == surname or one_typo_apart(fold(other.surname1), surname):
                return True
        return False

    @staticmethod
    def _ask(candidates: list[Candidate], ask_for: AskFor | None, why: str) -> Resolution:
        ordered = sorted(candidates, key=lambda m: (-m.name_grade, m.client.code))
        return Resolution("ask", "probable", None, tuple(ordered), ask_for, why)
