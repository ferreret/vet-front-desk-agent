"""The adapter: reading the legacy database and making sense of what was typed into it."""

import sqlite3
from collections import Counter

import pytest

from vetdesk.dbguard import ForeignDatabaseError
from vetdesk.legacy import LegacySqliteSource
from vetdesk.legacy.normalize import (
    name_key,
    normalize_species,
    osa_distance,
    parse_name,
    parse_phones,
)


@pytest.mark.parametrize("raw,expected", [
    ("971551234", ["+34971551234"]),
    ("971 55 12 34", ["+34971551234"]),
    ("971-551234", ["+34971551234"]),
    ("971.55.12.34", ["+34971551234"]),
    ("+34 600123456", ["+34600123456"]),
    ("0034600123456", ["+34600123456"]),
    ("600 12 34 56 (hija)", ["+34600123456"]),
    ("971551234 / 600123456", ["+34971551234", "+34600123456"]),
    ("", []),
    (None, []),
])
def test_phone_fields_are_read_whatever_their_format(raw, expected):
    assert parse_phones(raw) == (expected, False)


def test_unreadable_phone_is_reported_not_guessed():
    assert parse_phones("55 12 34") == ([], True)
    assert parse_phones("971551234 / ext. 12") == (["+34971551234"], True)


@pytest.mark.parametrize("raw,given,surname1,surname2", [
    ("Ferrer Oliver, Margalida", "Margalida", "Ferrer", "Oliver"),
    ("FERRER OLIVER, MARGALIDA", "MARGALIDA", "FERRER", "OLIVER"),
    ("Ferrer, Margalida", "Margalida", "Ferrer", None),
    ("Margalida Ferrer Oliver", "Margalida", "Ferrer", "Oliver"),
    ("  Ferrer   Oliver ,  Margalida ", "Margalida", "Ferrer", "Oliver"),
])
def test_names_are_read_in_every_stored_layout(raw, given, surname1, surname2):
    name = parse_name(raw)
    assert (name.given, name.surname1, name.surname2) == (given, surname1, surname2)


def test_unreadable_names():
    assert parse_name("") is None and parse_name("Margalida") is None


def test_one_person_one_key_however_the_name_was_typed():
    keys = {
        name_key(raw) for raw in (
            "Vega Ferrer, Joan", "VEGA FERRER, JOAN", "Joan Vega Ferrer", "Vega Ferrer, Juan",
        )
    }
    assert len(keys) == 1
    assert name_key("Vega, Joan") not in keys
    assert name_key("Vega Ferrer, Joana") not in keys


def test_edit_distance():
    assert osa_distance("esteve", "esteve") == 0
    assert osa_distance("esteve", "etseve") == 1  # two letters swapped
    assert osa_distance("ferrer", "ferer") == 1
    assert osa_distance("ramos", "vidal") > 2


def test_species_spellings_are_unified():
    assert {normalize_species(s) for s in ("Perro", "PERRO", "Canino", "Can")} == {"perro"}
    assert normalize_species("Felino") == "gato"
    assert normalize_species("Iguana") == "iguana"


# --- against the generated clinic --------------------------------------------------------------


def test_every_phone_on_file_is_recovered(world, clinic):
    for client in world.clients.values():
        on_file = sorted({p.number for p in client.phones_on_file})
        assert sorted(clinic.clients[client.legacy_codigo].phones) == on_file


def test_every_animal_reaches_exactly_the_clients_the_truth_says(world, clinic, client_ids):
    for pet in world.pets.values():
        animal = clinic.animals[pet.legacy_codigo]
        assert sorted(client_ids[code] for code in animal.owner_codes) == pet.linked_client_ids
    links = Counter(a.link for a in clinic.animals.values())
    assert links["none"] == world.config.orphan_animals
    assert links["fuzzy"] > 0


def test_names_are_parsed(world, clinic):
    for client in world.clients.values():
        name = clinic.clients[client.legacy_codigo].name
        assert name is not None
        assert (name.surname2 is not None) == client.surname2_on_file


def test_the_adapter_reports_what_it_had_to_work_around(clinic):
    kinds = Counter(issue.kind for issue in clinic.issues)
    for kind in ("phone_shared", "owner_ambiguous", "owner_fuzzy", "owner_not_found",
                 "name_duplicate", "animal_count_mismatch", "no_phone"):
        assert kinds[kind] > 0, kind


def test_species_are_normalized(clinic):
    assert {a.species for a in clinic.animals.values()} <= {
        "perro", "gato", "conejo", "ave", "hurón", "cobaya", "tortuga",
    }


def test_a_database_the_project_did_not_generate_is_never_opened(tmp_path):
    foreign = tmp_path / "clinic.db"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE Clientes (Codigo INTEGER, Nombre TEXT)")
    with pytest.raises(ForeignDatabaseError):
        LegacySqliteSource(foreign).load()
    with pytest.raises(FileNotFoundError):
        LegacySqliteSource(tmp_path / "missing.db").load()
