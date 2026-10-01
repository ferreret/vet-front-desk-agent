"""The identity resolver on a hand-made clinic small enough to check by eye.

Every test is a call: what the agent knows, and what the resolver must conclude.
"""

from dataclasses import replace

import pytest

from vetdesk.identity import Evidence, IdentityResolver
from vetdesk.legacy.models import Animal, Client, Clinic
from vetdesk.legacy.normalize import parse_name

LANDLINE = "+34971000001"
MARGA_MOBILE = "+34600000001"
ANTONIO_MOBILE = "+34600000002"
PERE_NUMBER = "+34600000003"
ROCA_FAMILY = "+34971000002"
DAVID_MOBILE = "+34600000004"
JOAN_MOBILE = "+34600000005"
PONS_LANDLINE = "+34971000003"
TOWN = "Vallserena"
TOWNS = {12: "Port Blau", 3: "Pinar del Mar"}

CLIENTS = [
    (1, "Ferrer Oliver, Margalida", [LANDLINE, MARGA_MOBILE]),
    (2, "Serra Vidal, Antoni", [LANDLINE]),
    (3, "García Martínez, Antonio", [ANTONIO_MOBILE]),
    (4, "García Martínez, Antonio", []),
    (5, "Mas, Pere", [PERE_NUMBER]),  # stored with a single surname
    (6, "Roca, Biel", [ROCA_FAMILY]),
    (7, "Roca, Marc", [ROCA_FAMILY]),
    (8, "Etseve Canals, David", [DAVID_MOBILE]),  # typo on file: Esteve
    (9, "Lozano Ferrer, Joan", []),
    (10, "Lozano Font, Joan", [JOAN_MOBILE]),
    (11, "Pons Riera, María", [PONS_LANDLINE]),
    (12, "VICH SOCIAS, FRANCESC", []),
]
ANIMALS = [
    (1, "Xispa", [1]),
    (2, "Luna", [2]),
    (3, "Rocky", [3, 4]),  # the owner name matches both Antonios
    (4, "Luna", [5]),
    (5, "Kira", [8]),
    (6, "Lluna", [12]),
    (7, "Trufa", [10]),
]


@pytest.fixture(scope="module")
def resolver():
    clients = {
        code: Client(code, raw, parse_name(raw), tuple(phones), None, TOWNS.get(code, TOWN))
        for code, raw, phones in CLIENTS
    }
    animals = {
        code: Animal(code, name, "perro", None, False, "", tuple(owners), "exact")
        for code, name, owners in ANIMALS
    }
    return IdentityResolver(Clinic(clients, animals))


def _code(resolution):
    return resolution.client.code if resolution.client else None


def _codes(resolution):
    return [c.client.code for c in resolution.candidates]


# --- the basic policy --------------------------------------------------------------------


def test_a_phone_alone_never_confirms(resolver):
    r = resolver.resolve(Evidence(MARGA_MOBILE))
    assert (r.decision, r.level, r.ask_for) == ("ask", "probable", "client_name")
    assert _codes(r) == [1]


def test_nothing_known_yet(resolver):
    r = resolver.resolve(Evidence())
    assert (r.decision, r.level, r.ask_for) == ("ask", "none", "client_name")


def test_name_plus_phone_confirms(resolver):
    r = resolver.resolve(Evidence(MARGA_MOBILE, "Margalida Ferrer Oliver"))
    assert (r.decision, r.level, _code(r)) == ("resolved", "confirmed", 1)
    assert "calling number is on this record" in r.why


def test_name_alone_asks_for_the_pet(resolver):
    r = resolver.resolve(Evidence(None, "Margalida Ferrer Oliver"))
    assert (r.decision, r.ask_for) == ("ask", "pet_name")


def test_full_name_and_pet_need_the_town_without_caller_id(resolver):
    evidence = Evidence(None, "Margalida Ferrer Oliver", pet_name="Xispa")
    r = resolver.resolve(evidence)
    assert (r.decision, r.ask_for) == ("ask", "town")
    assert _code(resolver.resolve(replace(evidence, town="Vallserena"))) == 1
    r = resolver.resolve(replace(evidence, town="Port Blau"))
    assert (r.decision, r.ask_for) == ("ask", None) and "town" in r.why


def test_a_badly_heard_town_is_still_recognised(resolver):
    evidence = Evidence(None, "Margalida Ferrer Oliver", pet_name="Xispa")
    for heard in ("Ballserena", "Vall Serena", "Balserena"):
        assert _code(resolver.resolve(replace(evidence, town=heard))) == 1, heard
    for heard in ("Madrid", "Port", "Pinar"):
        assert resolver.resolve(replace(evidence, town=heard)).decision == "ask", heard


def test_shared_landline_is_settled_by_the_name(resolver):
    assert _codes(resolver.resolve(Evidence(LANDLINE))) == [1, 2]
    assert _code(resolver.resolve(Evidence(LANDLINE, "Antoni Serra Vidal"))) == 2


def test_a_first_name_is_not_a_name(resolver):
    r = resolver.resolve(Evidence(None, "Margalida"))
    assert (r.decision, r.ask_for) == ("ask", "client_name")


# --- what speech recognition does to names -----------------------------------------------------


def test_sound_alike_spellings_match(resolver):
    assert _code(resolver.resolve(Evidence(LANDLINE, "Antoni Serra Bidal"))) == 2
    heard = Evidence(None, "Margalida Ferrer Oliver", pet_name="Chispa", town=TOWN)
    assert _code(resolver.resolve(heard)) == 1


