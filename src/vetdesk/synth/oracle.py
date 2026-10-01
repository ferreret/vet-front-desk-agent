"""The reference answer for identity resolution.

The oracle is a perfect listener holding a perfectly cleaned copy of what the legacy file
knows: it hears every name exactly as said and is never fooled by spelling. It is still
limited to what is on file, so it cannot tell two homonyms apart unless the data can.

Policy: a caller is confirmed when their name matches a client AND one more factor (a phone
on file, or a pet name) corroborates it, leaving exactly one candidate. A phone alone never
confirms anybody.
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
    for factor in (by_phone, view.by_pet(pet)):
        if candidates & factor:
            candidates &= factor
            corroborated = True
    if corroborated and len(candidates) == 1:
        (client_id,) = candidates
        return Decision("resolved", "confirmed", client_id, (client_id,))
    return Decision("ask", "probable", None, tuple(sorted(candidates)))
