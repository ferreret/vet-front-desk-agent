"""A large sweep of simulated calls, to give the false-identification count statistical weight.

Eighty hand-shaped scenarios show that each trap is handled. The sweep asks a blunter
question: across every client in the clinic, calling in several ways and at every level of
speech noise, plus many callers who are not clients at all, does the resolver ever confirm
the wrong person?
"""

from __future__ import annotations

import random

from ..synth import names
from ..synth.asr_noise import LEVELS, corrupt
from ..synth.oracle import OUTCOMES, FileView, SaidName, trace
from ..synth.scenarios import NOISE_ALLOWANCE
from ..synth.world import World, _person, _weighted
from .identity import Probe


def sweep_probes(world: World, strangers: int = 2000, seed: int = 0) -> list[Probe]:
    rng = random.Random(f"{world.config.seed}/sweep/{seed}")
    view = FileView(world)
    probes: list[Probe] = []

    def add(kind: str, caller_id: str | None, number: str | None, said: SaidName,
            pet: str | None, town: str, full: SaidName | None = None) -> None:
        steps = trace(view, number, said, pet, full, town)
        last = steps[-1][2]
        asked = sum(1 for step_kind, _, _ in steps if step_kind != "caller_number")
        for level in LEVELS:
            name_heard = corrupt(said.text(), level, rng)
            full_heard = corrupt(full.text(), level, rng) if full else None
            pet_heard = corrupt(pet, level, rng) if pet else None
            noise = level if (name_heard, pet_heard) != (said.text(), pet) else "none"
            probes.append(Probe(
                id=f"W-{len(probes) + 1:05d}",
                category=f"sweep.{kind}",
                caller_id=caller_id,
                number=number,
                name_said=said.text(),
                name_heard=name_heard,
                full_name_said=full.text() if full else None,
                full_name_heard=full_heard,
                pet_said=pet,
                pet_heard=pet_heard,
                town_heard=corrupt(town, level, rng),
                noise=noise,
                expected_outcome=OUTCOMES[last.decision],
                expected_client_id=last.client_id,
                max_questions=asked + NOISE_ALLOWANCE[noise],
            ))

    clients = list(world.clients.values())
    for client in clients:
        full = SaidName(client.given, client.surname1, client.surname2)
        pets = [world.pets[pet_id].name for pet_id in client.pet_ids]
        pet = rng.choice(pets) if pets else None
        town = world.households[client.household_id].town
        add("own_phone", client.client_id, world.current_phones(client)[0], full, pet, town)
        add("hidden_number", client.client_id, None, full, pet, town)
        one_surname = SaidName(client.given, client.surname1)
        add("one_surname", client.client_id, None, one_surname, pet, town, full)
        lender = rng.choice(clients)
        if lender.household_id != client.household_id:
            number = world.current_phones(lender)[0]
            add("borrowed_phone", client.client_id, number, full, pet, town)

    inherited = sorted(n for n, holder in world.stale_holders.items() if holder == "stranger")
    for _ in range(strangers):
        person = _person(rng, rng.choice(["es", "ca"]))
        said = SaidName(person.given, person.surname1, person.surname2)
        pet = _weighted(rng, names.PET_NAMES)
        number = rng.choice([None, None, rng.choice(inherited), "+34600000000"])
        add("not_a_client", None, number, said, pet, rng.choice(names.TOWNS)[0])
    return probes