def test_castilian_form_of_a_catalan_name_matches(resolver):
    assert _code(resolver.resolve(Evidence(MARGA_MOBILE, "Margarita Ferrer Oliver"))) == 1


def test_a_name_that_only_resembles_a_record_must_be_confirmed(resolver):
    r = resolver.resolve(Evidence(MARGA_MOBILE, "Margalida Ferré Oliver"))
    assert (r.decision, r.ask_for) == ("ask", "confirm_name")
    spelled = Evidence(MARGA_MOBILE, "Margalida Ferrer Oliver", name_verified=True)
    assert _code(resolver.resolve(spelled)) == 1


def test_a_pet_name_that_only_resembles_one_on_file_must_be_confirmed(resolver):
    heard = Evidence(None, "Francesc Vich Socias", pet_name="Luna")
    r = resolver.resolve(heard)
    assert (r.decision, r.ask_for) == ("ask", "confirm_pet")
    confirmed = Evidence(None, "Francesc Vich Socias", pet_name="Lluna", pet_verified=True,
                         town="Port Blau")
    assert _code(resolver.resolve(confirmed)) == 12
    denied = Evidence(None, "Francesc Vich Socias", pet_name="Luna", pet_verified=True)
    assert resolver.resolve(denied).decision == "ask"


def test_typing_mistake_in_a_surname_on_file(resolver):
    r = resolver.resolve(Evidence(DAVID_MOBILE, "David Esteve Canals"))
    assert (r.decision, r.ask_for) == ("ask", "confirm_name")
    spelled = Evidence(DAVID_MOBILE, "David Esteve Canals", name_verified=True)
    r = resolver.resolve(spelled)
    assert _code(r) == 8 and "one typo away" in r.why


# --- the traps ---------------------------------------------------------------------------------


def test_homonyms_without_caller_id_stay_unconfirmed(resolver):
    r = resolver.resolve(Evidence(None, "Antonio García Martínez", pet_name="Rocky"))
    assert (r.decision, r.ask_for) == ("ask", None)
    assert _codes(r) == [3, 4]


def test_the_phone_tells_homonyms_apart(resolver):
    assert _code(resolver.resolve(Evidence(ANTONIO_MOBILE, "Antonio García Martínez"))) == 3


def test_borrowed_phone_does_not_override_name_and_pet(resolver):
    evidence = Evidence(ANTONIO_MOBILE, "Margalida Ferrer Oliver", pet_name="Xispa", town=TOWN)
    assert _code(resolver.resolve(evidence)) == 1


def test_inherited_number_with_a_coinciding_pet_name(resolver):
    """A stranger holds Pere's old number and also has a dog called Luna."""
    r = resolver.resolve(Evidence(PERE_NUMBER, "Lucía Romero Gil", pet_name="Luna"))
    assert (r.decision, r.level, _code(r)) == ("not_found", "none", None)


def test_one_surname_is_never_enough_when_the_record_has_two(resolver):
    r = resolver.resolve(Evidence(MARGA_MOBILE, "Margalida Ferrer"))
    assert (r.decision, r.ask_for) == ("ask", "full_name")
    r = resolver.resolve(Evidence(None, "Margalida Ferrer", pet_name="Xispa"))
    assert (r.decision, r.ask_for) == ("ask", "full_name")


def test_relatives_with_the_same_name_borrowing_a_phone(resolver):
    """Joan Lozano Ferrer calls from the mobile on Joan Lozano Font's record, and speech
    recognition drops the second surname. The number must not decide between them."""
    r = resolver.resolve(Evidence(JOAN_MOBILE, "Yoan Losano"))
    assert (r.decision, r.ask_for) == ("ask", "full_name")
    spelled = Evidence(JOAN_MOBILE, "Joan Lozano Ferrer", name_verified=True)
    r = resolver.resolve(spelled)
    assert r.decision == "ask" and _codes(r) == [9]


def test_record_with_a_single_surname_is_confirmed_by_its_own_phone_only(resolver):
    assert _code(resolver.resolve(Evidence(PERE_NUMBER, "Pere Mas Coll"))) == 5
    r = resolver.resolve(Evidence(None, "Pere Mas Coll", pet_name="Luna"))
    assert (r.decision, r.ask_for) == ("ask", None)
    assert "single surname" in r.why


def test_family_number_cannot_confirm_half_a_name(resolver):
    """Two Rocas stored with one surname share a number: a third Roca could be calling."""
    r = resolver.resolve(Evidence(ROCA_FAMILY, "Marc Roca Mir", pet_name="Toby"))
    assert r.decision == "ask" and _codes(r) == [7]


def test_a_sister_is_not_her_sister(resolver):
    """Marta is not a client; María, same surnames, same landline, is."""
    r = resolver.resolve(Evidence(PONS_LANDLINE, "Marta Pons Riera"))
    assert (r.decision, r.ask_for) == ("ask", "confirm_name")
    spelled = Evidence(PONS_LANDLINE, "Marta Pons Riera", name_verified=True)
    assert resolver.resolve(spelled).decision == "not_found"


def test_every_decision_explains_itself(resolver):
    heard = Evidence(None, "Margalida Ferrer Oliver", pet_name="Chispa", town="Ballserena")
    r = resolver.resolve(heard)
    assert r.candidates[0].reasons(verified=False) == [
        "name exact: 'Ferrer Oliver, Margalida'",
        "pet sounds the same: 'Xispa'",
        "town matches: 'Vallserena'",
    ]
