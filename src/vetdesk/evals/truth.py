"""Ground truth lookups for the harness: who is who, in both numbering systems.

Scenarios speak in truth ids (`C-0042`, `A-0101`); the agent side only ever sees the legacy
codes. Only the harness may hold this mapping.
"""

from __future__ import annotations


class Truth:
    def __init__(self, truth: dict) -> None:
        self._clients = {c["client_id"]: c for c in truth["clients"]}
        self._pets = {p["pet_id"]: p for p in truth["pets"]}
        self._client_ids = {c["legacy_codigo"]: c["client_id"] for c in truth["clients"]}

    def client_code(self, client_id: str) -> int:
        return self._clients[client_id]["legacy_codigo"]

    def client_id(self, code: int | None) -> str | None:
        return self._client_ids.get(code) if code is not None else None

    def pet_code(self, pet_id: str) -> int:
        return self._pets[pet_id]["legacy_codigo"]

    def pet_name(self, pet_id: str) -> str:
        return self._pets[pet_id]["name"]

    def species(self, pet_id: str) -> str:
        return self._pets[pet_id]["species"].lower()

    def full_name(self, client_id: str) -> str:
        c = self._clients[client_id]
        return " ".join(part for part in (c["given_name"], c["surname1"], c["surname2"]) if part)

    def phone(self, client_id: str) -> str | None:
        """A number the client really answers, whether or not the clinic has it."""
        phones = self._clients[client_id]["current_phones"]
        return phones[0] if phones else None

    def numbers_on_file(self) -> set[str]:
        return {p["number"] for c in self._clients.values() for p in c["phones_on_file"]}

    def pet_names(self, client_id: str) -> list[str]:
        """Names of the animals that really belong to a client."""
        return [p["name"] for p in self._pets.values() if p["owner_client_id"] == client_id]
