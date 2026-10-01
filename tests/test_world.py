"""The synthetic clinic: reproducible, and carrying every defect the project is about."""

import re

import pytest

from vetdesk.synth import GeneratorConfig, generate_world
from vetdesk.synth.legacy_db import animal_rows, client_rows, defect_counts, export_truth
from vetdesk.synth.text import strip_accents

EXPECTED_DEFECTS = {
    "deceased", "homonym", "link_ambiguous", "link_drift", "name_castilianized",
    "name_natural_order", "name_no_accents", "name_single_surname", "name_typo",
    "name_upper_no_accents", "nani_stale", "nani_zero", "orphan", "phone_current_not_on_file",
    "phone_format_messy", "phone_missing", "phone_shared", "phone_stale",
    "phone_two_in_one_field", "species_inconsistent",
}


def test_same_seed_gives_the_same_clinic(world):
    again = generate_world(GeneratorConfig(seed=42))
    assert client_rows(again) == client_rows(world)
    assert animal_rows(again) == animal_rows(world)
    assert export_truth(again) == export_truth(world)


def test_another_seed_gives_another_clinic(world):
    other = generate_world(GeneratorConfig(seed=43))
    assert client_rows(other) != client_rows(world)


def test_requested_size(world):
    assert len(world.clients) == 400
    assert len(generate_world(GeneratorConfig(seed=1, n_clients=150)).clients) == 150


def test_too_small_a_clinic_is_rejected():
    with pytest.raises(ValueError):
        GeneratorConfig(n_clients=20)


def test_every_defect_is_present(world):
    assert set(defect_counts(world)) == EXPECTED_DEFECTS


def test_animals_point_at_their_owner_by_name(world):
    names_on_file = {c.legacy_name for c in world.clients.values()}
    for pet in world.pets.values():
        if "orphan" in pet.defects:
            assert pet.owner_id is None
            assert pet.legacy_owner_name not in names_on_file
        elif "link_drift" in pet.defects:
            assert pet.legacy_owner_name != world.clients[pet.owner_id].legacy_name
        else:
            assert pet.legacy_owner_name == world.clients[pet.owner_id].legacy_name


def test_planted_homonyms_are_indistinguishable_by_name(world):
    for first, second in world.planted["homonym"]:
        a, b = world.clients[first], world.clients[second]
        assert a.legacy_name == b.legacy_name
        assert a.household_id != b.household_id
        for pet_id in a.pet_ids + b.pet_ids:
            assert world.pets[pet_id].linked_client_ids == sorted([first, second])


def test_household_homonyms_share_a_landline(world):
    stored_identically = 0
    for first, second in world.planted["household_homonym"]:
        a, b = world.clients[first], world.clients[second]
        assert a.household_id == b.household_id
        assert (a.given, a.surname1) == (b.given, b.surname1) and a.surname2 != b.surname2
        landline = world.households[a.household_id].landline
        assert landline in {p.number for p in a.phones_on_file}
        assert landline in {p.number for p in b.phones_on_file}
        stored_identically += a.legacy_name == b.legacy_name
    assert 0 < stored_identically < len(world.planted["household_homonym"])


def test_shared_phones_stay_within_a_household(world):
    shared = {n: ids for n, ids in world.phone_index().items() if len(ids) > 1}
    assert shared
    for ids in shared.values():
        assert len({world.clients[i].household_id for i in ids}) == 1


def test_stale_numbers_are_on_file_but_no_longer_used(world):
    assert set(world.stale_holders.values()) == {"stranger", "none"}
    on_file = world.phone_index()
    for number in world.stale_holders:
        (client_id,) = on_file[number]
        assert number not in world.current_phones(world.clients[client_id])


def test_clients_without_animals(world):
    without = [c for c in world.clients.values() if not c.pet_ids]
    assert without
    assert any(c.nani_on_file == 0 for c in without)
    out_of_step = [c for c in world.clients.values() if c.nani_on_file != len(c.pet_ids)]
    assert out_of_step and all("nani_stale" in c.defects for c in out_of_step)


def test_both_languages_and_accent_variants(world):
    languages = {c.language for c in world.clients.values()}
    assert languages == {"es", "ca"}
    stored = [c.legacy_name for c in world.clients.values()]
    assert any(name != strip_accents(name) for name in stored)
    assert any(name.isupper() for name in stored)


def test_species_and_breeds_vary(world):
    assert len({p.species for p in world.pets.values()}) >= 5
    assert len({p.breed for p in world.pets.values()}) >= 20


def test_phones_on_file_keep_their_digits(world):
    for client in world.clients.values():
        for phone in client.phones_on_file:
            assert phone.number[3:] in re.sub(r"\D", "", phone.raw)
