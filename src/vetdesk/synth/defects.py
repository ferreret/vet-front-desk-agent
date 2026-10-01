"""Degrade the clean world into what a decades-old practice-management database holds.

Each degradation is recorded as a label on the affected record, so tests and the evaluation
harness know exactly which trap a record contains.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from . import names
from .text import strip_accents
from .world import REFERENCE_DATE, Client, Pet, PhoneOnFile, World, _weighted, file_key

NAME_STYLES = ["canonical", "upper_no_accents", "no_accents", "single_surname", "natural_order",
               "typo"]
NAME_STYLE_WEIGHTS = [0.55, 0.15, 0.10, 0.08, 0.07, 0.05]

# Household-homonym pairs from this index on are stored with a single surname, which makes
# parent and child indistinguishable on file.
SINGLE_SURNAME_FROM_PAIR = 3

FIRST_SIGNUP = date(2004, 3, 1)


def _typo(text: str, rng: random.Random) -> str:
    """Swap two neighbouring letters or drop one, somewhere inside a word."""
    spots = [i for i in range(1, len(text) - 2) if text[i].isalpha() and text[i + 1].isalpha()]
    for _ in range(20):
        i = rng.choice(spots)
        if rng.random() < 0.5:
            changed = text[:i] + text[i + 1] + text[i] + text[i + 2:]
        else:
            changed = text[:i] + text[i + 1:]
        if changed != text:
            return changed
    return text


def render_name(given: str, surname1: str, surname2: str, style: str, rng: random.Random) -> str:
    if style == "single_surname":
        return f"{surname1}, {given}"
    if style == "natural_order":
        return f"{given} {surname1} {surname2}"
    base = f"{surname1} {surname2}, {given}"
    if style == "upper_no_accents":
        return strip_accents(base).upper()
    if style == "no_accents":
        return strip_accents(base)
    if style == "typo":
        return _typo(base, rng)
    return base


def _degrade_names(world: World, rng: random.Random) -> None:
    cfg = world.config
    for client in world.clients.values():
        given = client.given
        if client.planted:
            kind, pair = client.planted
            single = kind == "household_homonym" and pair >= SINGLE_SURNAME_FROM_PAIR
            style = "single_surname" if single else "canonical"
        else:
            style = rng.choices(NAME_STYLES, NAME_STYLE_WEIGHTS)[0]
            castilian = names.CA_TO_ES_GIVEN.get(given)
            if castilian and rng.random() < cfg.castilianized_share:
                given = castilian
                client.defects.append("name_castilianized")
        client.legacy_name = render_name(given, client.surname1, client.surname2, style, rng)
        client.surname2_on_file = style != "single_surname"
        if style != "canonical":
            client.defects.append(f"name_{style}")


def _format_phone(number: str, rng: random.Random) -> str:
    n = number[3:]  # national part
    return rng.choice([
        n,
        n,
        f"{n[:3]} {n[3:5]} {n[5:7]} {n[7:]}",
        f"{n[:3]} {n[3:6]} {n[6:]}",
        f"{n[:3]}-{n[3:]}",
        f"{n[:3]}.{n[3:5]}.{n[5:7]}.{n[7:]}",
        f"+34 {n}",
    ])


def _degrade_phones(world: World, rng: random.Random) -> None:
    cfg = world.config
    for client in world.clients.values():
        household = world.households[client.household_id]
        first = world.members(client.household_id)[0]
        if not client.planted and rng.random() < cfg.missing_phone_share:
            client.defects.append("phone_missing")
            continue
        phones: list[PhoneOnFile] = []
        if household.landline:
            phones.append(PhoneOnFile(household.landline, "Telefono", "current"))
        if client.mobile:
            column = "Movil" if household.landline else "Telefono"
            phones.append(PhoneOnFile(client.mobile, column, "current"))
        # The first member's mobile often ends up on the other member's record too.
        if client is not first and first.mobile:
            if not household.landline or rng.random() < 0.3:
                phones.append(PhoneOnFile(first.mobile, "Telefono2", "current"))

        if not client.planted and client.mobile and rng.random() < cfg.stale_phone_share:
            old = world.new_number(rng, "mobile")
            entry = next(p for p in phones if p.number == client.mobile)
            entry.number, entry.status = old, "stale"
            world.stale_holders[old] = "stranger" if rng.random() < 0.65 else "none"
            client.defects.append("phone_stale")
            has_second = any(p.column == "Telefono2" for p in phones)
            if not has_second and rng.random() < 0.5:
                phones.append(PhoneOnFile(client.mobile, "Telefono2", "current"))
            else:
                client.defects.append("phone_current_not_on_file")

        for phone in phones:
            phone.raw = _format_phone(phone.number, rng)
        if rng.random() < 0.06:
            rng.choice(phones).raw += f" ({rng.choice(names.PHONE_NOTES)})"
            client.defects.append("phone_format_messy")
        columns = {p.column for p in phones}
        if {"Telefono", "Movil"} <= columns and rng.random() < 0.05:
            for phone in phones:
                if phone.column == "Movil":
                    phone.column = "Telefono"
            client.defects.append("phone_two_in_one_field")
        client.phones_on_file = phones

    for ids in world.phone_index().values():
        if len(ids) > 1:
            for client_id in ids:
                defects = world.clients[client_id].defects
                if "phone_shared" not in defects:
                    defects.append("phone_shared")


def _degrade_animals(world: World, rng: random.Random) -> None:
    cfg = world.config
    by_key: dict[tuple, list[str]] = {}
    for client in world.clients.values():
        by_key.setdefault(file_key(client), []).append(client.client_id)
    for ids in by_key.values():
        if len(ids) > 1:
            for client_id in ids:
                world.clients[client_id].defects.append("homonym")

    for pet in world.pets.values():
        owner = world.clients[pet.owner_id]
        pet.legacy_owner_name = owner.legacy_name
        pet.linked_client_ids = sorted(by_key[file_key(owner)])
        if len(pet.linked_client_ids) > 1:
            pet.defects.append("link_ambiguous")
        elif rng.random() < cfg.link_drift_share:
            # The client's name was edited later; the animal still carries the old spelling.
            name = owner.legacy_name
            variants = [strip_accents(name), strip_accents(name).upper(), _typo(name, rng)]
            variants = [v for v in variants if v != name]
            if variants:
                pet.legacy_owner_name = rng.choice(variants)
                pet.defects.append("link_drift")
        spellings = names.SPECIES[pet.species][2]
        pet.legacy_species = spellings[0] if rng.random() < 0.8 else rng.choice(spellings)
        if pet.legacy_species != spellings[0]:
            pet.defects.append("species_inconsistent")
        if pet.deceased:
            pet.defects.append("deceased")


def _add_orphans(world: World, rng: random.Random) -> None:
    """Animals whose owner name matches nobody: the client record was deleted or retyped."""
    on_file = {file_key(c)[:2] for c in world.clients.values()}
    serial = len(world.pets)
    species_names = list(names.SPECIES)
    for _ in range(world.config.orphan_animals):
        while True:
            language = rng.choice(["es", "ca"])
            given = _weighted(rng, names.GIVEN[language][rng.choice("FM")])
            surname1 = _weighted(rng, names.SURNAMES[language])
            surname2 = _weighted(rng, names.SURNAMES[language])
            ghost = Client("", given, surname1, surname2, "F", language, "", None)
            if file_key(ghost)[:2] not in on_file:
                break
        serial += 1
        species = rng.choice(species_names[:2])
        pet = Pet(
            pet_id=f"A-{serial:04d}",
            name=_weighted(rng, names.PET_NAMES),
            species=species,
            breed=rng.choice(names.SPECIES[species][1]),
            sex=rng.choice("MH"),
            born=REFERENCE_DATE - timedelta(days=rng.randint(400, 14 * 365)),
            coat=rng.choice(names.COATS),
            chip=None,
            deceased=False,
            owner_id=None,
            legacy_owner_name=f"{surname1} {surname2}, {given}",
            legacy_species=species,
            defects=["orphan"],
        )
        world.pets[pet.pet_id] = pet


def _degrade_nani(world: World, rng: random.Random) -> None:
    for client in world.clients.values():
        real = len(client.pet_ids)
        client.nani_on_file = real
        if real == 0:
            client.defects.append("nani_zero")
        if not client.planted and rng.random() < world.config.nani_stale_share:
            client.nani_on_file = real + 1 if real == 0 else real + rng.choice([-1, 1])
            client.defects.append("nani_stale")


def _assign_codes(world: World, rng: random.Random) -> None:
    """Legacy codes follow sign-up order, with gaps left by deleted records."""
    clients = list(world.clients.values())
    rng.shuffle(clients)
    span = (REFERENCE_DATE - FIRST_SIGNUP).days
    code = 0
    for position, client in enumerate(clients):
        code += 1 + (rng.randint(1, 3) if rng.random() < 0.1 else 0)
        client.legacy_codigo = code
        client.signup = FIRST_SIGNUP + timedelta(days=span * position // len(clients))
        if rng.random() < 0.08:
            client.notes = rng.choice(names.CLIENT_NOTES)
    pets = list(world.pets.values())
    rng.shuffle(pets)
    code = 0
    for pet in pets:
        code += 1 + (rng.randint(1, 4) if rng.random() < 0.1 else 0)
        pet.legacy_codigo = code


def apply_defects(world: World) -> None:
    rng = random.Random(f"{world.config.seed}/defects")
    _degrade_names(world, rng)
    _degrade_phones(world, rng)
    _degrade_animals(world, rng)
    _add_orphans(world, rng)
    _degrade_nani(world, rng)
    _assign_codes(world, rng)
