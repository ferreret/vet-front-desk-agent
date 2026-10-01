"""The reference answer for identity resolution.

The oracle is a perfect listener holding a perfectly cleaned copy of what the legacy file
knows: it hears every name exactly as said and is never fooled by spelling. It is still
limited to what is on file, so it cannot tell two homonyms apart unless the data can.

Policy: a caller is confirmed when their name matches a client AND one more factor
corroborates it, leaving exactly one candidate. The factor is the calling number being on
that client's record, or a pet name linked to that client. How much the name is worth
depends on how much of it could be compared:

* Full name (both surnames given and on file): either factor confirms it.
* The caller gave one surname but the record holds two: nothing confirms it; the missing
  surname has to be asked for. Relatives are often namesakes and borrow each other's phones.
* The record itself holds a single surname: only the phone confirms it, and only when
  nobody else with that surname shares the number. A pet name cannot confirm half a name.

A phone alone never confirms anybody.
"""

from __future__ import annotations

from dataclasses import dataclass

from .names import given_key
from .text import fold
from .world import World


@dataclass(frozen=True)
class SaidName:
    given: str
    surname1: str
    surname2: str | None = None

    def text(self) -> str:
        return " ".join(part for part in (self.given, self.surname1, self.surname2) if part)


@dataclass(frozen=True)
class Decision:
    decision: str  # ask | resolved | not_found
    level: str  # none | probable | confirmed
    client_id: str | None
    consistent_with: tuple[str, ...]


class FileView:
    """What the legacy file can support, with all spelling problems already solved."""

    def __init__(self, world: World) -> None:
        self._phones = world.phone_index()
        self._two_surnames = {c.client_id for c in world.clients.values() if c.surname2_on_file}
        self._surname1 = {c.client_id: fold(c.surname1) for c in world.clients.values()}
        self._names: dict[tuple[str, str], list] = {}
        for client in world.clients.values():
            key = (given_key(client.given), fold(client.surname1))
            self._names.setdefault(key, []).append(client)
        self._pets: dict[str, set[str]] = {}
        for pet in world.pets.values():
            self._pets.setdefault(fold(pet.name), set()).update(pet.linked_client_ids)

    def by_phone(self, number: str | None) -> set[str]:
        return set(self._phones.get(number, ())) if number else set()

    def by_name(self, said: SaidName) -> set[str]:
        surname2 = fold(said.surname2) if said.surname2 else None
        matches = self._names.get((given_key(said.given), fold(said.surname1)), [])
        return {
            c.client_id
            for c in matches
            if surname2 is None or not c.surname2_on_file or fold(c.surname2) == surname2
        }

    def by_pet(self, name: str | None) -> set[str]:
        return set(self._pets.get(fold(name), ())) if name else set()

    def namesake_risk(self, number: str | None, client_id: str) -> bool:
        """Somebody else on this number carries the same first surname: a family."""
        others = self.by_phone(number) - {client_id}
        return any(self._surname1[o] == self._surname1[client_id] for o in others)

    def full_name_matched(self, said: SaidName, client_id: str) -> bool:
        """Both surnames were given and the record has both to compare them with."""
        return bool(said.surname2) and client_id in self._two_surnames

    def phone_confirms(self, said: SaidName, number: str | None, client_id: str) -> bool:
        if client_id in self._two_surnames:
            return bool(said.surname2)
        return not self.namesake_risk(number, client_id)


def decide(
    view: FileView, number: str | None, name: SaidName | None, pet: str | None
) -> Decision:
    by_phone = view.by_phone(number)
    if name is None:
        level = "probable" if by_phone else "none"
        return Decision("ask", level, None, tuple(sorted(by_phone)))
    candidates = view.by_name(name)
    if not candidates:
        return Decision("not_found", "none", None, ())
    corroborated = False
    on_phone = candidates & by_phone
    if on_phone:
        candidates = on_phone
        corroborated = all(view.phone_confirms(name, number, c) for c in on_phone)
    with_pet = candidates & view.by_pet(pet)
    if with_pet:
        candidates = with_pet
        if all(view.full_name_matched(name, client_id) for client_id in with_pet):
            corroborated = True
    if corroborated and len(candidates) == 1:
        (client_id,) = candidates
        return Decision("resolved", "confirmed", client_id, (client_id,))
    return Decision("ask", "probable", None, tuple(sorted(candidates)))


def trace(
    view: FileView,
    number: str | None,
    name: SaidName,
    pet: str | None,
    full_name: SaidName | None = None,
) -> list[tuple[str, str, Decision]]:
    """Evidence in the order a call produces it, with the decision after each piece:
    number, name, full name (when the caller first gave a single surname), pet.
    Stops at the first decision that settles the matter."""
    evidence: list[tuple[str, object]] = [("caller_number", number)] if number else []
    evidence.append(("client_name", name))
    if full_name and full_name != name:
        evidence.append(("client_name", full_name))
    if pet:
        evidence.append(("pet_name", pet))
    steps: list[tuple[str, str, Decision]] = []
    known_name = known_pet = None
    for kind, value in evidence:
        known_name = value if kind == "client_name" else known_name
        known_pet = value if kind == "pet_name" else known_pet
        value = value.text() if isinstance(value, SaidName) else value
        decision = decide(view, number, known_name, known_pet)
        steps.append((kind, value, decision))
        if decision.decision != "ask":
            break
    return steps


OUTCOMES = {"resolved": "resolved", "not_found": "not_a_client", "ask": "unresolved"}
