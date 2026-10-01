"""The identity policy, on a hand-made file small enough to check by eye."""

from datetime import date

import pytest

from vetdesk.synth.oracle import FileView, SaidName, decide, trace
from vetdesk.synth.world import Client, GeneratorConfig, Pet, PhoneOnFile, World

LANDLINE = "+34971000001"
MARGA_MOBILE = "+34600000001"
ANTONIO_MOBILE = "+34600000002"
OLD_NUMBER = "+34600000009"
FAMILY_NUMBER = "+34971000002"


def _client(world, client_id, given, surname1, surname2, phones=(), surname2_on_file=True):
    client = Client(client_id, given, surname1, surname2, "F", "ca", "H-1", None)
    client.surname2_on_file = surname2_on_file
    client.phones_on_file = [PhoneOnFile(n, "Telefono", "current") for n in phones]
    world.clients[client_id] = client


def _pet(world, pet_id, name, linked):
    pet = Pet(pet_id, name, "Perro", "Mestizo", "H", date(2020, 1, 1), "Negro", None, False,
              linked[0])
    pet.linked_client_ids = list(linked)
    world.pets[pet_id] = pet


@pytest.fixture(scope="module")
def view():
    world = World(config=GeneratorConfig())
    # A couple sharing a landline.
    _client(world, "MARGA", "Margalida", "Ferrer", "Oliver", [LANDLINE, MARGA_MOBILE])
    _client(world, "TONI", "Antoni", "Serra", "Vidal", [LANDLINE])
    _pet(world, "P1", "Xispa", ["MARGA"])
    _pet(world, "P2", "Luna", ["TONI"])
    # Two full homonyms: the file links their animals by name, so to both of them.
    _client(world, "ANT1", "Antonio", "García", "Martínez", [ANTONIO_MOBILE])
    _client(world, "ANT2", "Antonio", "García", "Martínez")
    _pet(world, "P3", "Rocky", ["ANT1", "ANT2"])
    _pet(world, "P4", "Luna", ["ANT1", "ANT2"])
    # Stored with one surname, a stale number on file, and a pet called Luna.
    _client(world, "PERE", "Pere", "Mas", "Coll", [OLD_NUMBER], surname2_on_file=False)
    _pet(world, "P5", "Luna", ["PERE"])
    # A family number shared by two people stored with a single surname.
    _client(world, "BIEL", "Biel", "Roca", "Pons", [FAMILY_NUMBER], surname2_on_file=False)
    _client(world, "MARC", "Marc", "Roca", "Mir", [FAMILY_NUMBER], surname2_on_file=False)
    return FileView(world)


MARGA = SaidName("Margalida", "Ferrer", "Oliver")
ANTONIO = SaidName("Antonio", "García", "Martínez")


def test_a_phone_alone_never_confirms_anybody(view):
    assert decide(view, MARGA_MOBILE, None, None).decision == "ask"
    shared = decide(view, LANDLINE, None, None)
    assert shared.decision == "ask" and shared.consistent_with == ("MARGA", "TONI")


def test_name_plus_phone_confirms(view):
    result = decide(view, LANDLINE, MARGA, None)
    assert (result.decision, result.client_id) == ("resolved", "MARGA")


def test_name_alone_is_only_probable(view):
    result = decide(view, None, MARGA, None)
    assert (result.decision, result.level) == ("ask", "probable")


def test_name_plus_pet_confirms_without_caller_id(view):
    assert decide(view, None, MARGA, "Xispa").client_id == "MARGA"


def test_a_pet_that_is_not_on_file_does_not_confirm(view):
    assert decide(view, None, MARGA, "Puppy").decision == "ask"


def test_catalan_and_castilian_forms_of_a_name_match(view):
    said = SaidName("Margarita", "Ferrer", "Oliver")
    assert decide(view, None, said, "Xispa").client_id == "MARGA"


def test_homonyms_cannot_be_told_apart_by_a_pet_linked_by_name(view):
    result = decide(view, None, ANTONIO, "Rocky")
    assert result.decision == "ask" and result.consistent_with == ("ANT1", "ANT2")


def test_the_phone_tells_homonyms_apart(view):
    assert decide(view, ANTONIO_MOBILE, ANTONIO, None).client_id == "ANT1"


def test_borrowed_phone_does_not_override_name_and_pet(view):
    result = decide(view, ANTONIO_MOBILE, MARGA, "Xispa")
    assert (result.decision, result.client_id) == ("resolved", "MARGA")


def test_inherited_number_with_a_coinciding_pet_name_is_not_a_match(view):
    """A stranger holds Pere's old number and also has a dog called Luna."""
    stranger = SaidName("Lucía", "Romero", "Gil")
    result = decide(view, OLD_NUMBER, stranger, "Luna")
    assert (result.decision, result.client_id) == ("not_found", None)


def test_second_surname_cannot_rule_out_a_record_that_lacks_it(view):
    lookalike = SaidName("Pere", "Mas", "Riera")
    assert decide(view, None, lookalike, None).consistent_with == ("PERE",)
    stranger = SaidName("Margalida", "Ferrer", "Riera")
    assert decide(view, None, stranger, None).decision == "not_found"


def test_one_surname_is_never_enough_when_the_record_has_two(view):
    partial = SaidName("Margalida", "Ferrer")
    assert decide(view, MARGA_MOBILE, partial, None).decision == "ask"
    assert decide(view, None, partial, "Xispa").decision == "ask"
    assert decide(view, MARGA_MOBILE, MARGA, None).decision == "resolved"


def test_record_with_a_single_surname_is_confirmed_by_its_own_phone_only(view):
    pere = SaidName("Pere", "Mas", "Coll")
    assert decide(view, OLD_NUMBER, pere, None).client_id == "PERE"
    assert decide(view, None, pere, "Luna").decision == "ask"


def test_family_number_cannot_confirm_half_a_name(view):
    """Two Rocas stored with one surname share a number: a third Roca could be calling."""
    marc = SaidName("Marc", "Roca", "Mir")
    assert view.by_name(marc) == {"MARC"}
    assert decide(view, FAMILY_NUMBER, marc, None).decision == "ask"


def test_trace_asks_for_both_surnames_before_the_pet(view):
    steps = trace(view, None, SaidName("Margalida", "Ferrer"), "Xispa", MARGA)
    assert [(kind, value, d.decision) for kind, value, d in steps] == [
        ("client_name", "Margalida Ferrer", "ask"),
        ("client_name", "Margalida Ferrer Oliver", "ask"),
        ("pet_name", "Xispa", "resolved"),
    ]
