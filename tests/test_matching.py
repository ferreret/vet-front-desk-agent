"""How names are compared: what sounds the same, what merely resembles, what is different."""

import pytest

from vetdesk.identity.matching import (
    EXACT,
    NO_MATCH,
    SIMILAR,
    SOUNDS_SAME,
    TYPO,
    heard_grade,
    name_grade,
    parse_spoken_name,
    spelled_grade,
)
from vetdesk.identity.phonetics import phonetic_key
from vetdesk.legacy.normalize import parse_name


@pytest.mark.parametrize("on_file,heard", [
    ("Vázquez", "Bázquez"), ("Ginard", "Jinard"), ("Rocky", "Roki"), ("Joan", "Yoan"),
    ("Vich", "Vic"), ("Xisca", "Chisca"), ("Llorenç", "Llorens"), ("Vicenç", "Bisens"),
    ("Guillem", "Guiyem"), ("Garrido", "Garido"), ("Moll", "Moy"), ("Max", "Macs"),
    ("Thor", "Tor"), ("Muñoz", "Muños"), ("Kira", "Quira"), ("Coco", "Koko"),
])
def test_spellings_of_the_same_sound(on_file, heard):
    assert phonetic_key(on_file) == phonetic_key(heard)
    assert heard_grade(heard.lower(), on_file.lower()) == SOUNDS_SAME


@pytest.mark.parametrize("on_file,heard", [
    ("Puig", "Puch"), ("Bosch", "Bosque"), ("Francesc", "Fransés"), ("Lluna", "Luna"),
    ("Ferrer", "Ferré"), ("Pons", "Pon"), ("Ramos", "Ramis"), ("Etseve", "Esteve"),
])
def test_near_misses_are_only_similar(on_file, heard):
    assert heard_grade(heard.lower(), on_file.lower()) == SIMILAR


@pytest.mark.parametrize("on_file,heard", [("Mas", "Mir"), ("Ferrer", "Vidal"), ("Luna", "Coco")])
def test_different_words_do_not_match(on_file, heard):
    assert heard_grade(heard.lower(), on_file.lower()) == NO_MATCH


def test_given_names_match_across_catalan_and_castilian():
    assert heard_grade("margarita", "margalida", True) == EXACT
    assert heard_grade("lorenzo", "llorenç", True) == EXACT
    assert heard_grade("yusep", "josé", True) == SIMILAR  # misheard Josep, record says José
    assert heard_grade("margarita", "margalida", False) < EXACT  # not for surnames


def test_given_names_one_letter_apart_are_different_people():
    assert heard_grade("juana", "juan", True) == SIMILAR
    assert spelled_grade("juana", "juan", True) == NO_MATCH
    assert spelled_grade("marta", "maría", True) == NO_MATCH


def test_spelled_surnames_allow_one_typing_mistake_on_file():
    assert spelled_grade("esteve", "etseve") == TYPO
    assert spelled_grade("sala", "ala") == TYPO
    assert spelled_grade("vila", "bila") == TYPO
    assert spelled_grade("mas", "mar") == NO_MATCH  # too short to call it a typo
    assert spelled_grade("ferrer", "ferrer") == EXACT


def _grade(said, on_file, verified=False):
    return name_grade(parse_spoken_name(said), parse_name(on_file), verified)


def test_whole_names():
    assert _grade("Margalida Ferrer Oliver", "Ferrer Oliver, Margalida") == EXACT
    assert _grade("Margalida Ferrer Oliver", "FERRER OLIVER, MARGARITA") == EXACT
    assert _grade("Margalida Ferrer Oliver", "Margalida Ferrer Oliver") == EXACT
    assert _grade("Margalida Ferré Oliver", "Ferrer Oliver, Margalida") == SIMILAR
    assert _grade("Margalida Ferrer Vidal", "Ferrer Oliver, Margalida") == NO_MATCH
    assert _grade("Joana Ferrer Oliver", "Ferrer Oliver, Margalida") == NO_MATCH


def test_a_second_surname_only_counts_when_both_sides_have_one():
    assert _grade("Margalida Ferrer", "Ferrer Oliver, Margalida") == EXACT
    assert _grade("Margalida Ferrer Vidal", "Ferrer, Margalida") == EXACT


def test_spelled_names():
    assert _grade("David Esteve Canals", "Etseve Canals, David", verified=True) == TYPO
    assert _grade("David Esteve Canals", "Etseve Canlas, David", verified=True) == NO_MATCH
    assert _grade("Davis Esteve Canals", "Esteve Canals, David", verified=True) == NO_MATCH


def test_spoken_names_keep_accents_for_their_sound():
    name = parse_spoken_name("Vicenç  Bauzà Llull")
    assert (name.given, name.surname1, name.surname2) == ("vicenç", "bauzà", "llull")
    assert parse_spoken_name("Vicenç") is None
