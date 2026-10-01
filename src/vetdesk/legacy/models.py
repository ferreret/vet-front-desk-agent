"""The clean model the rest of the system works with.

Any practice-management system can sit behind `ClinicSource`: the legacy SQLite file is one
implementation; a web product with an API would be another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class PersonName:
    given: str
    surname1: str
    surname2: str | None = None

    def display(self) -> str:
        return " ".join(part for part in (self.given, self.surname1, self.surname2) if part)


@dataclass(frozen=True)
class Client:
    code: int  # the source system's own identifier
    raw_name: str
    name: PersonName | None  # None when the stored name could not be read
    phones: tuple[str, ...]  # E.164
    animals_declared: int | None  # the count the source system keeps on the client
    town: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class Animal:
    code: int
    name: str
    species: str
    breed: str | None
    deceased: bool
    owner_raw: str
    # Clients the owner name leads to. Several means the source cannot say whose it is.
    owner_codes: tuple[int, ...]
    link: Literal["exact", "fuzzy", "none"]


@dataclass(frozen=True)
class DataIssue:
    kind: str
    table: str
    code: int
    detail: str


@dataclass
class Clinic:
    clients: dict[int, Client]
    animals: dict[int, Animal]
    issues: list[DataIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_phone: dict[str, list[Client]] = {}
        for client in self.clients.values():
            for number in client.phones:
                self._by_phone.setdefault(number, []).append(client)
        self._by_owner: dict[int, list[Animal]] = {}
        for animal in self.animals.values():
            for code in animal.owner_codes:
                self._by_owner.setdefault(code, []).append(animal)

    def clients_by_phone(self, number: str | None) -> tuple[Client, ...]:
        return tuple(self._by_phone.get(number, ())) if number else ()

    def animals_of(self, client_code: int) -> tuple[Animal, ...]:
        """Animals whose owner name leads to this client, including shared-name ones."""
        return tuple(self._by_owner.get(client_code, ()))


class ClinicSource(Protocol):
    def load(self) -> Clinic: ...
